"""SMS Queue engine — agent lifecycle + lead assignment + chat + disposition.

Design notes:
- The DB work is synchronous (matches the rest of this codebase). Each method
  returns ``(data, events)`` where ``events`` is a list of socket events for the
  async router layer to flush via emit_to_* — keeping this module pure/testable
  and free of socket.io coupling.
- v1 uses direct round-robin assignment (next QUEUED lead -> next AVAILABLE
  idle agent) rather than the source's broadcast-race. Same agent popup UX,
  simpler to reason about; can be swapped for true broadcast later.
- Outbound ``send`` records the message locally (status SENT). The Engage Cloud
  provider call is wired in at step 2 — this is the single seam to swap.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.sms import SmsAgentAction, SmsAgentBreak, SmsDoNotCall, SmsLead, SmsMessage, SmsQueueAgent

BREAK_REASONS = {"Lunch", "Bathroom", "Meeting", "Personal", "Other"}

PRIORITY_RANK = {"HOT": 0, "WARM": 1, "NORMAL": 2}
# Admin-type roles never work the SMS queue: they can't join and are never
# offered leads (so they never get the offer popup). Compared case-insensitively.
ADMIN_ROLES = ("tenant_admin", "admin", "super_admin")
# Consecutive missed offers (reclaimed by rebroadcast) before an agent is
# auto-parked AWAY, so leads stop being offered into an abandoned session.
MISS_AWAY_LIMIT = 2
# How long a lead an agent PASSED is skipped for that agent before it can be
# re-offered to them. Bounds the skip so an aged lead everyone has passed still
# re-enters rotation instead of stranding in the pool forever.
PASS_COOLDOWN_MINUTES = 15
# An agent's UI heartbeats last_active_at every ~5s (via /sms/queue/current).
# If it goes stale past this many seconds the session is gone (closed laptop /
# tab / lost network) and the reaper takes them OFFLINE. Generous enough to ride
# out a refresh or brief blip without flapping.
STALE_AGENT_SECONDS = 60
APPOINTMENT_DISPOSITIONS = {"APPOINTMENT_SET", "SALE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evt(to: str, id_: str, event: str, data: dict) -> dict:
    return {"to": to, "id": str(id_) if id_ else None, "event": event, "data": data}


def _lead_dict(l: SmsLead) -> dict:
    return {
        "id": str(l.id),
        "phone_number": l.phone_number,
        "customer_name": l.customer_name,
        "last_message": l.last_message,
        "priority": l.priority,
        "status": l.status,
        "assigned_agent_id": str(l.assigned_agent_id) if l.assigned_agent_id else None,
        "disposition": l.disposition,
        "message_count": l.message_count,
        "accepted_at": l.accepted_at.isoformat() if l.accepted_at else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


def _compose_address(src) -> str | None:
    """Readable address from a source Lead's city/state/zip (we store no street)."""
    if src is None:
        return None
    region = " ".join(p for p in [src.state, src.zip_code] if p)
    parts = [p for p in [src.city, region] if p]
    return ", ".join(parts) or None


def _lead_dicts_with_address(db: Session, leads: list) -> list[dict]:
    """_lead_dict for each lead, plus a composed `address` from its source Lead.

    Batch-loads the linked Lead rows in one query so a list of leads doesn't fan
    out into N address lookups.
    """
    items = [_lead_dict(l) for l in leads]
    lead_ids = [l.lead_id for l in leads if l.lead_id]
    srcs: dict[str, object] = {}
    if lead_ids:
        from app.models.lead import Lead

        srcs = {str(s.id): s for s in db.query(Lead).filter(Lead.id.in_(lead_ids)).all()}
    for item, l in zip(items, leads):
        item["address"] = _compose_address(srcs.get(str(l.lead_id))) if l.lead_id else None
    return items


def _msg_dict(m: SmsMessage) -> dict:
    return {
        "id": str(m.id),
        "sms_lead_id": str(m.sms_lead_id) if m.sms_lead_id else None,
        "phone_number": m.phone_number,
        "direction": m.direction,
        "body": m.body,
        "sender_type": m.sender_type,
        "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _dispatch_sms(
    tenant_id: str, to_number: str, body: str, lead_id: str | None
) -> tuple[str, str | None, str | None, str | None]:
    """The single outbound seam. Returns (status, provider, provider_message_id, error).

    When SMS_LIVE_SEND_ENABLED is off (dev), records locally as SENT with no
    real send. When on (prod), dispatches via the shared Sinch client.
    """
    from app.core.config import settings

    if not settings.SMS_LIVE_SEND_ENABLED:
        return "SENT", None, None, None
    try:
        from app.ai.services.communication_provider import communication_service

        res = communication_service.send_sms(
            to=to_number, body=body, tenant_id=tenant_id, lead_id=lead_id
        )
        if res.get("error") or res.get("status") == "failed":
            return "FAILED", res.get("provider"), res.get("message_sid"), res.get("error") or "send failed"
        return "SENT", res.get("provider"), res.get("message_sid"), None
    except Exception as exc:  # never let a send crash the request
        return "FAILED", None, None, str(exc)


def _get_or_create_agent(db: Session, tenant_id: str, user_id: str) -> SmsQueueAgent:
    agent = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.user_id == user_id)
        .first()
    )
    if not agent:
        agent = SmsQueueAgent(tenant_id=tenant_id, user_id=user_id, status="OFFLINE")
        db.add(agent)
        db.flush()
    return agent


