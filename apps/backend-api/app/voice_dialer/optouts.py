"""Auto Dialer → Opted out: the tenant's Do-Not-Call list, readable.

Every number here is never called or texted again (the dialer, the regular
pool and CSV uploads all check `sms_do_not_call` via queue_service.is_dnc).
This module only shows the list — who, when, why, what they last said — and
lets an admin add a number by hand or take one off after a mistake.

A real STOP (reason OPTED_OUT) can be removed too, but only with an explicit
`confirm_stop`: the law says that number may not be contacted again until the
client opts back in (e.g. texts START / YES), so the UI asks first.
"""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.sms import SmsDoNotCall, SmsLead, SmsMessage
from app.sms_queue.services import queue_service as qs
from app.voice_dialer import replies
from app.voice_dialer.service import _in_period, period_bounds, PERIODS

# Reason on the row -> the filter group it belongs to.
GROUPS = {
    "stop": ("OPTED_OUT",),
    "not_interested": ("NOT_INTERESTED",),
    "wrong_number": ("WRONG_NUMBER",),
    "abusive": ("ABUSIVE",),
}
FILTERS = ("all", "stop", "not_interested", "abusive", "wrong_number", "other")
_KNOWN = tuple(r for rs in GROUPS.values() for r in rs)


def _filter(q, kind: str):
    if kind in GROUPS:
        return q.filter(SmsDoNotCall.reason.in_(GROUPS[kind]))
    if kind == "other":
        return q.filter(or_(SmsDoNotCall.reason.is_(None), SmsDoNotCall.reason.notin_(_KNOWN)))
    return q


def _period(q, start, end):
    if start is not None:
        q = q.filter(SmsDoNotCall.created_at >= start)
    if end is not None:
        q = q.filter(SmsDoNotCall.created_at < end)
    return q


def listing(db: Session, tenant_id, kind: str = "all", q: str | None = None, period: str = "all",
            date_from: str | None = None, date_to: str | None = None,
            before: str | None = None, limit: int = 30) -> dict:
    from datetime import datetime

    kind = kind if kind in FILTERS else "all"
    limit = max(1, min(int(limit or 30), 200))
    start, end = period_bounds(period if period in PERIODS else "all", date_from, date_to)
    base = _period(db.query(SmsDoNotCall).filter(SmsDoNotCall.tenant_id == tenant_id), start, end)

    text = (q or "").strip()
    if text:
        digits = "".join(c for c in text if c.isdigit())
        conds = []
        if digits:
            conds.append(SmsDoNotCall.phone_number.contains(digits))
        # Name search: numbers whose lead carries that name.
        named = [
            replies.last10(p) for (p,) in db.query(SmsLead.phone_number)
            .filter(SmsLead.tenant_id == tenant_id, SmsLead.customer_name.ilike(f"%{text}%"))
            .limit(500)
            .all()
        ]
        named = [n for n in named if n]
        if named:
            conds.append(func.right(SmsDoNotCall.phone_number, 10).in_(named))
        base = base.filter(or_(*conds)) if conds else base.filter(False)

    rows_q = _filter(base, kind)
    if before:
        try:
            rows_q = rows_q.filter(SmsDoNotCall.created_at < datetime.fromisoformat(before.replace("Z", "+00:00")))
        except ValueError:
            pass
    rows = rows_q.order_by(SmsDoNotCall.created_at.desc()).limit(limit + 1).all()
    counts = {k: _filter(base.with_entities(func.count(SmsDoNotCall.id)), k).scalar() or 0 for k in FILTERS}
    return {"items": [_row(db, tenant_id, r) for r in rows[:limit]], "has_more": len(rows) > limit, "counts": counts}


def _row(db: Session, tenant_id, r: SmsDoNotCall) -> dict:
    from app.voice_dialer.service import _phone_match

    lead = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, _phone_match(SmsLead.phone_number, r.phone_number))
        .order_by(SmsLead.created_at.desc())
        .first()
    )
    said = None
    if lead is not None:
        m = (
            db.query(SmsMessage.body)
            .filter(SmsMessage.sms_lead_id == lead.id, SmsMessage.direction == "INBOUND")
            .order_by(SmsMessage.created_at.desc())
            .first()
        )
        said = m[0] if m else None
    return {
        "id": str(r.id),
        "phone_number": r.phone_number,
        "reason": r.reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "customer_name": lead.customer_name if lead else None,
        "lead_id": str(lead.id) if lead else None,
        "last_text": said,
    }


def count_in_period(db: Session, tenant_id, start, end) -> int:
    q = _period(db.query(func.count(SmsDoNotCall.id)).filter(SmsDoNotCall.tenant_id == tenant_id), start, end)
    return int(q.scalar() or 0)


def add(db: Session, tenant_id, phone: str, reason: str | None = None) -> dict:
    """Admin blocks a number by hand."""
    norm = replies.normalize_phone(phone)
    if not norm:
        raise ValueError("Enter a valid US phone number")
    if qs.is_dnc(db, tenant_id, norm):
        return {"ok": True, "already": True}
    qs.add_to_dnc(db, tenant_id, norm, (reason or "MANUAL")[:40])
    # Anyone waiting to be auto-called stops waiting.
    for lead in (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status == "READY_TO_CALL")
        .all()
    ):
        if replies.last10(lead.phone_number) == replies.last10(norm):
            lead.status = "DISPOSITIONED"
            lead.disposition = lead.disposition or "UNQUALIFIED"
    db.commit()
    return {"ok": True}


def remove(db: Session, tenant_id, dnc_id: str, confirm_stop: bool = False) -> dict:
    """Take a number off the list (a mistake, or the client opted back in).
    Nothing is re-dialed by this alone — they are called again only after a
    new YES."""
    row = db.query(SmsDoNotCall).filter(SmsDoNotCall.id == dnc_id, SmsDoNotCall.tenant_id == tenant_id).first()
    if row is None:
        return {"ok": False, "reason": "not_found"}
    if row.reason == "OPTED_OUT" and not confirm_stop:
        return {"ok": False, "reason": "needs_confirm"}
    out = {"ok": True, "phone_number": row.phone_number, "reason": row.reason}
    db.delete(row)
    db.commit()
    return out
