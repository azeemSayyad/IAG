"""Read aggregation for the SMS Manager board.

All tenant-scoped. Read-only for now (force-actions land in a later slice).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.sms import (
    SmsAgentAction,
    SmsAgentBreak,
    SmsLead,
    SmsMessage,
    SmsPollLog,
    SmsQueueAgent,
)
from app.models.user import User

ONLINE_STATUSES = ("AVAILABLE", "ON_CALL", "AWAY")
PRIORITY_RANK = {"HOT": 0, "WARM": 1, "NORMAL": 2}
# Leads still "alive" in the human lane — every one was a positive ("yes") reply.
YES_OPEN_STATUSES = ("QUEUED", "ASSIGNED", "IN_PROGRESS")


def _name(user: User | None) -> str:
    if not user:
        return "Unassigned"
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or user.email


def _period_start(period: str) -> datetime:
    """Start of the requested window (day | week | month), tz-aware UTC."""
    now = datetime.now(timezone.utc)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    # default: today (since local midnight UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_overview(db: Session, tenant_id: str) -> dict:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    agent_rows = (
        db.query(SmsQueueAgent, User)
        .outerjoin(User, User.id == SmsQueueAgent.user_id)
        .filter(SmsQueueAgent.tenant_id == tenant_id)
        .all()
    )
    # Open breaks (reason + since) for AWAY agents.
    open_breaks = {
        str(b.user_id): b
        for b in db.query(SmsAgentBreak).filter(
            SmsAgentBreak.tenant_id == tenant_id, SmsAgentBreak.ended_at.is_(None)
        )
    }
    # Agents currently holding an offered-but-unaccepted lead (waiting to accept).
    offered_agents = {
        str(uid)
        for (uid,) in db.query(SmsLead.assigned_agent_id)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "ASSIGNED",
            SmsLead.assigned_agent_id.isnot(None),
        )
        .distinct()
    }

    def _activity(status: str, uid: str) -> str:
        # Friendly state for the manager board: talking / waiting / idle / away / offline.
        if status == "ON_CALL":
            return "talking"
        if status == "AWAY":
            return "away"
        if status == "AVAILABLE":
            return "waiting" if uid in offered_agents else "idle"
        return "offline"

    agents = [
        {
            "user_id": str(a.user_id),
            "name": _name(u),
            "status": a.status,
            "activity": _activity(a.status, str(a.user_id)),
            "queue_position": a.queue_position,
            "total_leads_handled": a.total_leads_handled,
            "total_appointments_set": a.total_appointments_set,
            "avg_response_time_ms": a.avg_response_time_ms,
            "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None,
            "break_reason": open_breaks[str(a.user_id)].reason if str(a.user_id) in open_breaks else None,
            "break_started_at": (
                open_breaks[str(a.user_id)].started_at.isoformat()
                if str(a.user_id) in open_breaks and open_breaks[str(a.user_id)].started_at
                else None
            ),
        }
        for a, u in agent_rows
    ]
    agents.sort(key=lambda x: (x["status"] not in ONLINE_STATUSES, x["name"]))

    by_status: dict[str, int] = {}
    by_activity: dict[str, int] = {}
    for a in agents:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        by_activity[a["activity"]] = by_activity.get(a["activity"], 0) + 1

    def _count(*statuses: str) -> int:
        return (
            db.query(func.count(SmsLead.id))
            .filter(SmsLead.tenant_id == tenant_id, SmsLead.status.in_(statuses))
            .scalar()
            or 0
        )

    dispositioned_today = (
        db.query(func.count(SmsLead.id))
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.dispositioned_at >= today,
        )
        .scalar()
        or 0
    )

    # "Yes" counts — every sms_lead was pulled in because the customer replied
    # positively. yes_open = still being worked; yes_today = arrived today.
    yes_open = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status.in_(YES_OPEN_STATUSES))
        .scalar()
        or 0
    )
    yes_today = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.created_at >= today)
        .scalar()
        or 0
    )

    try:
        from app.sms_queue.services.wrap_presence import wrapping_user_ids
        _wrap_ids = wrapping_user_ids(str(tenant_id))
    except Exception:
        _wrap_ids = set()
    # Flag WHICH agents are wrapping (on the Add Deal form / after-call work); the count
    # is derived from the same set so the KPI and the per-agent flags always agree.
    for _a in agents:
        _a["wrapping"] = _a["user_id"] in _wrap_ids
    _wrapping = sum(1 for _a in agents if _a["wrapping"])

    return {
        "agents": agents,
        "counts": {
            # agents currently on the Add Deal form (after-call work) — display-only
            "wrapping": _wrapping,
            "available": by_status.get("AVAILABLE", 0),
            "on_call": by_status.get("ON_CALL", 0),
            "away": by_status.get("AWAY", 0),
            "queued": _count("QUEUED"),
            "assigned": _count("ASSIGNED"),
            "in_progress": _count("IN_PROGRESS"),
            "parked": _count("PARKED"),
            "dispositioned_today": dispositioned_today,
            "agents_online": sum(1 for a in agents if a["status"] in ONLINE_STATUSES),
            # #4 agent states
            "talking": by_activity.get("talking", 0),
            "waiting": by_activity.get("waiting", 0),
            "idle": by_activity.get("idle", 0),
            # #3 yes counts
            "yes_open": int(yes_open),
            "yes_today": int(yes_today),
        },
    }


def get_pool_counts(db: Session, tenant_id: str) -> dict:
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=15)
    queued = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .all()
    )
    # "Fresh" = last ACTIVITY (a reply bumps updated_at), not first-seen — so a lead
    # that just replied counts as fresh even if it entered the pool days ago.
    fresh = sum(1 for l in queued if (l.updated_at or l.created_at) and (l.updated_at or l.created_at) >= cutoff)
    aged = len(queued) - fresh
    rejected = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.pass_count >= 2)
        .scalar()
        or 0
    )
    return {"freshCount": fresh, "agedCount": aged, "rejectedCount": rejected}


def get_queued(db: Session, tenant_id: str, limit: int = 100) -> dict:
    rows = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .all()
    )
    # Pool is ordered by LAST ACTIVITY, newest first (top of the UI) — a lead that
    # just replied jumps to the top with a correct "x min ago", even if it first
    # entered the pool days earlier. Serving order is unchanged (oldest-first /
    # FIFO) in queue_service._next_queued_lead.
    rows.sort(key=lambda l: (l.updated_at or l.created_at), reverse=True)
    rows = rows[:limit]
    return {
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "customer_name": l.customer_name,
                "priority": l.priority,
                "last_message": l.last_message,
                "message_count": l.message_count,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                # When the lead LAST messaged/changed — what the UI shows as "x ago".
                "last_message_at": (l.updated_at or l.created_at).isoformat() if (l.updated_at or l.created_at) else None,
            }
            for l in rows
        ]
    }


def get_active(db: Session, tenant_id: str, limit: int = 100) -> dict:
    rows = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status.in_(("ASSIGNED", "IN_PROGRESS")),
        )
        .order_by(SmsLead.accepted_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "customer_name": l.customer_name,
                "status": l.status,
                "priority": l.priority,
                "last_message": l.last_message,
                "agent_name": _name(u),
                "assigned_agent_id": str(l.assigned_agent_id) if l.assigned_agent_id else None,
                "message_count": l.message_count,
                "accepted_at": l.accepted_at.isoformat() if l.accepted_at else None,
            }
            for l, u in rows
        ]
    }


def _range(from_iso: str | None, to_iso: str | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = now
    start = now - timedelta(days=7)
    try:
        if from_iso:
            start = datetime.fromisoformat(from_iso).replace(tzinfo=timezone.utc)
        if to_iso:
            end = datetime.fromisoformat(to_iso).replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError:
        pass
    return start, end


def get_leaderboard(db: Session, tenant_id: str, from_iso: str | None = None, to_iso: str | None = None) -> dict:
    start, end = _range(from_iso, to_iso)
    rows = (
        db.query(
            SmsLead.assigned_agent_id,
            func.count(SmsLead.id).label("attempted"),
            func.sum(case((SmsLead.message_count > 1, 1), else_=0)).label("replied"),
            func.sum(case((SmsLead.disposition == "APPOINTMENT_SET", 1), else_=0)).label("appointments"),
            func.sum(case((SmsLead.disposition == "SALE", 1), else_=0)).label("sold"),
            func.avg(SmsLead.response_time_ms).label("avg_resp"),
        )
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id.isnot(None),
            # "Attempted" = every lead the agent worked in range (matches the
            # Gamified leaderboard), not only the ones that were dispositioned.
            SmsLead.created_at >= start,
            SmsLead.created_at < end,
        )
        .group_by(SmsLead.assigned_agent_id)
        .all()
    )
    users = {str(u.id): u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    board = []
    for agent_id, attempted, replied, appointments, sold, avg_resp in rows:
        attempted = int(attempted or 0)
        replied = int(replied or 0)
        sold = int(sold or 0)
        board.append(
            {
                "agent_name": _name(users.get(str(agent_id))),
                "attempted": attempted,
                "replied": replied,
                "reply_rate_pct": round(replied / attempted * 100) if attempted else 0,
                "sold": sold,
                "appointments": int(appointments or 0),
                "handled": attempted,
                "conv_rate_pct": round(sold / attempted * 100) if attempted else 0,
                "avg_response_ms": int(avg_resp) if avg_resp else None,
            }
        )
    board.sort(key=lambda x: (x["sold"], x["appointments"], x["attempted"]), reverse=True)
    return {"items": board}


def get_funnel(db: Session, tenant_id: str, from_iso: str | None = None, to_iso: str | None = None) -> dict:
    start, end = _range(from_iso, to_iso)
    base = db.query(SmsLead).filter(
        SmsLead.tenant_id == tenant_id,
        SmsLead.created_at >= start,
        SmsLead.created_at < end,
    )
    attempted = base.count()
    replied = base.filter(SmsLead.message_count > 1).count()
    sold = base.filter(SmsLead.disposition == "SALE").count()
    return {
        "from": start.date().isoformat(),
        "to": (end - timedelta(days=1)).date().isoformat(),
        "attempted": attempted,
        "replied": replied,
        "sold": sold,
        "replied_pct": round(replied / attempted * 100) if attempted else 0,
        "sold_pct": round(sold / attempted * 100) if attempted else 0,
    }


# Dispositions intentionally hidden from the manager/admin-facing views.
# "Eligible for Medicare" is recorded only so those leads can be pulled straight
# from the DB later (they're not sellable — Medicare isn't a product we sell), so
# it must never surface in the dashboards, per-agent breakdowns or re-text lists.
HIDDEN_DISPOSITIONS = ("ELIGIBLE_FOR_MEDICARE",)


# A parked "kind" can fold in related dispositions so they share one panel.
# Wrong Number + Unqualified both park into the one "Do Not Call" panel, so a
# manager triaging straight from the Lead Pool sees them land in the same place.
PARKED_KINDS: dict[str, tuple[str, ...]] = {
    "UNQUALIFIED": ("UNQUALIFIED", "NOT_INTERESTED", "WRONG_NUMBER"),
}


def get_parked(db: Session, tenant_id: str, kind: str = "ATTEMPTED", limit: int = 50) -> dict:
    dispositions = PARKED_KINDS.get(kind, (kind,))
    rows = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.disposition.in_(dispositions))
        .order_by(SmsLead.dispositioned_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    total = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.disposition.in_(dispositions))
        .scalar()
        or 0
    )
    return {
        "total": total,
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "last_message": l.last_message,
                "agent_name": _name(u),
                "dispositioned_at": l.dispositioned_at.isoformat() if l.dispositioned_at else None,
            }
            for l, u in rows
        ],
    }


def get_engine_status(db: Session, tenant_id: str) -> dict:
    """Capacity-pacing + multi-carrier fleet status for the manager dashboard (CF).
    Read-only; each section is independently guarded so one failure can't 500 the
    whole panel."""
    from app.core.config import settings
    from app.core import engine_flags
    _f = engine_flags.all_flags()

    def _flag(name: str) -> dict:
        return _f.get(name, {"enabled": False, "source": "env"})

    out = {
        "flags": {
            # Master switch for the whole same-day pacing/release engine (UI-flippable).
            "same_day_pacing_enabled": _flag("SAME_DAY_PACING_ENABLED")["enabled"],
            "same_day_pacing_source": _flag("SAME_DAY_PACING_ENABLED")["source"],
            "capacity_pacing_enabled": _f["CAPACITY_PACING_ENABLED"]["enabled"],
            "capacity_pacing_source": _f["CAPACITY_PACING_ENABLED"]["source"],
            "fatigue_enabled": _f["FATIGUE_ENABLED"]["enabled"],
            "fatigue_source": _f["FATIGUE_ENABLED"]["source"],
            # DID-fleet enforcement toggles (observe-only when off) — UI-flippable live.
            "carrier_caps_enforce": _flag("CARRIER_CAPS_ENFORCE")["enabled"],
            "carrier_caps_enforce_source": _flag("CARRIER_CAPS_ENFORCE")["source"],
            "tmobile_dedup_enforce": _flag("TMOBILE_DEDUP_ENFORCE")["enabled"],
            "tmobile_dedup_enforce_source": _flag("TMOBILE_DEDUP_ENFORCE")["source"],
            "working_hours_enforce": _flag("WORKING_HOURS_ENFORCE")["enabled"],
            "working_hours_enforce_source": _flag("WORKING_HOURS_ENFORCE")["source"],
            "capacity_buffer": float(getattr(settings, "CAPACITY_BUFFER", 1.5)),
        },
    }
    try:
        from app.pacing import live_capacity as lc
        states = lc.default_states()
        out["capacity"] = {
            "states": states,
            "free_agents_by_state": lc.free_licensed_by_state(db, tenant_id, states),
            "states_with_capacity": sorted(lc.states_with_capacity(db, tenant_id, states)),
            "release_ceiling": lc.release_ceiling(db, tenant_id, states),
        }
    except Exception as exc:
        out["capacity"] = {"error": str(exc)}
    try:
        from app.ai.services import sender_pool
        out["fleet"] = sender_pool.fleet_status()
    except Exception as exc:
        out["fleet"] = {"error": str(exc)}
    return out


def set_engine_flag(name: str, enabled: bool) -> dict:
    """Set a UI-toggleable engine flag's Redis override (live, no redeploy). Returns
    the new effective state. Raises ValueError on a non-toggleable flag (e.g. the
    lockdown is never exposed)."""
    from app.core import engine_flags
    engine_flags.set_engine_override(name, bool(enabled))
    return {"name": name, "enabled": engine_flags.engine_enabled(name),
            "source": engine_flags.flag_source(name)}


def get_fleet_dashboard() -> dict:
    """The DID Fleet page view: per-dimension enforcement, provider + T-Mobile-pair
    caps, recipient-carrier rollup, dedup/routing tallies, Pacific reset (+ the
    provisioning forecast, recorded as a side effect)."""
    from app.ai.services import fleet_dashboard
    return fleet_dashboard.did_fleet_view()


def get_manage_leads(db: Session, tenant_id: str, category: str, limit: int = 200) -> dict:
    """List the leads a Manage-Leads bulk action would affect, for review."""
    from app.sms_queue.services import queue_service

    joined = queue_service.category_filter(
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(SmsLead.tenant_id == tenant_id),
        category,
    )
    if joined is None:
        return {"ok": False, "reason": "unknown_category", "total": 0, "items": []}
    counted = queue_service.category_filter(
        db.query(func.count(SmsLead.id)).filter(SmsLead.tenant_id == tenant_id), category
    )
    total = counted.scalar() or 0
    rows = joined.order_by(SmsLead.dispositioned_at.desc().nullslast()).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "last_message": l.last_message,
                "agent_name": _name(u),
                "dispositioned_at": l.dispositioned_at.isoformat() if l.dispositioned_at else None,
            }
            for l, u in rows
        ],
    }


def get_dropped_leads(db: Session, tenant_id: str, limit: int = 50) -> dict:
    """Assigned/in-progress leads with no recent agent activity (overdue)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    base = db.query(SmsLead).filter(
        SmsLead.tenant_id == tenant_id,
        SmsLead.status.in_(("ASSIGNED", "IN_PROGRESS")),
        SmsLead.updated_at < cutoff,
    )
    total = base.count()
    rows = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status.in_(("ASSIGNED", "IN_PROGRESS")),
            SmsLead.updated_at < cutoff,
        )
        .order_by(SmsLead.updated_at.asc())
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "agent_name": _name(u),
                "assigned_agent_id": str(l.assigned_agent_id) if l.assigned_agent_id else None,
                "last_message": l.last_message,
                "updated_at": l.updated_at.isoformat() if l.updated_at else None,
            }
            for l, u in rows
        ],
    }