def _passed_lead_ids(db: Session, tenant_id: str, user_id) -> set[str]:
    """Lead IDs this agent passed *recently* — skipped for THEM only during the
    cooldown, then re-offered.

    A permanent exclusion stranded leads: once every available agent had passed a
    lead, it could never be offered to anyone again and sat QUEUED forever (the
    manager's "Rejected 2+ passes" pile). Scoping the skip to a cooldown keeps the
    original intent — a lead you just passed isn't bounced straight back to you —
    while guaranteeing an aged lead eventually re-enters rotation.
    """
    cutoff = _now() - timedelta(minutes=PASS_COOLDOWN_MINUTES)
    rows = (
        db.query(SmsAgentAction.sms_lead_id, SmsAgentAction.created_at)
        .filter(
            SmsAgentAction.tenant_id == tenant_id,
            SmsAgentAction.user_id == user_id,
            SmsAgentAction.action == "PASS",
            SmsAgentAction.sms_lead_id.isnot(None),
            SmsAgentAction.created_at >= cutoff,
        )
        .all()
    )
    return {str(lid) for lid, created in rows if created is None or created >= cutoff}


def _next_queued_lead(
    db: Session, tenant_id: str, exclude_lead_ids: set[str] | None = None
) -> SmsLead | None:
    rows = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .all()
    )
    if exclude_lead_ids:
        rows = [l for l in rows if str(l.id) not in exclude_lead_ids]
    if not rows:
        return None
    # Pure first-come-first-served: the OLDEST queued lead is served to the next
    # agent. Time only — no HOT/WARM/NORMAL priority tiers.
    rows.sort(key=lambda l: l.created_at)
    return rows[0]


def _available_agents(db: Session, tenant_id: str, exclude_user_id: str | None = None):
    """Available agents holding no lead, longest-idle first.

    Admins (tenant_admin / admin / super_admin) never work the queue, so they're
    excluded here — a lead can never be offered/assigned to an admin regardless
    of their row state.
    """
    from sqlalchemy import func

    from app.models.user import User

    q = (
        db.query(SmsQueueAgent)
        .join(User, User.id == SmsQueueAgent.user_id)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.status == "AVAILABLE",
            SmsQueueAgent.current_lead_id.is_(None),
            func.lower(func.coalesce(User.role, "")).notin_(ADMIN_ROLES),
        )
    )
    if exclude_user_id:
        q = q.filter(SmsQueueAgent.user_id != exclude_user_id)
    return q.order_by(SmsQueueAgent.last_active_at.asc().nullsfirst()).all()


def _try_assign(db: Session, tenant_id: str, exclude_user_id: str | None = None) -> list[dict]:
    """Offer a queued lead to the longest-idle available agent.

    An agent is never re-offered a lead they already passed: we walk available
    agents in idle order and give each the next queued lead they haven't passed.
    So passing always surfaces a *different* lead for the passer, while the lead
    they passed still reaches other agents. Assigns one (agent, lead) pair per
    call — callers loop when they want to fill multiple agents.
    """
    events: list[dict] = []
    for agent in _available_agents(db, tenant_id, exclude_user_id):
        lead = _next_queued_lead(
            db, tenant_id, exclude_lead_ids=_passed_lead_ids(db, tenant_id, agent.user_id)
        )
        if not lead:
            continue
        lead.status = "ASSIGNED"
        lead.assigned_agent_id = agent.user_id
        agent.current_lead_id = lead.id
        db.flush()
        events.append(_evt("agent", agent.user_id, "sms:lead_assigned", _lead_dict(lead)))
        events.append(_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "assigned"}))
        return events
    return events


# ---- Agent lifecycle ----------------------------------------------------

def join(db: Session, tenant_id: str, user_id: str) -> tuple[dict, list[dict]]:
    agent = _get_or_create_agent(db, tenant_id, user_id)
    agent.status = "AVAILABLE"
    agent.last_active_at = _now()
    agent.consecutive_misses = 0
    db.flush()
    events = _try_assign(db, tenant_id)
    db.commit()
    return {"status": agent.status}, events


def _release_held_leads(db: Session, tenant_id: str, user_id: str) -> int:
    """Return every lead this agent holds (offered OR in-progress) to the pool.

    Matched by assigned_agent_id (not the agent's current_lead_id, which can
    drift), so a lead never stays bound to an agent who has stepped away. Without
    this, an offered (ASSIGNED) lead keeps the offer popup showing to an agent
    who is on break / has left.
    """
    held = (
        db.query(SmsLead)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id == user_id,
            SmsLead.status.in_(("ASSIGNED", "IN_PROGRESS")),
        )
        .all()
    )
    for lead in held:
        lead.status = "QUEUED"
        lead.assigned_agent_id = None
        lead.accepted_at = None
    return len(held)


def leave(db: Session, tenant_id: str, user_id: str) -> tuple[dict, list[dict]]:
    agent = _get_or_create_agent(db, tenant_id, user_id)
    _release_held_leads(db, tenant_id, user_id)
    agent.current_lead_id = None
    agent.status = "OFFLINE"
    db.flush()
    events = _try_assign(db, tenant_id)
    db.commit()
    return {"status": agent.status}, events


