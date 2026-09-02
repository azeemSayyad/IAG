"""Read-only aggregation for the SMS Monitoring dashboard.

Pure queries over the SMS tables — no side effects, safe to poll frequently.
"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.sms import SmsLead, SmsMessage, SmsPollLog, SmsQueueAgent

ONLINE_STATUSES = ("AVAILABLE", "ON_CALL", "AWAY")

# Process start, for the System Health uptime readout.
_START_TS = datetime.now(timezone.utc)
_VERSION = (
    os.getenv("GIT_SHA")
    or os.getenv("RAILWAY_GIT_COMMIT_SHA")
    or os.getenv("APP_VERSION")
    or "local-dev"
)


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def get_stats(db: Session, tenant_id: str) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    # --- Outbound delivery (last 24h) ---
    out_rows = (
        db.query(SmsMessage.status, func.count(SmsMessage.id))
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "OUTBOUND",
            SmsMessage.created_at >= since,
        )
        .group_by(SmsMessage.status)
        .all()
    )
    out = {status: count for status, count in out_rows}
    sent = sum(out.values())
    delivered = out.get("DELIVERED", 0)
    failed = out.get("FAILED", 0)

    # --- Texts being sent right now (in-flight) + lifetime sent ---
    sending_now = (
        db.query(func.count(SmsMessage.id))
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "OUTBOUND",
            SmsMessage.status == "PENDING",
        )
        .scalar()
        or 0
    )
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        db.query(func.count(SmsMessage.id))
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "OUTBOUND",
            SmsMessage.created_at >= today,
        )
        .scalar()
        or 0
    )
    sent_all_time = (
        db.query(func.count(SmsMessage.id))
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "OUTBOUND",
        )
        .scalar()
        or 0
    )

    # --- Polling reliability (last 24h) ---
    poll_rows = (
        db.query(SmsPollLog.succeeded, func.count(SmsPollLog.id))
        .filter(SmsPollLog.attempted_at >= since)
        .group_by(SmsPollLog.succeeded)
        .all()
    )
    polls = {bool(ok): count for ok, count in poll_rows}
    polls_ok = polls.get(True, 0)
    polls_attempted = polls_ok + polls.get(False, 0)

    # --- Queue + agents (live) ---
    queued = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .scalar()
        or 0
    )
    oldest_queued = (
        db.query(func.min(SmsLead.created_at))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "QUEUED")
        .scalar()
    )
    oldest_age_s = int((now - oldest_queued).total_seconds()) if oldest_queued else 0

    agent_rows = (
        db.query(SmsQueueAgent.status, func.count(SmsQueueAgent.id))
        .filter(SmsQueueAgent.tenant_id == tenant_id)
        .group_by(SmsQueueAgent.status)
        .all()
    )
    agents = {status: count for status, count in agent_rows}
    agents_online = sum(agents.get(s, 0) for s in ONLINE_STATUSES)

    # --- System health ---
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {
        "polling": {
            "success_rate_pct": _pct(polls_ok, polls_attempted),
            "last24h_polls_succeeded": polls_ok,
            "last24h_polls_attempted": polls_attempted,
        },
        "outbound": {
            # succeeded (anything not FAILED) / total outbound
            "success_rate_pct": _pct(sent - failed, sent),
            "last24h_messages_sent": sent,
            "last24h_messages_delivered": delivered,
            "last24h_messages_failed": failed,
            # #9 — texts being sent now + how many have been sent
            "sending_now": int(sending_now),
            "sent_today": int(sent_today),
            "sent_all_time": int(sent_all_time),
        },
        "queue": {
            "current_queued": queued,
            "agents_online": agents_online,
            "agents_available": agents.get("AVAILABLE", 0),
            "agents_on_call": agents.get("ON_CALL", 0),
            "agents_away": agents.get("AWAY", 0),
            "agents_on_break": agents.get("AWAY", 0),
            "oldest_queued_age_seconds": oldest_age_s,
        },
        "health": {
            "backend_uptime_ms": int((now - _START_TS).total_seconds() * 1000),
            "db_status": db_status,
            "version": _VERSION,
        },
    }


def get_time_series(db: Session, tenant_id: str) -> dict:
    """Hourly buckets for the last 24h."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    hour = func.date_trunc("hour", SmsMessage.created_at)

    msg_rows = (
        db.query(hour.label("h"), SmsMessage.direction, func.count(SmsMessage.id))
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.created_at >= since)
        .group_by("h", SmsMessage.direction)
        .all()
    )
    poll_hour = func.date_trunc("hour", SmsPollLog.attempted_at)
    poll_rows = (
        db.query(poll_hour.label("h"), SmsPollLog.succeeded, func.count(SmsPollLog.id))
        .filter(SmsPollLog.attempted_at >= since)
        .group_by("h", SmsPollLog.succeeded)
        .all()
    )

    buckets: dict[str, dict] = {}
    for i in range(24):
        slot = (since + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        buckets[slot.isoformat()] = {
            "hour": slot.isoformat(),
            "inbound": 0,
            "outbound": 0,
            "polls_ok": 0,
            "polls_fail": 0,
        }

    def _key(dt: datetime) -> str:
        return dt.replace(minute=0, second=0, microsecond=0).isoformat()

    for h, direction, count in msg_rows:
        b = buckets.get(_key(h))
        if not b:
            continue
        b["inbound" if direction == "INBOUND" else "outbound"] += count

    for h, ok, count in poll_rows:
        b = buckets.get(_key(h))
        if not b:
            continue
        b["polls_ok" if ok else "polls_fail"] += count

    return {"points": list(buckets.values())}


def get_recent_failures(db: Session, tenant_id: str, limit: int = 20) -> dict:
    failed_msgs = (
        db.query(SmsMessage)
        .filter(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.direction == "OUTBOUND",
            SmsMessage.status == "FAILED",
        )
        .order_by(SmsMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    failed_polls = (
        db.query(SmsPollLog)
        .filter(SmsPollLog.succeeded.is_(False))
        .order_by(SmsPollLog.attempted_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "failed_outbound_messages": [
            {
                "id": str(m.id),
                "phoneNumber": m.phone_number,
                "message": m.body,
                "error": m.error_message or m.error_code,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m in failed_msgs
        ],
        "failed_polls": [
            {
                "id": str(p.id),
                "error_message": p.error,
                "attempted_at": p.attempted_at.isoformat() if p.attempted_at else None,
                "duration_ms": p.duration_ms,
            }
            for p in failed_polls
        ],
    }


def get_pulse_events(db: Session, tenant_id: str, minutes: int = 10) -> dict:
    """Recent activity blips for the live ECG strip: polls + inbound/outbound."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    events: list[dict] = []

    polls = (
        db.query(SmsPollLog)
        .filter(SmsPollLog.attempted_at >= since)
        .order_by(SmsPollLog.attempted_at.desc())
        .limit(100)
        .all()
    )
    for p in polls:
        events.append(
            {
                "id": f"poll-{p.id}",
                "type": "poll_success" if p.succeeded else "poll_fail",
                "at": p.attempted_at.isoformat(),
            }
        )

    msgs = (
        db.query(SmsMessage)
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.created_at >= since)
        .order_by(SmsMessage.created_at.desc())
        .limit(100)
        .all()
    )
    for m in msgs:
        events.append(
            {
                "id": f"msg-{m.id}",
                "type": "inbound" if m.direction == "INBOUND" else "outbound",
                "at": m.created_at.isoformat(),
            }
        )

    events.sort(key=lambda e: e["at"])
    return {"events": events}