def get_agent_activity(db: Session, tenant_id: str, from_iso: str | None = None, to_iso: str | None = None) -> dict:
    start, end = _range(from_iso, to_iso)
    rows = (
        db.query(
            SmsLead.assigned_agent_id,
            func.sum(case((SmsLead.accepted_at.isnot(None), 1), else_=0)).label("accepted"),
            func.sum(case((SmsLead.dispositioned_at.isnot(None), 1), else_=0)).label("dispositioned"),
        )
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id.isnot(None),
            SmsLead.updated_at >= start,
            SmsLead.updated_at < end,
        )
        .group_by(SmsLead.assigned_agent_id)
        .all()
    )
    users = {str(u.id): u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    return {
        "from": start.date().isoformat(),
        "to": (end - timedelta(days=1)).date().isoformat(),
        "items": [
            {
                "agent_name": _name(users.get(str(agent_id))),
                "accepted": int(accepted or 0),
                "dispositioned": int(dispositioned or 0),
            }
            for agent_id, accepted, dispositioned in rows
        ],
    }


def get_callbacks(db: Session, tenant_id: str, limit: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.callback_time.isnot(None))
        .order_by(SmsLead.callback_time.asc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "agent_name": _name(u),
                "callback_time": l.callback_time.isoformat() if l.callback_time else None,
                "past_due": bool(l.callback_time and l.callback_time < now),
            }
            for l, u in rows
        ]
    }


