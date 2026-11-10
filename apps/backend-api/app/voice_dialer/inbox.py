"""Auto Dialer → Inbox: one conversation per PHONE NUMBER, latest activity
first — everyone a text campaign reached (replied or not), everyone who texted
our numbers, and everyone we called. A thread mixes their texts, our campaign
texts, the one-to-one texts sent from here (send_text) and our calls (outcome,
agent, talk time, recording).

Keyed by phone, not lead: a campaign contact who never replied has no lead.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, literal, or_, union_all
from sqlalchemy.orm import Session

from app.models.sms import SmsLead, SmsMessage, TextCampaignRecipient, VoiceCallAttempt
from app.voice_dialer import replies, telnyx

# What a lead's status means here, in plain words.
STAGE = {
    "READY_TO_CALL": "Waiting to call",
    "DIALING": "Calling now",
    "IN_PROGRESS": "With an agent",
    "QUEUED": "In regular pool",
    "ASSIGNED": "Offered to an agent",
    "DISPOSITIONED": "Closed",
}
MAX_TEXT = 1000


# "vonage" is the previous carrier: rows it wrote stay part of each client's history.
DIALER_PROVIDERS = ("vonage", "telnyx")


class InboxError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _stage(l: SmsLead | None) -> str:
    if l is None:
        return "Texted · no reply"
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


def _activity(tenant_id):
    """Every (phone, at, body, direction) the inbox knows about, as one subquery."""
    texts = (
        SmsMessage.__table__.select()
        .with_only_columns(SmsMessage.phone_number.label("phone"), SmsMessage.created_at.label("at"),
                           SmsMessage.body.label("body"), SmsMessage.direction.label("direction"))
        .where(SmsMessage.tenant_id == tenant_id, SmsMessage.provider.in_(DIALER_PROVIDERS))
    )
    campaign = (
        TextCampaignRecipient.__table__.select()
        .with_only_columns(TextCampaignRecipient.phone_number.label("phone"), TextCampaignRecipient.sent_at.label("at"),
                           TextCampaignRecipient.body.label("body"), literal("OUTBOUND").label("direction"))
        .where(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.sent_at.isnot(None))
    )
    calls = (
        VoiceCallAttempt.__table__.select()
        .with_only_columns(VoiceCallAttempt.phone_number.label("phone"), VoiceCallAttempt.created_at.label("at"),
                           literal(None).label("body"), literal("CALL").label("direction"))
        .where(VoiceCallAttempt.tenant_id == tenant_id)
    )
    return union_all(texts, campaign, calls).subquery()


def conversations(db: Session, tenant_id, q: str | None = None, before: str | None = None,
                  limit: int = 60) -> dict:
    a = _activity(tenant_id)
    per_phone = (
        db.query(a.c.phone, func.max(a.c.at).label("last_at"),
                 func.count().filter(a.c.direction != "CALL").label("texts"),
                 func.count().filter(a.c.direction == "INBOUND").label("inbound"))
        .group_by(a.c.phone)
    )
    term = (q or "").strip()
    if term:
        digits = "".join(c for c in term if c.isdigit())
        named = {p for (p,) in db.query(SmsLead.phone_number).filter(
            SmsLead.tenant_id == tenant_id, SmsLead.customer_name.ilike(f"%{term}%")).limit(500)}
        named |= {p for (p,) in db.query(TextCampaignRecipient.phone_number).filter(
            TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.first_name.ilike(f"%{term}%")).limit(500)}
        cond = a.c.phone.in_(list(named)) if named else literal(False)
        if len(digits) >= 3:
            cond = or_(cond, a.c.phone.like(f"%{digits}%"))
        per_phone = per_phone.filter(cond)
    sub = per_phone.subquery()
    query = db.query(sub)
    if before:
        query = query.filter(sub.c.last_at < before)
    limit = max(1, min(int(limit or 60), 200))
    rows = query.order_by(sub.c.last_at.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    phones = [r.phone for r in rows]

    leads: dict = {}
    if phones:
        for l in (db.query(SmsLead).filter(SmsLead.tenant_id == tenant_id, SmsLead.phone_number.in_(phones))
                  .order_by(SmsLead.created_at.asc()).all()):
            leads[l.phone_number] = l  # newest wins
    names: dict = {}
    if phones:
        for p, n in (db.query(TextCampaignRecipient.phone_number, TextCampaignRecipient.first_name)
                     .filter(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number.in_(phones),
                             TextCampaignRecipient.first_name.isnot(None)).all()):
            names[p] = n
    last_body: dict = {}
    if phones:
        for p, body, direction in (db.query(a.c.phone, a.c.body, a.c.direction)
                                   .filter(a.c.phone.in_(phones), a.c.body.isnot(None))
                                   .order_by(a.c.at.asc()).all()):
            last_body[p] = (body, direction)  # ascending -> the last one wins
    last_call: dict = {}
    if phones:
        for c in (db.query(VoiceCallAttempt).filter(VoiceCallAttempt.tenant_id == tenant_id,
                                                    VoiceCallAttempt.phone_number.in_(phones))
                  .order_by(VoiceCallAttempt.created_at.asc()).all()):
            last_call[c.phone_number] = c
    items = []
    for r in rows:
        l = leads.get(r.phone)
        body, direction = last_body.get(r.phone, (None, None))
        call = last_call.get(r.phone)
        items.append({
            "phone_number": r.phone,
            "lead_id": str(l.id) if l else None,
            "customer_name": (l.customer_name if l and l.customer_name and not l.customer_name.startswith("Unknown") else None)
            or names.get(r.phone) or (l.customer_name if l else None),
            "last_message": body,
            "last_from_us": direction == "OUTBOUND",
            "last_at": _iso(r.last_at),
            "texts": int(r.texts or 0),
            "replied": int(r.inbound or 0) > 0,
            "stage": _stage(l),
            "disposition": l.disposition if l else None,
            "last_call_status": call.status if call else None,
        })
    total = db.query(func.count()).select_from(per_phone.subquery()).scalar() if not before else None
    return {"items": items, "has_more": more, "total": total}


def _phone_for(db: Session, tenant_id, key: str) -> str | None:
    """A thread key is a phone number (or, from older links, a lead id)."""
    try:
        lid = uuid.UUID(str(key))
    except (ValueError, TypeError):
        return replies.normalize_phone(key)
    lead = db.query(SmsLead).filter(SmsLead.id == lid, SmsLead.tenant_id == tenant_id).first()
    return lead.phone_number if lead else None


def thread(db: Session, tenant_id, key) -> dict | None:
    from app.voice_dialer.campaigns import texts_for_phone
    from app.voice_dialer.service import find_lead, lead_state

    phone = _phone_for(db, tenant_id, key)
    if not phone:
        return None
    lead = find_lead(db, tenant_id, phone)
    cond = SmsMessage.phone_number == phone
    if lead is not None:
        cond = or_(cond, SmsMessage.sms_lead_id == lead.id)
    msgs = db.query(SmsMessage).filter(SmsMessage.tenant_id == tenant_id, cond).order_by(SmsMessage.created_at.asc()).all()
    calls = (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.tenant_id == tenant_id,
                or_(VoiceCallAttempt.phone_number == phone,
                    VoiceCallAttempt.sms_lead_id == lead.id if lead is not None else literal(False)))
        .order_by(VoiceCallAttempt.created_at.asc())
        .all()
    )
    campaign = texts_for_phone(db, tenant_id, phone)
    if lead is None and not msgs and not calls and not campaign:
        return None
    names = _user_names(db, [c.agent_user_id for c in calls] + [m.agent_id for m in msgs])
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
            "sent_by": names.get(m.agent_id) if m.direction == "OUTBOUND" else None,
            "delivery": m.status if m.direction == "OUTBOUND" else None,
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
    ] + [
        {"kind": "text", "at": t["at"], "direction": "OUTBOUND", "body": t["body"], "consent": None,
         "campaign": t["campaign"], "delivery": t["status"]}
        for t in campaign
    ]
    items.sort(key=lambda i: i["at"] or "")
    from app.sms_queue.services import queue_service as qs

    name = lead.customer_name if lead and lead.customer_name and not lead.customer_name.startswith("Unknown") else None
    if not name:
        name = (
            db.query(TextCampaignRecipient.first_name)
            .filter(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number == phone,
                    TextCampaignRecipient.first_name.isnot(None))
            .limit(1).scalar()
        ) or (lead.customer_name if lead else None)
    return {
        "phone_number": phone,
        "lead_id": str(lead.id) if lead else None,
        "customer_name": name,
        "state": lead_state(db, lead) if lead else None,
        "stage": _stage(lead),
        "disposition": lead.disposition if lead else None,
        "opted_out": qs.is_dnc(db, str(tenant_id), phone),
        "items": items,
    }


# ---- One-to-one text from the inbox ----------------------------------------------------

def sender_for(db: Session, tenant_id, phone: str) -> str | None:
    """Reply from the number they already know: their lead's, else the one
    their campaign text came from, else the campaign's stable pick."""
    from app.voice_dialer import campaigns
    from app.voice_dialer.service import find_lead

    numbers = campaigns.senders()
    if not numbers:
        return None
    lead = find_lead(db, tenant_id, phone)
    last = (
        db.query(TextCampaignRecipient.from_number)
        .filter(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number == phone,
                TextCampaignRecipient.from_number.isnot(None))
        .order_by(TextCampaignRecipient.sent_at.desc()).limit(1).scalar()
    )
    for known in (lead.dial_from if lead else None, last):
        if known in numbers:
            return known
    return campaigns.sender_for(phone, numbers)