def _open_break(db: Session, tenant_id: str, user_id: str) -> SmsAgentBreak | None:
    return (
        db.query(SmsAgentBreak)
        .filter(
            SmsAgentBreak.tenant_id == tenant_id,
            SmsAgentBreak.user_id == user_id,
            SmsAgentBreak.ended_at.is_(None),
        )
        .order_by(SmsAgentBreak.started_at.desc())
        .first()
    )


def start_break(db: Session, tenant_id: str, user_id: str, reason: str) -> tuple[dict, list[dict]]:
    reason = reason if reason in BREAK_REASONS else "Other"
    agent = _get_or_create_agent(db, tenant_id, user_id)
    # Close any dangling open break first (safety), then open a fresh one.
    existing = _open_break(db, tenant_id, user_id)
    if existing:
        existing.ended_at = _now()
    brk = SmsAgentBreak(tenant_id=tenant_id, user_id=user_id, reason=reason)
    db.add(brk)
    # Going on break = not working: hand any offered/held lead back to the pool
    # so it isn't stuck on an away agent (which kept the offer popup showing).
    _release_held_leads(db, tenant_id, user_id)
    agent.current_lead_id = None
    agent.status = "AWAY"
    agent.last_active_at = _now()
    db.flush()
    # Redistribute the freed lead(s) to whoever is still available.
    events = _try_assign(db, tenant_id)
    db.commit()
    return {
        "status": agent.status,
        "break_reason": reason,
        "break_started_at": brk.started_at.isoformat() if brk.started_at else None,
    }, events


def end_break(db: Session, tenant_id: str, user_id: str) -> tuple[dict, list[dict]]:
    agent = _get_or_create_agent(db, tenant_id, user_id)
    brk = _open_break(db, tenant_id, user_id)
    if brk:
        brk.ended_at = _now()
    agent.status = "AVAILABLE"
    agent.last_active_at = _now()
    db.flush()
    events = _try_assign(db, tenant_id)
    db.commit()
    return {"status": agent.status}, events


# Backward-compatible toggle (legacy /break endpoint): no reason captured.
def set_break(db: Session, tenant_id: str, user_id: str, on_break: bool) -> tuple[dict, list[dict]]:
    if on_break:
        return start_break(db, tenant_id, user_id, "Other")
    return end_break(db, tenant_id, user_id)


# ---- Lead actions -------------------------------------------------------

def accept(db: Session, tenant_id: str, user_id: str, lead_id: str) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(
            SmsLead.id == lead_id,
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "ASSIGNED",
            SmsLead.assigned_agent_id == user_id,
        )
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "not_assigned_to_you"}, []
    agent = _get_or_create_agent(db, tenant_id, user_id)
    lead.status = "IN_PROGRESS"
    lead.accepted_at = _now()
    agent.status = "ON_CALL"
    agent.current_lead_id = lead.id
    agent.consecutive_misses = 0
    db.add(SmsAgentAction(tenant_id=tenant_id, user_id=user_id, sms_lead_id=lead.id, action="KEEP"))
    db.flush()
    db.commit()
    events = [
        _evt("agent", user_id, "sms:lead_accepted", _lead_dict(lead)),
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "accepted"}),
    ]
    return {"ok": True, "lead": _lead_dict(lead)}, events


def pass_lead(db: Session, tenant_id: str, user_id: str, lead_id: str) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(
            SmsLead.id == lead_id,
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id == user_id,
        )
        .first()
    )
    agent = _get_or_create_agent(db, tenant_id, user_id)
    if lead and lead.status == "ASSIGNED":
        lead.status = "QUEUED"
        lead.assigned_agent_id = None
        lead.pass_count = (lead.pass_count or 0) + 1
        db.add(SmsAgentAction(tenant_id=tenant_id, user_id=user_id, sms_lead_id=lead.id, action="PASS"))
    agent.current_lead_id = None
    # A deliberate PASS is active engagement — the agent is here, working. Only a
    # genuinely IGNORED offer (reclaimed by rebroadcast) is a "miss" that should
    # push them toward auto-park AWAY. Counting passes as misses parked a busy rep
    # after skipping two leads, draining the available pool. Reset it instead.
    agent.consecutive_misses = 0
    db.flush()
    # Re-run assignment. The passer can be offered again, but _try_assign skips
    # leads they've passed (during the cooldown) — so they get the NEXT lead, not
    # the same one straight back, and the passed lead is free to go to others.
    events = _try_assign(db, tenant_id)
    db.commit()
    return {"ok": True}, events


def send_message(
    db: Session, tenant_id: str, user_id: str, lead_id: str, body: str
) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "lead_not_found"}, []

    status, provider, provider_id, error = _dispatch_sms(
        tenant_id, lead.phone_number, body, str(lead.lead_id) if lead.lead_id else None
    )
    msg = SmsMessage(
        tenant_id=tenant_id,
        sms_lead_id=lead.id,
        phone_number=lead.phone_number,
        direction="OUTBOUND",
        body=body,
        sender_type="AGENT",
        agent_id=user_id,
        status=status,
        provider=provider,
        provider_message_id=provider_id,
        error_message=error,
    )
    db.add(msg)
    lead.message_count = (lead.message_count or 0) + 1
    lead.last_message = body
    db.flush()
    db.commit()
    events = [_evt("agent", user_id, "sms:new_message", _msg_dict(msg))]
    return {"ok": status != "FAILED", "message": _msg_dict(msg)}, events