def get_webhook_reliability(db: Session, tenant_id: str) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    inbound = (
        db.query(func.count(SmsMessage.id))
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "INBOUND",
            SmsMessage.created_at >= since,
        )
        .scalar()
        or 0
    )
    poll_rows = (
        db.query(SmsPollLog.succeeded, func.count(SmsPollLog.id))
        .filter(SmsPollLog.attempted_at >= since)
        .group_by(SmsPollLog.succeeded)
        .all()
    )
    polls = {bool(ok): n for ok, n in poll_rows}
    polls_ok = polls.get(True, 0)
    polls_total = polls_ok + polls.get(False, 0)
    pulled = (
        db.query(func.coalesce(func.sum(SmsPollLog.messages_pulled), 0))
        .filter(SmsPollLog.attempted_at >= since, SmsPollLog.succeeded.is_(True))
        .scalar()
        or 0
    )
    poll_rate = round(polls_ok / polls_total * 100, 1) if polls_total else 100.0
    return {
        "last24h_inbound": inbound,
        "polls_succeeded": polls_ok,
        "polls_attempted": polls_total,
        "poll_success_rate_pct": poll_rate,
        "messages_pulled_via_polling": int(pulled),
        "status": "RELIABLE" if poll_rate >= 95 else "DEGRADED",
    }


