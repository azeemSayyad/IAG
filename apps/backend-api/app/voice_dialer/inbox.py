"""Auto Dialer → Inbox: every client who texted our Vonage numbers, newest first,
and one timeline per client mixing their texts with our calls (outcome, agent,
talk time, recording). Read-only — the outreach texts are sent from Vonage, not
from the portal, so the thread holds what came IN plus what we did about it.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.sms import SmsLead, SmsMessage, VoiceCallAttempt

# What a lead's status means here, in plain words.
STAGE = {
    "READY_TO_CALL": "Waiting to call",
    "DIALING": "Calling now",
    "IN_PROGRESS": "With an agent",
    "QUEUED": "In regular pool",
    "ASSIGNED": "Offered to an agent",
    "DISPOSITIONED": "Closed",
}


def _stage(l: SmsLead) -> str:
    if not str(l.phone_number or "").startswith("+1"):
        return "Text only · not a US number"  # saved, never dialed
    return STAGE.get(l.status, l.status.title())


def _iso(d):
    return d.isoformat() if d else None


def _user_names(db: Session, ids) -> dict:
    from app.models.user import User

    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {
        u.id: (f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email)
        for u in db.query(User).filter(User.id.in_(ids)).all()
    }


def conversations(db: Session, tenant_id, limit: int = 80) -> list[dict]:
    texts = (
        db.query(
            SmsMessage.sms_lead_id.label("lead_id"),
            func.max(SmsMessage.created_at).label("last_at"),
            func.count(SmsMessage.id).label("n"),
        )
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.provider == "vonage", SmsMessage.sms_lead_id.isnot(None))
        .group_by(SmsMessage.sms_lead_id)
        .subquery()
    )
    rows = (
        db.query(SmsLead, texts.c.last_at, texts.c.n)
        .join(texts, texts.c.lead_id == SmsLead.id)
        .order_by(texts.c.last_at.desc())
        .limit(limit)
        .all()
    )
    lead_ids = [l.id for l, _a, _n in rows]
    last_call: dict = {}
    if lead_ids:
        for a in (
            db.query(VoiceCallAttempt)
            .filter(VoiceCallAttempt.sms_lead_id.in_(lead_ids))
            .order_by(VoiceCallAttempt.created_at.asc())
            .all()
        ):
            last_call[a.sms_lead_id] = a  # ascending -> the last one wins
    out = []
    for l, last_at, n in rows:
        call = last_call.get(l.id)
        latest = max([d for d in (last_at, call.created_at if call else None) if d])
        out.append({
            "lead_id": str(l.id),
            "customer_name": l.customer_name,
            "phone_number": l.phone_number,
            "last_message": l.last_message,
            "last_at": _iso(latest),
            "texts": int(n or 0),
            "stage": _stage(l),
            "disposition": l.disposition,
            "last_call_status": call.status if call else None,
        })
    out.sort(key=lambda c: c["last_at"] or "", reverse=True)
    return out


def thread(db: Session, tenant_id, lead_id) -> dict | None:
    from app.voice_dialer.service import lead_state

    lead = db.query(SmsLead).filter(SmsLead.id == lead_id, SmsLead.tenant_id == tenant_id).first()
    if lead is None:
        return None
    msgs = (
        db.query(SmsMessage)
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.sms_lead_id == lead.id)
        .order_by(SmsMessage.created_at.asc())
        .all()
    )
    calls = (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.sms_lead_id == lead.id)
        .order_by(VoiceCallAttempt.created_at.asc())
        .all()
    )
    names = _user_names(db, [c.agent_user_id for c in calls])
    stored = {c.recording_id for c in calls if c.recording_id}
    items = [
        {
            "kind": "text",
            "at": _iso(m.created_at),
            "direction": m.direction,
            "body": m.body,
            # OPT_OUT = this text put them on Do-Not-Call; OPT_IN = this text
            # (a clear yes after that) took them back off — the consent record.
            "consent": {"OPT_OUT": "opted_out", "OPT_IN": "opted_in"}.get(m.status),
        }
        for m in msgs
    ] + [
        {
            "kind": "call",
            "at": _iso(c.created_at),
            "attempt_id": str(c.id),
            "status": c.status,
            "disposition": c.disposition,
            "direction": c.direction,
            "agent_name": names.get(c.agent_user_id),
            "duration_seconds": c.duration_seconds if c.connected_at else None,
            "has_recording": c.recording_id in stored,
        }
        for c in calls
    ]
    items.sort(key=lambda i: i["at"] or "")
    return {
        "lead_id": str(lead.id),
        "customer_name": lead.customer_name,
        "phone_number": lead.phone_number,
        "state": lead_state(db, lead),
        "stage": _stage(lead),
        "disposition": lead.disposition,
        "items": items,
    }