def _parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _create_real_appointment(
    db: Session, tenant_id: str, user_id: str, sms_lead, appointment_time: str | None
) -> str | None:
    """Create a real portal Appointment from an SMS 'Appointment Set' so it shows
    on the calendar AND gets the automatic 24h/1h/15m reminders. Best-effort —
    never raises (an SMS lead without an underlying real lead, or an agent with
    no Agent record, is simply skipped)."""
    try:
        from datetime import timedelta

        from app.models.agent import Agent
        from app.models.appointment import Appointment
        from app.models.lead import Lead

        if not sms_lead.lead_id:
            return None  # manual/sample SMS lead has no real lead to attach
        start = _parse_dt(appointment_time) if appointment_time else None
        if not start:
            return None
        agent_row = (
            db.query(Agent)
            .filter(Agent.tenant_id == tenant_id, Agent.user_id == user_id)
            .first()
        )
        if not agent_row:
            return None
        appt = Appointment(
            tenant_id=tenant_id,
            lead_id=sms_lead.lead_id,
            agent_id=agent_row.id,
            start_time=start,
            end_time=start + timedelta(minutes=30),
            status="confirmed",
            booking_source="manual",
            notes="Booked from SMS Queue",
        )
        db.add(appt)
        # Mark the underlying lead booked so the AI stops outreach (mirrors the
        # AI booking flow).
        real = db.query(Lead).filter(Lead.id == sms_lead.lead_id).first()
        if real:
            real.status = "booked"
        db.flush()
        return str(appt.id)
    except Exception:  # never let appointment creation break the disposition
        return None


def disposition(
    db: Session,
    tenant_id: str,
    user_id: str,
    lead_id: str,
    disposition_value: str,
    callback_time: str | None = None,
    appointment_time: str | None = None,
) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "lead_not_found"}, []
    agent = _get_or_create_agent(db, tenant_id, user_id)
    lead.status = "DISPOSITIONED"
    lead.disposition = disposition_value
    if disposition_value in DNC_DISPOSITIONS:
        add_to_dnc(db, tenant_id, lead.phone_number, disposition_value)
    lead.dispositioned_at = _now()
    if callback_time:
        try:
            lead.callback_time = datetime.fromisoformat(callback_time)
        except ValueError:
            pass
    agent.total_leads_handled = (agent.total_leads_handled or 0) + 1
    appointment_id = None
    if disposition_value == "APPOINTMENT_SET":
        agent.total_appointments_set = (agent.total_appointments_set or 0) + 1
        if appointment_time:
            appointment_id = _create_real_appointment(db, tenant_id, user_id, lead, appointment_time)
    agent.current_lead_id = None
    agent.status = "AVAILABLE"
    db.flush()
    events = [_evt("tenant", tenant_id, "sms:lead_dispositioned", {"lead_id": str(lead.id)})]
    events += _try_assign(db, tenant_id)
    db.commit()
    return {"ok": True, "appointment_id": appointment_id}, events


# ---- Queries ------------------------------------------------------------

# ---- Manager actions ----------------------------------------------------

def reassign(db: Session, tenant_id: str, lead_id: str, new_agent_user_id: str) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "lead_not_found"}, []
    # Detach from the previous agent.
    if lead.assigned_agent_id:
        prev = _get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
        if str(prev.current_lead_id) == str(lead.id):
            prev.current_lead_id = None
    new_agent = _get_or_create_agent(db, tenant_id, new_agent_user_id)
    lead.assigned_agent_id = new_agent.user_id
    lead.status = "ASSIGNED"
    lead.accepted_at = None
    new_agent.current_lead_id = lead.id
    db.flush()
    db.commit()
    events = [
        _evt("agent", new_agent_user_id, "sms:lead_assigned", _lead_dict(lead)),
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "reassigned"}),
    ]
    return {"ok": True}, events


# Dispositions a manager/admin may apply to a pool lead directly (no agent).
MANAGER_DISPOSITIONS = {"WRONG_NUMBER", "UNQUALIFIED"}

# Dispositions that permanently suppress a number (Do Not Call). A lead with one
# of these lands in the "Parked — Unqualified" panel AND its phone is written to
# the sms_do_not_call table so the queue can never re-ingest it.
DNC_DISPOSITIONS = {"WRONG_NUMBER", "UNQUALIFIED", "NOT_INTERESTED"}


def _dnc_phone(phone) -> str:
    """Digits-only phone — the canonical key for the Do-Not-Call list."""
    return "".join(c for c in str(phone or "") if c.isdigit())


def is_dnc(db: Session, tenant_id: str, phone: str) -> bool:
    """True if this number is on the tenant's Do-Not-Call list."""
    digits = _dnc_phone(phone)
    if not digits:
        return False
    return (
        db.query(SmsDoNotCall.id)
        .filter(SmsDoNotCall.tenant_id == tenant_id, SmsDoNotCall.phone_number == digits)
        .first()
        is not None
    )


def add_to_dnc(db: Session, tenant_id: str, phone: str, reason: str | None = None) -> None:
    """Stage a number onto the Do-Not-Call list (no-op if already there). The
    caller commits, so this joins the disposition's own transaction."""
    digits = _dnc_phone(phone)
    if not digits:
        return
    exists = (
        db.query(SmsDoNotCall.id)
        .filter(SmsDoNotCall.tenant_id == tenant_id, SmsDoNotCall.phone_number == digits)
        .first()
    )
    if exists:
        return
    db.add(SmsDoNotCall(tenant_id=tenant_id, phone_number=digits, reason=reason))