def get_pass_keep(db: Session, tenant_id: str, period: str = "day") -> dict:
    """Per-agent passed-vs-kept tally for the chosen window (day | week | month).

    KEEP = accepted an offered lead; PASS = passed it on. Logged in
    sms_agent_actions so the numbers are time-bucketed and sortable.
    """
    start = _period_start(period)
    rows = (
        db.query(
            SmsAgentAction.user_id,
            func.sum(case((SmsAgentAction.action == "KEEP", 1), else_=0)).label("kept"),
            func.sum(case((SmsAgentAction.action == "PASS", 1), else_=0)).label("passed"),
        )
        .filter(
            SmsAgentAction.tenant_id == tenant_id,
            SmsAgentAction.created_at >= start,
        )
        .group_by(SmsAgentAction.user_id)
        .all()
    )
    users = {str(u.id): u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    items = []
    for user_id, kept, passed in rows:
        kept = int(kept or 0)
        passed = int(passed or 0)
        offered = kept + passed
        items.append(
            {
                "agent_name": _name(users.get(str(user_id))),
                "kept": kept,
                "passed": passed,
                "offered": offered,
                "keep_rate_pct": round(kept / offered * 100) if offered else 0,
            }
        )
    items.sort(key=lambda x: (x["kept"], -x["passed"]), reverse=True)
    return {"period": period, "since": start.isoformat(), "items": items}


def get_agent_dispositions(db: Session, tenant_id: str, period: str = "day") -> dict:
    """Per-agent breakdown: call dispositions + break time (away/lunch/etc.)."""
    start = _period_start(period)

    # Call dispositions per agent within the window.
    disp_rows = (
        db.query(
            SmsLead.assigned_agent_id,
            SmsLead.disposition,
            func.count(SmsLead.id),
        )
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id.isnot(None),
            SmsLead.disposition.isnot(None),
            SmsLead.disposition.notin_(HIDDEN_DISPOSITIONS),
            SmsLead.dispositioned_at >= start,
        )
        .group_by(SmsLead.assigned_agent_id, SmsLead.disposition)
        .all()
    )

    # Only breaks the agent is CURRENTLY on (live state). A completed lunch must
    # not linger here as "Lunch: 1" — finished breaks are reported in the
    # "Breaks Today" panel. An ongoing break is one with no ended_at.
    now = datetime.now(timezone.utc)
    break_rows = (
        db.query(SmsAgentBreak)
        .filter(
            SmsAgentBreak.tenant_id == tenant_id,
            SmsAgentBreak.ended_at.is_(None),
        )
        .all()
    )

    users = {str(u.id): u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    agg: dict[str, dict] = {}

    def _row(uid: str) -> dict:
        if uid not in agg:
            agg[uid] = {
                "agent_name": _name(users.get(uid)),
                "dispositions": {},
                "breaks": {},
                "break_seconds": 0,
                "break_count": 0,
            }
        return agg[uid]

    for user_id, disposition, count in disp_rows:
        r = _row(str(user_id))
        r["dispositions"][disposition] = int(count or 0)

    for b in break_rows:
        r = _row(str(b.user_id))
        end = b.ended_at or now
        secs = max(0, int((end - b.started_at).total_seconds()))
        r["breaks"][b.reason] = r["breaks"].get(b.reason, 0) + 1
        r["break_seconds"] += secs
        r["break_count"] += 1

    items = sorted(agg.values(), key=lambda x: x["agent_name"])
    return {"period": period, "since": start.isoformat(), "items": items}


def get_restorable_yes(db: Session, tenant_id: str, limit: int = 50) -> dict:
    """Closed-out "yes" leads that a manager can re-queue or re-text.

    Lists recently dispositioned leads (all were positive replies) so a manager
    can give a missed/cold "yes" another attempt.
    """
    rows = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "DISPOSITIONED",
            SmsLead.disposition.notin_(HIDDEN_DISPOSITIONS),
        )
        .order_by(SmsLead.dispositioned_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    total = (
        db.query(func.count(SmsLead.id))
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "DISPOSITIONED",
            SmsLead.disposition.notin_(HIDDEN_DISPOSITIONS),
        )
        .scalar()
        or 0
    )
    return {
        "total": total,
        "items": [
            {
                "id": str(l.id),
                "phone_number": l.phone_number,
                "customer_name": l.customer_name,
                "last_message": l.last_message,
                "disposition": l.disposition,
                "agent_name": _name(u),
                "dispositioned_at": l.dispositioned_at.isoformat() if l.dispositioned_at else None,
            }
            for l, u in rows
        ],
    }