def send_text(db: Session, tenant_id, user, key: str, text: str) -> dict:
    """Send one custom text to one person, from the inbox thread."""
    from app.sms_queue.services import queue_service as qs
    from app.voice_dialer import campaigns
    from app.voice_dialer.service import _url, find_lead

    phone = _phone_for(db, tenant_id, key)
    if not phone or not phone.startswith("+1"):
        raise InboxError("Texts go to US numbers only")
    body = (text or "").strip()
    if not body:
        raise InboxError("Write a message first")
    if len(body) > MAX_TEXT:
        raise InboxError(f"Keep it under {MAX_TEXT} characters")
    if qs.is_dnc(db, str(tenant_id), phone):
        raise InboxError("They opted out (Do-Not-Call) — texting them is not allowed", 409)
    if not telnyx.simulating() and not telnyx.available():
        raise InboxError("Texting is not set up (Telnyx is not configured)", 503)
    if campaigns.sent_today(db, tenant_id) >= campaigns.daily_cap():
        raise InboxError("Today's texting limit is reached (carrier daily cap) — try again tomorrow", 429)
    frm = sender_for(db, tenant_id, phone)
    if not frm:
        raise InboxError("No sending number — set TELNYX_FROM_NUMBERS", 503)
    try:
        mid = telnyx.send_sms(from_=frm, to=phone, text=body, webhook_url=_url("sms-status"))
    except telnyx.SendError as exc:
        raise InboxError(f"Not sent: {exc}", 502)
    lead = find_lead(db, tenant_id, phone)
    m = SmsMessage(
        tenant_id=tenant_id, sms_lead_id=lead.id if lead else None, phone_number=phone, direction="OUTBOUND",
        body=body, sender_type="AGENT", agent_id=getattr(user, "id", None),
        status="DELIVERED" if telnyx.simulating() else "SENT", provider=telnyx.SLUG, provider_message_id=mid,
    )
    db.add(m)
    if lead is not None and not lead.dial_from:
        lead.dial_from = frm
    db.commit()
    return {"ok": True, "from_number": frm, "at": _iso(m.created_at)}