def manager_disposition(
    db: Session, tenant_id: str, lead_id: str, disposition_value: str
) -> tuple[dict, list[dict]]:
    """Admin/manager marks a pool lead directly, without an agent handling it.

    Triages a queued lead straight from the Lead Pool as Wrong Number or
    Unqualified: it leaves the pool and lands in the matching review panel
    (Manage-Leads "Wrong Numbers" / "Parked — Unqualified"). Because the row is
    kept as DISPOSITIONED (its lead_id survives), the auto-sync won't re-ingest
    it back into the pool — the mark sticks, same as a delete.
    """
    if disposition_value not in MANAGER_DISPOSITIONS:
        return {"ok": False, "reason": "invalid_disposition"}, []
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "lead_not_found"}, []
    # Detach from any agent who happened to be holding it.
    if lead.assigned_agent_id:
        prev = _get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
        if str(prev.current_lead_id) == str(lead.id):
            prev.current_lead_id = None
    lead.status = "DISPOSITIONED"
    lead.disposition = disposition_value
    if disposition_value in DNC_DISPOSITIONS:
        add_to_dnc(db, tenant_id, lead.phone_number, disposition_value)
    lead.dispositioned_at = _now()
    lead.assigned_agent_id = None
    db.flush()
    events = [_evt("tenant", tenant_id, "sms:lead_dispositioned", {"lead_id": str(lead.id)})]
    events += _try_assign(db, tenant_id)
    db.commit()
    return {"ok": True}, events


def assign_next(db: Session, tenant_id: str) -> tuple[dict, list[dict]]:
    events = _try_assign(db, tenant_id)
    db.commit()
    return {"ok": True, "assigned": bool(events)}, events


def ping_agent(db: Session, tenant_id: str, lead_id: str) -> tuple[dict, list[dict]]:
    """Nudge the agent sitting on a dropped/overdue lead to act on it."""
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False, "reason": "lead_not_found"}, []
    if not lead.assigned_agent_id:
        return {"ok": False, "reason": "no_agent"}, []
    events = [
        _evt(
            "agent",
            lead.assigned_agent_id,
            "sms:ping",
            {
                "lead_id": str(lead.id),
                "phone_number": lead.phone_number,
                "message": "A manager pinged you — please handle this lead.",
            },
        )
    ]
    return {"ok": True, "agent_id": str(lead.assigned_agent_id)}, events


def rebroadcast(db: Session, tenant_id: str, stale_seconds: int = 20) -> tuple[dict, list[dict]]:
    """Recover stuck offers: pull ASSIGNED-but-unaccepted leads (older than
    stale_seconds, e.g. the agent went idle/offline without accepting) back to
    the queue, then re-distribute them to available agents."""
    cutoff = _now() - timedelta(seconds=stale_seconds)
    stuck = (
        db.query(SmsLead)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "ASSIGNED",
            SmsLead.updated_at < cutoff,
        )
        .all()
    )
    for lead in stuck:
        if lead.assigned_agent_id:
            agent = _get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
            if str(agent.current_lead_id) == str(lead.id):
                agent.current_lead_id = None
            lead.pass_count = (lead.pass_count or 0) + 1
            # An agent who keeps failing to accept an offer is treated as gone:
            # park them AWAY after MISS_AWAY_LIMIT misses so leads stop being
            # offered into a dead/abandoned session (and we don't re-offer the
            # same lead to the same unresponsive agent forever).
            agent.consecutive_misses = (agent.consecutive_misses or 0) + 1
            if agent.consecutive_misses >= MISS_AWAY_LIMIT and agent.status == "AVAILABLE":
                agent.status = "AWAY"
        lead.status = "QUEUED"
        lead.assigned_agent_id = None
    db.flush()

    events: list[dict] = []
    for _ in range(100):
        round_events = _try_assign(db, tenant_id)
        if not round_events:
            break
        events += round_events
    db.commit()
    return {
        "ok": True,
        "rebroadcast": len(stuck),
        "reassigned": sum(1 for e in events if e["event"] == "sms:lead_assigned"),
    }, events


def reap_stale_agents(db: Session, tenant_id: str, stale_seconds: int = STALE_AGENT_SECONDS) -> tuple[dict, list[dict]]:
    """Take OFFLINE any agent whose UI stopped heartbeating (closed laptop / tab /
    lost network) and release whatever lead they were holding back to the queue.

    last_active_at is refreshed every ~5s by the agent's /sms/queue/current poll,
    so a value older than `stale_seconds` means the session is gone. Without this,
    a closed laptop leaves the agent frozen as Talking / On break / Waiting to
    accept forever, blocking their lead and skewing the queue.
    """
    cutoff = _now() - timedelta(seconds=stale_seconds)
    stale = (
        db.query(SmsQueueAgent)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.status.in_(("AVAILABLE", "ON_CALL", "AWAY")),
            SmsQueueAgent.last_active_at.isnot(None),
            SmsQueueAgent.last_active_at < cutoff,
        )
        .all()
    )
    events: list[dict] = []
    reaped = 0
    for agent in stale:
        # Release a held/offered lead so it goes back into rotation.
        if agent.current_lead_id:
            lead = db.query(SmsLead).filter(SmsLead.id == agent.current_lead_id).first()
            if lead and lead.status in ("ASSIGNED", "IN_PROGRESS"):
                lead.status = "QUEUED"
                lead.assigned_agent_id = None
                lead.pass_count = (lead.pass_count or 0) + 1
            agent.current_lead_id = None
        # Close any open break so break stats stay correct.
        brk = _open_break(db, tenant_id, str(agent.user_id))
        if brk:
            brk.ended_at = _now()
        agent.status = "OFFLINE"
        reaped += 1
        events.append(_evt("agent", agent.user_id, "sms:presence", {"status": "OFFLINE", "reason": "timeout"}))
    if reaped:
        db.flush()
        events.append(_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "agent_reaped"}))
        # Re-offer any leads we just freed to whoever is still online.
        for _ in range(100):
            round_events = _try_assign(db, tenant_id)
            if not round_events:
                break
            events += round_events
    db.commit()
    return {"reaped": reaped}, events