def get_breaks_today(db: Session, tenant_id: str) -> dict:
    """Every break taken today (reason + start/end/duration) + per-agent totals."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(SmsAgentBreak, User)
        .outerjoin(User, User.id == SmsAgentBreak.user_id)
        .filter(SmsAgentBreak.tenant_id == tenant_id, SmsAgentBreak.started_at >= today)
        .order_by(SmsAgentBreak.started_at.asc())
        .all()
    )
    items = []
    totals: dict[str, int] = {}
    for b, u in rows:
        end = b.ended_at or now
        secs = max(0, int((end - b.started_at).total_seconds()))
        name = _name(u)
        items.append(
            {
                "agent_name": name,
                "reason": b.reason,
                "started_at": b.started_at.isoformat() if b.started_at else None,
                "ended_at": b.ended_at.isoformat() if b.ended_at else None,
                "ongoing": b.ended_at is None,
                "duration_seconds": secs,
            }
        )
        totals[name] = totals.get(name, 0) + secs
    return {
        "items": items,
        "totals": [{"agent_name": k, "total_seconds": v} for k, v in sorted(totals.items())],
    }


def get_daily_summary(db: Session, tenant_id: str) -> dict:
    """Per-agent day: shift / break / billable time + applications/appts/avg/conv."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    lead_rows = (
        db.query(
            SmsLead.assigned_agent_id,
            func.count(SmsLead.id),
            func.sum(case((SmsLead.disposition == "SALE", 1), else_=0)),
            func.sum(case((SmsLead.disposition == "APPOINTMENT_SET", 1), else_=0)),
            func.avg(SmsLead.response_time_ms),
        )
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.assigned_agent_id.isnot(None),
            SmsLead.dispositioned_at >= today,
        )
        .group_by(SmsLead.assigned_agent_id)
        .all()
    )
    lead_by: dict[str, dict] = {}
    for agent_id, handled, sold, appts, avg in lead_rows:
        lead_by[str(agent_id)] = {
            "handled": int(handled or 0),
            "applications": int(sold or 0),
            "appts": int(appts or 0),
            "avg": int(avg) if avg else 0,
        }

    break_by: dict[str, int] = {}
    first_seen: dict[str, datetime] = {}
    for b in (
        db.query(SmsAgentBreak)
        .filter(SmsAgentBreak.tenant_id == tenant_id, SmsAgentBreak.started_at >= today)
        .all()
    ):
        uid = str(b.user_id)
        end = b.ended_at or now
        break_by[uid] = break_by.get(uid, 0) + max(0, int((end - b.started_at).total_seconds()))
        if uid not in first_seen or b.started_at < first_seen[uid]:
            first_seen[uid] = b.started_at
    for uid, mn in (
        db.query(SmsAgentAction.user_id, func.min(SmsAgentAction.created_at))
        .filter(SmsAgentAction.tenant_id == tenant_id, SmsAgentAction.created_at >= today)
        .group_by(SmsAgentAction.user_id)
        .all()
    ):
        k = str(uid)
        if mn and (k not in first_seen or mn < first_seen[k]):
            first_seen[k] = mn

    users = {str(u.id): u for u in db.query(User).filter(User.tenant_id == tenant_id).all()}
    agent_ids = set(lead_by) | set(break_by) | set(first_seen)
    items = []
    for aid in agent_ids:
        s = lead_by.get(aid, {"handled": 0, "applications": 0, "appts": 0, "avg": 0})
        brk = break_by.get(aid, 0)
        fs = first_seen.get(aid)
        shift = int((now - fs).total_seconds()) if fs else 0
        billable = max(0, shift - brk)
        conv = round(s["applications"] / s["handled"] * 100) if s["handled"] else 0
        items.append(
            {
                "agent_name": _name(users.get(aid)),
                "shift_seconds": shift,
                "break_seconds": brk,
                "billable_seconds": billable,
                "applications": s["applications"],
                "appts": s["appts"],
                "avg_response_ms": s["avg"],
                "conv_pct": conv,
            }
        )
    items.sort(key=lambda x: x["agent_name"])
    return {"items": items}


def get_sold_tank(
    db: Session,
    tenant_id: str,
    search: str | None = None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    limit: int = 500,
) -> dict:
    """All-time SOLD leads (cumulative) — customer / phone / agent / sold date."""
    q = (
        db.query(SmsLead, User)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.disposition == "SALE")
    )
    if from_iso or to_iso:
        start, end = _range(from_iso, to_iso)
        q = q.filter(SmsLead.dispositioned_at >= start, SmsLead.dispositioned_at < end)
    if search and search.strip():
        like = f"%{search.strip()}%"
        q = q.filter((SmsLead.customer_name.ilike(like)) | (SmsLead.phone_number.ilike(like)))
    total = q.count()
    rows = q.order_by(SmsLead.dispositioned_at.desc().nullslast()).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": str(l.id),
                "customer_name": l.customer_name,
                "phone_number": l.phone_number,
                "agent_name": _name(u),
                "sold_date": l.dispositioned_at.isoformat() if l.dispositioned_at else None,
                "coverage": None,
            }
            for l, u in rows
        ],
    }
