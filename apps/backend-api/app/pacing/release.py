"""The capacity controller (closed-loop release engine).

Each cycle, per state: compute how many leads to release so the day's
appointment slots fill (no over-messaging), pick the top-ranked held leads, and
either enqueue them (live) or just log the plan (dry-run).

Math (per state S):
    slots_open      = capacity.slots_open_today(S)
    rates           = funnel.rates(S)                  # reply, book, show
    target_bookings = slots_open / max(show, SHOW_FLOOR)   # over-book for no-shows
    leads_needed    = ceil(target_bookings / (reply*book) * (1 + WAVE_BUFFER))
    in_flight       = released-but-unresolved leads in S
    release_n       = max(0, leads_needed - in_flight)     # 0 if state is full / past cutoff
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core import engine_flags
from app.core.redis import redis_service
from app.models.lead import Lead
from app.pacing import capacity, funnel, scoring, events

logger = logging.getLogger(__name__)


def _now_local_hour() -> int:
    name = getattr(settings, "AGENT_TZ", None) or "America/New_York"
    if ZoneInfo is None:
        return datetime.utcnow().hour
    try:
        return datetime.now(ZoneInfo(name)).hour
    except Exception:
        return datetime.utcnow().hour


def _wave_id() -> str:
    # Deterministic per cycle-ish; second precision is enough to group a wave.
    return "w" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def in_flight(db: Session, tenant_id: str, state: Optional[str]) -> int:
    """Leads already released for this state and not yet resolved."""
    q = db.query(Lead).filter(
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
        Lead.pacing_status == "released",
    )
    if state:
        q = q.filter(Lead.state == state)
    return q.count()


def compute(db: Session, tenant_id: str, state: Optional[str]) -> Dict:
    """Compute the release plan for one state (no side effects)."""
    slots_open = capacity.slots_open_today(db, tenant_id, state)
    r = funnel.rates(db, tenant_id, state=state)
    show = max(float(r["show"]), float(getattr(settings, "PACING_SHOW_FLOOR", 0.5) or 0.5))
    eff = max(0.0001, float(r["effective_conv"]))
    buffer = float(getattr(settings, "PACING_WAVE_BUFFER", 0.10) or 0.0)

    target_bookings = slots_open / show if slots_open > 0 else 0.0
    leads_needed = math.ceil(target_bookings / eff * (1 + buffer)) if target_bookings > 0 else 0
    flight = in_flight(db, tenant_id, state)

    cap = capacity.capacity_today(db, tenant_id, state)
    target_util = getattr(settings, "TARGET_UTILIZATION", 1.0)
    target_util = 1.0 if target_util is None else float(target_util)
    full = cap["slots_total"] > 0 and (cap["fill_pct"] / 100.0 >= target_util)
    # NB: read the cutoff hour without `or` — 0 (midnight) is a valid hour and
    # must not be treated as "unset".
    cutoff_hour = getattr(settings, "OUTREACH_CUTOFF_HOUR", 16)
    cutoff_hour = 16 if cutoff_hour is None else int(cutoff_hour)
    past_cutoff = _now_local_hour() >= cutoff_hour
    no_capacity = cap["licensed_agents"] == 0

    if full or past_cutoff or no_capacity:
        release_n = 0
    else:
        release_n = max(0, leads_needed - flight)

    reason = None
    if no_capacity:
        reason = "no_licensed_agent"
    elif full:
        reason = "target_utilization_reached"
    elif past_cutoff:
        reason = "past_outreach_cutoff"

    return {
        "state": state,
        "slots_open": slots_open,
        "slots_total": cap["slots_total"],
        "booked": cap["booked"],
        "fill_pct": cap["fill_pct"],
        "licensed_agents": cap["licensed_agents"],
        "rates": {k: r[k] for k in ("reply", "book", "show", "effective_conv")},
        "target_bookings": round(target_bookings, 1),
        "leads_needed": leads_needed,
        "in_flight": flight,
        "release_n": release_n,
        "reason": reason,
        "full": bool(full),
        "outreach_stopped": release_n == 0,
        "target_utilization": target_util,
    }


def _enqueue_lead(lead: Lead, wave_id: str) -> None:
    job = {
        "lead_id": str(lead.id),
        "tenant_id": str(lead.tenant_id),
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
        "phone": lead.phone,
        "source": lead.source,
        "score": lead.lead_score,
        "wave_id": wave_id,
        # Carry the campaign so the send-once guard scopes per campaign (a number
        # gets the first template once per campaign; a new campaign re-texts).
        "campaign_id": str(lead.campaign_id) if lead.campaign_id else None,
        "kind": "first_template",   # the ONLY message allowed past the send chokepoint
    }
    try:
        redis_service.client.rpush("queue:outbound_sms", json.dumps(job))
    except Exception:
        redis_service.enqueue_sms(job)


def _release(db: Session, tenant_id: str, leads: List[Lead], wave_id: str, dry_run: bool) -> int:
    """Release the given held leads (or just count them in dry-run)."""
    n = 0
    now = datetime.now(timezone.utc)
    for lead in leads:
        if not dry_run:
            _enqueue_lead(lead, wave_id)
            lead.pacing_status = "released"
            lead.released_at = now
            lead.wave_id = wave_id
        n += 1
    if not dry_run:
        db.commit()
    return n


def run_cycle(db: Session, tenant_id: str, dry_run: Optional[bool] = None) -> Dict:
    """Run one controller cycle across all states with a held pool."""
    if not engine_flags.same_day_pacing_enabled():
        return {"enabled": False, "states": []}
    # Global kill-switch: when paused, hold everything — release nothing.
    try:
        from app.core.sending import is_sending_paused
        if is_sending_paused(tenant_id):
            return {"enabled": True, "paused": True, "states": []}
    except Exception:
        pass
    # Queue-Only Mode: the booking-driven capacity engine stands down — its math
    # assumes the AI books appointments, which it doesn't here. The flat
    # drip_cycle() handles held-lead release at the admin-set rate instead.
    try:
        from app.core.sending import is_autopilot_paused
        if is_autopilot_paused(tenant_id):
            return {"enabled": True, "autopilot_paused": True, "states": []}
    except Exception:
        pass
    if dry_run is None:
        dry_run = bool(getattr(settings, "PACING_DRY_RUN", True))

    wave_id = _wave_id()
    from app.pacing import waitlist
    held_by_state = events.states_with_leads(db, tenant_id, pacing_status="held")
    wait_by_state = events.states_with_leads(db, tenant_id, pacing_status="awaiting_slot")
    all_states = set(held_by_state) | set(wait_by_state)
    reports = []
    total_release = 0
    for state in all_states:
        st = state or None
        # 1) Work the waitlist FIRST — interested leads before any untouched lead.
        refilled = waitlist.process_waitlist(db, tenant_id, st, dry_run=dry_run)
        # 2) Release new held leads to cover the remaining gap to full.
        plan = compute(db, tenant_id, st)
        plan["held"] = held_by_state.get(state, 0)
        plan["waitlisted"] = wait_by_state.get(state, 0)
        plan["waitlist_refilled"] = refilled
        if plan["release_n"] > 0 and held_by_state.get(state, 0) > 0:
            leads = scoring.ranked_held(db, tenant_id, st, plan["release_n"])
            released = _release(db, tenant_id, leads, wave_id, dry_run)
            plan["released"] = released
            total_release += released
        else:
            plan["released"] = 0
        total_release += refilled
        reports.append(plan)
        logger.info(
            "[pacing%s] tenant=%s state=%s held=%s waitlisted=%s refilled=%s "
            "slots_open=%s in_flight=%s leads_needed=%s release=%s reason=%s",
            " DRY-RUN" if dry_run else "",
            tenant_id, st, plan["held"], plan["waitlisted"], plan["waitlist_refilled"],
            plan["slots_open"], plan["in_flight"], plan["leads_needed"],
            plan["released"], plan.get("reason"),
        )

    # Best-effort metrics (Phase 10 owns the dashboard; safe no-op if absent).
    try:
        from app.pacing import metrics
        metrics.write_cycle(db, tenant_id, reports, dry_run=dry_run, wave_id=wave_id)
    except Exception:
        pass

    return {
        "enabled": True,
        "dry_run": dry_run,
        "wave_id": wave_id,
        "total_release": total_release,
        "states": reports,
    }


def on_import_complete(db: Session, tenant_id: str, imported: int) -> Dict:
    """Kick off Wave 1 right after a CSV import (best-effort)."""
    logger.info("[pacing] import_complete tenant=%s imported=%s -> wave 1", tenant_id, imported)
    try:
        return run_cycle(db, tenant_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("on_import_complete cycle failed: %s", exc)
        return {"enabled": True, "error": str(exc)}


def _apply_capacity(db: Session, tenant_id: str, leads: list) -> tuple:
    """Capacity-sized pacing + compliance gate (P2 + P3), applied to an already
    time-paced batch right before release. When CAPACITY_PACING_ENABLED is OFF this
    is a no-op (returns the batch unchanged) so nothing changes for the live drip.

    When ON:
      - P3 gate: drop leads whose state has NO active-licensed agent (send nothing
        into an unlicensed state). Leads with no state pass (state-less fallback).
      - P2 cap : keep at most (free licensed agents x CAPACITY_BUFFER) leads this
        tick, so a burst never outruns what free agents can absorb. 0 free -> hold.
    Never sends; only narrows WHICH/HOW MANY leads release this tick. The first-
    template lockdown downstream is untouched.
    """
    info: dict = {}
    # Fatigue at the PULL step (independent of capacity pacing): drop leads whose
    # phone is over the frequency cap or in cooldown, so they're never released into
    # a wave. The send gate re-checks as the final guard.
    if engine_flags.engine_enabled("FATIGUE_ENABLED"):
        from app.core.fatigue import fatigue_ok
        before = len(leads)
        leads = [l for l in leads if fatigue_ok(getattr(l, "phone", None))]
        info["fatigue_dropped"] = before - len(leads)
    if not engine_flags.engine_enabled("CAPACITY_PACING_ENABLED"):
        info["capacity"] = "off"
        return leads, info
    from app.pacing import live_capacity
    states = live_capacity.default_states()
    allowed = live_capacity.states_with_capacity(db, tenant_id, states)
    gated = [l for l in leads if ((getattr(l, "state", None) or "").upper() in allowed)
             or not getattr(l, "state", None)]
    ceiling = live_capacity.release_ceiling(db, tenant_id, states)
    capped = gated[: max(0, ceiling)]
    info.update({"capacity": "on", "allowed_states": sorted(allowed), "ceiling": ceiling,
                 "after_gate": len(gated), "released": len(capped)})
    return capped, info


def drip_cycle(db: Session, tenant_id: str, dry_run: Optional[bool] = None) -> Dict:
    """Queue-Only Mode release: send the first template to `leads` held leads
    every `minutes` (admin-set rate), independent of bookings.

    Only acts when: pacing is enabled (held leads exist), Queue-Only Mode is ON,
    and the kill-switch is OFF. Self-throttles via a per-tenant 'last run'
    timestamp so it honours the chosen interval regardless of how often the beat
    task fires.
    """
    import time as _time
    from app.core.sending import (
        is_sending_paused, is_autopilot_paused,
        get_drip_config, get_drip_last_run, set_drip_last_run,
    )

    # Queue-Only Mode + kill-switch apply to BOTH the per-campaign drip and the
    # same-day-pacing engine drip. The SAME_DAY_PACING_ENABLED gate is applied LOWER
    # DOWN (only to the engine path), so a running CSV campaign always drips at its
    # own rate — that's the basic campaign feature, independent of the pacing engine.
    if not is_autopilot_paused(tenant_id):
        return {"enabled": True, "reason": "not_queue_only"}      # capacity engine owns it
    if is_sending_paused(tenant_id):
        return {"enabled": True, "reason": "kill_switch"}          # full stop wins

    # --- Campaign manager: if a CSV-upload campaign is "running", drip THAT
    # campaign at ITS own rate, releasing only ITS held leads. Only one campaign
    # runs at a time. Auto-stops the campaign once its pool is drained. ---
    try:
        from app.models.campaign import Campaign
        from app.models.lead import Lead as _Lead
        from app.core.redis import redis_service as _r
        running = db.query(Campaign).filter(
            Campaign.tenant_id == tenant_id, Campaign.description == "upload_batch",
            Campaign.send_state == "running", Campaign.deleted_at.is_(None)).first()
    except Exception:
        running = None
    if running is not None:
        rate_leads = max(1, int(running.drip_leads or 50))
        minutes = max(1, int(running.drip_minutes or 10))
        # EVEN drip: release ONE lead every (minutes*60 / rate_leads) seconds, so the
        # batch is spread across the WHOLE interval instead of dumped at once and then
        # idle for the rest of the minute. e.g. 20/1min -> 1 every 3s; 2/1min -> 1
        # every 30s; 10/10min -> 1 per minute. The drip beat fires every few seconds;
        # this self-throttles to per_lead. (Per-DID 1/sec ceiling still caps sends.)
        per_lead = max(0.5, (minutes * 60.0) / rate_leads)   # seconds between sends
        now = _time.time()
        ckey = f"autopilot:drip:last:campaign:{running.id}"
        try:
            lv = _r.client.get(ckey); last = float(lv) if lv else 0.0
        except Exception:
            last = 0.0
        if last:
            n = int((now - last) // per_lead)
            if n < 1:
                return {"enabled": True, "reason": "interval_not_elapsed",
                        "campaign": str(running.id), "wait_s": round(per_lead - (now - last))}
            n = min(n, 20)                     # safety cap so downtime can't burst
            new_last = last + n * per_lead     # advance the clock drift-free
        else:
            n, new_last = 1, now               # first tick: send one, anchor the clock
        if dry_run is None:
            dry_run = bool(getattr(settings, "PACING_DRY_RUN", True))
        leads = (db.query(_Lead).filter(
            _Lead.tenant_id == tenant_id, _Lead.campaign_id == running.id,
            _Lead.pacing_status == "held", _Lead.deleted_at.is_(None))
            .order_by(_Lead.created_at).limit(n).all())
        if not leads:
            try: _r.client.set(ckey, str(now))
            except Exception: pass
            try:
                running.send_state = "stopped"; db.commit()   # drained -> auto-stop
            except Exception:
                db.rollback()
            return {"enabled": True, "reason": "campaign_drained", "released": 0,
                    "campaign": str(running.id)}
        leads, cap_info = _apply_capacity(db, tenant_id, leads)
        if not leads:
            try: _r.client.set(ckey, str(now))
            except Exception: pass
            return {"enabled": True, "reason": "capacity_hold", "released": 0,
                    "campaign": str(running.id), **cap_info}
        wave_id = _wave_id()
        released = _release(db, tenant_id, leads, wave_id, dry_run)
        try: _r.client.set(ckey, str(new_last))
        except Exception: pass
        logger.info("[drip%s] tenant=%s campaign=%s released=%s (even: 1 / %.1fs, target %s/%smin)",
                    " DRY-RUN" if dry_run else "", tenant_id, running.id, released,
                    per_lead, rate_leads, minutes)
        return {"enabled": True, "dry_run": dry_run, "released": released,
                "campaign": str(running.id), "wave_id": wave_id}

    # No CSV campaign running: the same-day-pacing ENGINE's general held-lead release
    # (across all states, by tenant drip config) stays flag-gated so it ships dormant.
    # The per-campaign drip above is intentionally NOT gated.
    if not engine_flags.same_day_pacing_enabled():
        return {"enabled": False, "reason": "pacing_disabled"}

    cfg = get_drip_config(tenant_id)
    leads_per_wave, minutes = int(cfg["leads"]), int(cfg["minutes"])

    # EVEN drip (non-campaign held leads): one lead every (minutes*60 / leads)
    # seconds — same per-lead spreading as the campaign path, not the whole wave
    # at once then a long wait.
    per_lead = max(0.5, (minutes * 60.0) / max(1, leads_per_wave))
    now = _time.time()
    last = get_drip_last_run(tenant_id)
    if last:
        n = int((now - last) // per_lead)
        if n < 1:
            return {"enabled": True, "reason": "interval_not_elapsed",
                    "wait_s": round(per_lead - (now - last)), "config": cfg}
        n = min(n, 20)
        new_last = last + n * per_lead
    else:
        n, new_last = 1, now

    if dry_run is None:
        dry_run = bool(getattr(settings, "PACING_DRY_RUN", True))

    # Select the next batch of held leads across all states (best-first).
    leads = scoring.ranked_held(db, tenant_id, None, n)
    if not leads:
        # Nothing left to drip — stamp the run so we don't busy-check, and report.
        set_drip_last_run(tenant_id, now)
        return {"enabled": True, "reason": "no_held_leads", "released": 0, "config": cfg}

    leads, cap_info = _apply_capacity(db, tenant_id, leads)
    if not leads:
        set_drip_last_run(tenant_id, now)
        return {"enabled": True, "reason": "capacity_hold", "released": 0, "config": cfg, **cap_info}

    wave_id = _wave_id()
    released = _release(db, tenant_id, leads, wave_id, dry_run)
    set_drip_last_run(tenant_id, new_last)
    logger.info("[drip%s] tenant=%s released=%s (even: 1 / %.1fs, rate=%s/%smin) wave=%s",
                " DRY-RUN" if dry_run else "", tenant_id, released,
                per_lead, leads_per_wave, minutes, wave_id)
    return {"enabled": True, "dry_run": dry_run, "released": released,
            "wave_id": wave_id, "config": cfg}


def free_agent_after_deal(tenant_id: str, agent_id) -> Dict:
    """Capacity-engine hook for the Add Deal form: an agent just logged a deal, so
    they are free for the next lead. In its OWN session, mark the CREDITED agent
    AVAILABLE (so live_capacity counts them as free, and queue_service.join hands
    them the next queued lead), then run one drip so more CSV leads release to the
    freed capacity. Best-effort; a no-op unless CAPACITY_PACING_ENABLED.

    `agent_id` is the compliance Agent.id; it is mapped to that agent's auth
    user_id (== SmsQueueAgent.user_id). The send path is unchanged — drip releases
    still go out as kind="first_template", so the first-template-only lockdown is
    untouched.
    """
    from app.core import engine_flags
    if not engine_flags.engine_enabled("CAPACITY_PACING_ENABLED"):
        return {"skipped": "engine_off"}

    from app.core.database import get_db
    from app.models.agent import Agent
    from app.sms_queue.services import queue_service
    from app.sms_queue.services.inbound_sync import _flush_events

    db = next(get_db())
    try:
        ag = (
            db.query(Agent)
            .filter(Agent.id == agent_id, Agent.tenant_id == tenant_id)
            .first()
        )
        if not ag or not ag.user_id:
            return {"skipped": "no_agent_user"}
        # Marks the agent AVAILABLE + assigns them the next queued lead, commits,
        # and returns the realtime events to push (assignment popup, etc.).
        _res, events = queue_service.join(db, tenant_id, str(ag.user_id))
        if events:
            _flush_events(events)
        assigned = sum(1 for e in events if e.get("event") == "sms:lead_assigned")
        released = None
        try:
            released = drip_cycle(db, tenant_id).get("released")
        except Exception:
            logger.warning("free_agent_after_deal: drip_cycle failed", exc_info=True)
        return {"agent_user_id": str(ag.user_id), "assigned": assigned, "released": released}
    finally:
        db.close()