def distribute_all(db: Session, tenant_id: str, max_rounds: int = 100) -> tuple[dict, list[dict]]:
    events: list[dict] = []
    for _ in range(max_rounds):
        round_events = _try_assign(db, tenant_id)
        if not round_events:
            break
        events += round_events
    db.commit()
    return {"ok": True, "assigned": sum(1 for e in events if e["event"] == "sms:lead_assigned")}, events


def kick_all(db: Session, tenant_id: str) -> tuple[dict, list[dict]]:
    """Force every non-offline agent offline (mirrors the gamified force-offline).

    For each agent we: close any open break, return a held lead (ASSIGNED or
    IN_PROGRESS) to the queue, then mark the agent OFFLINE. Freed leads are
    redistributed to anyone still available afterwards.
    """
    agents = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.status != "OFFLINE")
        .all()
    )
    for a in agents:
        # End any open break so break stats stay correct.
        brk = _open_break(db, tenant_id, str(a.user_id))
        if brk:
            brk.ended_at = _now()
        # Return a held lead (assigned or in-progress) to the queue.
        if a.current_lead_id:
            lead = db.query(SmsLead).filter(SmsLead.id == a.current_lead_id).first()
            if lead and lead.status in ("ASSIGNED", "IN_PROGRESS"):
                lead.status = "QUEUED"
                lead.assigned_agent_id = None
                lead.accepted_at = None
        a.status = "OFFLINE"
        a.queue_position = None
        a.current_lead_id = None
    db.flush()
    # Redistribute freed leads to anyone still available (no-op when everyone
    # was kicked, but matches the gamified re-assign behavior).
    events = _try_assign(db, tenant_id)
    db.commit()
    events.append(_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "kick_all"}))
    return {"ok": True, "kicked": len(agents)}, events


def delete_lead(db: Session, tenant_id: str, lead_id: str) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False}, []
    db.query(SmsMessage).filter(SmsMessage.sms_lead_id == lead.id).delete()
    # Detach agent-action history (PASS/KEEP) from the lead before deleting it.
    # The FK has no ON DELETE rule, so leaving these rows would block the delete;
    # nulling instead of deleting preserves each agent's passed/kept tally.
    db.query(SmsAgentAction).filter(SmsAgentAction.sms_lead_id == lead.id).update(
        {SmsAgentAction.sms_lead_id: None}, synchronize_session=False
    )
    if lead.assigned_agent_id:
        agent = _get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
        if str(agent.current_lead_id) == str(lead.id):
            agent.current_lead_id = None
    # Tombstone instead of hard-delete. Both ingestion paths — the 60s auto-sync
    # (lead_ingest) and the webhook/poll mirror (inbound_sync) — dedupe on
    # "does a sms_leads row already exist for this lead_id?". Hard-deleting wipes
    # that key, so the still-positive reply gets re-ingested within ~60s and the
    # lead reappears in the pool. Keeping a DELETED row preserves the dedupe key
    # (delete sticks), while DELETED is excluded from every pool/active/manage view.
    lead.status = "DELETED"
    lead.assigned_agent_id = None
    lead.disposition = None
    lead.pass_count = 0
    db.commit()
    return {"ok": True}, [_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "deleted"})]


def restore_parked(db: Session, tenant_id: str, lead_id: str) -> tuple[dict, list[dict]]:
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False}, []
    # Do-Not-Call: a parked-unqualified number must not be restorable to the pool.
    if is_dnc(db, tenant_id, lead.phone_number):
        return {"ok": False, "reason": "do_not_call"}, []
    lead.status = "QUEUED"
    lead.disposition = None
    lead.assigned_agent_id = None
    lead.dispositioned_at = None
    lead.pass_count = 0
    db.commit()
    return {"ok": True}, [_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "restored"})]


# category -> filter applied to a SmsLead query. Shared by the bulk-delete
# action and the manage-leads review listing so the two never drift.
def category_filter(q, category: str):
    # Never surface tombstoned (deleted) leads in any Manage-Leads view/action.
    q = q.filter(SmsLead.status != "DELETED")
    if category == "wrong_number":
        return q.filter(SmsLead.disposition == "WRONG_NUMBER")
    if category == "couldnt_sell":
        return q.filter(SmsLead.disposition == "COULDNT_SELL")
    if category == "rejected_blocked":
        return q.filter((SmsLead.pass_count >= 2) | (SmsLead.disposition.in_(("DNC", "UNQUALIFIED", "NOT_INTERESTED"))))
    if category == "attempted_3plus":
        return q.filter(SmsLead.disposition == "ATTEMPTED", SmsLead.pass_count >= 3)
    return None


def bulk_delete(db: Session, tenant_id: str, category: str) -> tuple[dict, list[dict]]:
    from app.models.sms import SmsMessage as _Msg

    q = category_filter(db.query(SmsLead).filter(SmsLead.tenant_id == tenant_id), category)
    if q is None:
        return {"ok": False, "reason": "unknown_category"}, []

    ids = [str(l.id) for l in q.all()]
    if ids:
        db.query(_Msg).filter(_Msg.sms_lead_id.in_(ids)).delete(synchronize_session=False)
        # Detach agent-action history before deleting the leads (FK has no
        # ON DELETE rule); nulling preserves each agent's passed/kept tally.
        db.query(SmsAgentAction).filter(SmsAgentAction.sms_lead_id.in_(ids)).update(
            {SmsAgentAction.sms_lead_id: None}, synchronize_session=False
        )
        # Tombstone, don't hard-delete (see delete_lead): keep the rows as DELETED
        # so the auto-sync never re-ingests these positives back into the pool.
        db.query(SmsLead).filter(SmsLead.id.in_(ids)).update(
            {
                SmsLead.status: "DELETED",
                SmsLead.disposition: None,
                SmsLead.assigned_agent_id: None,
                SmsLead.pass_count: 0,
            },
            synchronize_session=False,
        )
        db.commit()
    return {"ok": True, "deleted": len(ids)}, [
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "bulk_delete"})
    ]


def get_polling(db: Session, tenant_id: str) -> dict:
    from app.models.sms import SmsSettings

    s = db.query(SmsSettings).filter(SmsSettings.tenant_id == tenant_id).first()
    return {"polling_enabled": bool(s.polling_enabled) if s else True}


def set_polling(db: Session, tenant_id: str, enabled: bool) -> dict:
    from app.models.sms import SmsSettings

    s = db.query(SmsSettings).filter(SmsSettings.tenant_id == tenant_id).first()
    if not s:
        s = SmsSettings(tenant_id=tenant_id, polling_enabled=enabled)
        db.add(s)
    else:
        s.polling_enabled = enabled
    db.commit()
    return {"polling_enabled": enabled}


def manager_send(db: Session, tenant_id: str, to_number: str, body: str) -> tuple[dict, list[dict]]:
    """Free-form manager send. Records the message (real Sinch send wired later)."""
    status, provider, provider_id, error = _dispatch_sms(tenant_id, to_number, body, None)
    msg = SmsMessage(
        tenant_id=tenant_id,
        phone_number=to_number,
        direction="OUTBOUND",
        body=body,
        sender_type="SYSTEM",
        status=status,
        provider=provider,
        provider_message_id=provider_id,
        error_message=error,
    )
    db.add(msg)
    db.commit()
    return {"ok": status != "FAILED", "message": _msg_dict(msg)}, [
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "manager_send"})
    ]


def get_status(db: Session, tenant_id: str, user_id: str) -> dict:
    from sqlalchemy import func

    agent = _get_or_create_agent(db, tenant_id, user_id)
    brk = _open_break(db, tenant_id, user_id) if agent.status == "AWAY" else None
    # "Yes" leads waiting in the shared pool (every queued lead is a positive reply).
    yes_waiting = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .scalar()
        or 0
    )
    db.commit()
    return {
        "status": agent.status,
        "current_lead_id": str(agent.current_lead_id) if agent.current_lead_id else None,
        "consecutive_misses": agent.consecutive_misses,
        "total_leads_handled": agent.total_leads_handled,
        "total_appointments_set": agent.total_appointments_set,
        "break_reason": brk.reason if brk else None,
        "break_started_at": brk.started_at.isoformat() if brk and brk.started_at else None,
        "yes_waiting": int(yes_waiting),
    }


def get_my_stats(db: Session, tenant_id: str, user_id: str) -> dict:
    """Per-agent 'Today' stats + scorecard for the SMS Queue (mirrors Gamified)."""
    from sqlalchemy import case, func

    now = _now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Leads I dispositioned today + appointments + avg response.
    handled_row = (
        db.query(
            func.count(SmsLead.id),
            func.sum(case((SmsLead.disposition == "APPOINTMENT_SET", 1), else_=0)),
            func.sum(case((SmsLead.disposition == "SALE", 1), else_=0)),
            func.avg(SmsLead.response_time_ms),
        )
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id == user_id,
            SmsLead.dispositioned_at >= today,
        )
        .first()
    )
    handled = int(handled_row[0] or 0)
    appointments = int(handled_row[1] or 0)
    sold = int(handled_row[2] or 0)
    avg_resp_ms = int(handled_row[3]) if handled_row[3] else 0

    # Offers I acted on today: KEEP = accepted, PASS = passed/missed.
    act_rows = (
        db.query(SmsAgentAction.action, func.count(SmsAgentAction.id))
        .filter(
            SmsAgentAction.tenant_id == tenant_id,
            SmsAgentAction.user_id == user_id,
            SmsAgentAction.created_at >= today,
        )
        .group_by(SmsAgentAction.action)
        .all()
    )
    actions = {a: int(n) for a, n in act_rows}
    accepted = actions.get("KEEP", 0)
    missed = actions.get("PASS", 0)
    received = accepted + missed

    # Break time today + earliest activity (for active-time proxy).
    breaks = (
        db.query(SmsAgentBreak)
        .filter(
            SmsAgentBreak.tenant_id == tenant_id,
            SmsAgentBreak.user_id == user_id,
            SmsAgentBreak.started_at >= today,
        )
        .all()
    )
    break_seconds = 0
    first_seen = None
    for b in breaks:
        end = b.ended_at or now
        break_seconds += max(0, int((end - b.started_at).total_seconds()))
        if first_seen is None or b.started_at < first_seen:
            first_seen = b.started_at
    first_act = (
        db.query(func.min(SmsAgentAction.created_at))
        .filter(
            SmsAgentAction.tenant_id == tenant_id,
            SmsAgentAction.user_id == user_id,
            SmsAgentAction.created_at >= today,
        )
        .scalar()
    )
    if first_act and (first_seen is None or first_act < first_seen):
        first_seen = first_act
    active_seconds = max(0, int((now - first_seen).total_seconds()) - break_seconds) if first_seen else 0

    # Conversion = actual sales / leads handled. Appointments are NOT sales, so
    # they must not count toward conversion (an appointment that never closes is
    # not a conversion); only a SALE disposition does.
    conversion_pct = round(sold / handled * 100) if handled else 0
    return {
        "today": {
            "leads_handled": handled,
            "avg_response_ms": avg_resp_ms,
            "appointments": appointments,
            "sold": sold,
            "conversion_pct": conversion_pct,
        },
        "scorecard": {
            "leads_received": received,
            "accepted": accepted,
            "missed": missed,
            "appointments": appointments,
            "sold": sold,
            "avg_response_ms": avg_resp_ms,
            "break_seconds": break_seconds,
            "active_seconds": active_seconds,
            "conversion_pct": conversion_pct,
        },
    }


def get_current(db: Session, tenant_id: str, user_id: str) -> dict:
    """The lead currently offered to or being handled by this agent.

    Doubles as the agent heartbeat: the agent UI polls this every ~5s, so we
    stamp last_active_at here. A stale last_active_at therefore means the
    browser stopped polling (closed laptop/tab) and the reaper can safely
    take the agent OFFLINE. We only touch a LIVE (non-OFFLINE) row — never
    resurrect or create one for a manager just viewing.
    """
    agent = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.user_id == user_id)
        .first()
    )
    if agent is not None and agent.status != "OFFLINE":
        agent.last_active_at = _now()
        db.commit()

    lead = (
        db.query(SmsLead)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id == user_id,
            SmsLead.status.in_(("ASSIGNED", "IN_PROGRESS")),
        )
        .order_by(SmsLead.accepted_at.desc().nullslast())
        .first()
    )
    # Safety net: never surface an unaccepted OFFER (ASSIGNED) to an agent who
    # isn't actively working (away / offline / no live row). This is what makes
    # the offer popup appear to a "not joined" agent if a lead ever drifts onto
    # them; hide it so only AVAILABLE/ON_CALL agents get offered.
    if lead and lead.status == "ASSIGNED" and (agent is None or agent.status in ("AWAY", "OFFLINE")):
        lead = None
    data = _lead_dict(lead) if lead else None
    # Enrich the offer popup with the contact's address (city/state/zip from the
    # linked source Lead). Looked up at read time so existing leads show it too.
    if data and lead.lead_id:
        from app.models.lead import Lead

        src = db.query(Lead).filter(Lead.id == lead.lead_id).first()
        data["address"] = _compose_address(src)
    return {"lead": data}


def get_my_leads(db: Session, tenant_id: str, user_id: str, limit: int = 100) -> dict:
    rows = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.assigned_agent_id == user_id)
        .order_by(SmsLead.updated_at.desc())
        .limit(limit)
        .all()
    )
    return {"items": _lead_dicts_with_address(db, rows)}


def get_conversation(db: Session, tenant_id: str, lead_id: str) -> dict:
    rows = (
        db.query(SmsMessage)
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.sms_lead_id == lead_id)
        .order_by(SmsMessage.created_at.asc())
        .all()
    )
    # The agent should NOT see the automated opener — only the conversation from
    # the customer's first reply onward. Drop any leading OUTBOUND messages that
    # came before the first INBOUND (customer) message.
    first_inbound = next(
        (i for i, m in enumerate(rows) if m.direction == "INBOUND"), None
    )
    if first_inbound is not None:
        rows = rows[first_inbound:]
    return {"items": [_msg_dict(m) for m in rows]}


def simulate_inbound(
    db: Session, tenant_id: str, lead_id: str, body: str
) -> tuple[dict, list[dict]]:
    """DEV helper: fake a customer reply so the chat is demonstrable pre-Engage."""
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id)
        .first()
    )
    if not lead:
        return {"ok": False}, []
    msg = SmsMessage(
        tenant_id=tenant_id,
        sms_lead_id=lead.id,
        phone_number=lead.phone_number,
        direction="INBOUND",
        body=body,
        sender_type="CUSTOMER",
        status="RECEIVED",
    )
    db.add(msg)
    lead.message_count = (lead.message_count or 0) + 1
    lead.last_message = body
    db.flush()
    db.commit()
    events: list[dict] = [_evt("tenant", tenant_id, "sms:new_message", _msg_dict(msg))]
    if lead.assigned_agent_id:
        events.append(_evt("agent", lead.assigned_agent_id, "sms:new_message", _msg_dict(msg)))
    return {"ok": True, "message": _msg_dict(msg)}, events
