"""Bridge: pull real Sinch-fed inbound leads into the SMS Queue.

Reads the existing leads / conversations / messages (populated by the Sinch
webhook + AI orchestrator) and mirrors inbound leads into sms_leads + their
history into sms_messages. Strictly additive — only WRITES to the new SMS
tables, never modifies the production lead/AI pipeline.

Idempotent: a lead already bridged (sms_leads.lead_id present) is skipped.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.intent.services.classifier import fast_classify
from app.intent.services.intents import Intent
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.models.sms import SmsLead, SmsMessage

# Only positive-intent replies are pulled into the human queue. The AI keeps
# running independently on every lead (broadcast + booking); this queue is the
# parallel human lane for the hot ones. Tunable.
POSITIVE_INTENTS = {Intent.POSITIVE, Intent.INTERESTED, Intent.BOOK_NOW}
# intent -> queue priority
PRIORITY_BY_INTENT = {
    Intent.BOOK_NOW: "HOT",
    Intent.POSITIVE: "HOT",
    Intent.INTERESTED: "WARM",
}

# message.sender -> sms_messages.sender_type
SENDER_TYPE = {"customer": "CUSTOMER", "agent": "AGENT", "ai": "SYSTEM", "system": "SYSTEM"}
# message.delivery_status -> sms_messages.status
STATUS_MAP = {
    "received": "RECEIVED",
    "queued": "SENT",
    "sent": "SENT",
    "delivered": "DELIVERED",
    "failed": "FAILED",
    "pending": "PENDING",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _priority(last_inbound_at: datetime | None) -> str:
    if not last_inbound_at:
        return "NORMAL"
    age = _now() - last_inbound_at
    if age <= timedelta(hours=1):
        return "HOT"
    if age <= timedelta(hours=24):
        return "WARM"
    return "NORMAL"


def ingest_inbound_leads(
    db: Session, tenant_id: str, since_hours: int = 168, limit: int = 1000
) -> dict:
    since = _now() - timedelta(hours=since_hours)

    # Conversations that have a recent inbound (customer) message.
    inbound_convos = (
        db.query(Message.conversation_id)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "customer",
            Message.created_at >= since,
        )
        .distinct()
        .subquery()
    )
    # Exclude leads already mirrored into the pool AT THE SQL LEVEL, and take the
    # OLDEST un-pooled conversation first. This is what lets the job drain a
    # backlog: on a reply spike (e.g. a blast day with 500+ replies) the `limit`
    # budget is spent only on NEW leads, and across runs it pages through the
    # whole backlog — instead of re-fetching the newest 200 (already pooled) and
    # never reaching the un-pooled tail, which then ages out of the window and is
    # lost forever. Oldest-first also prioritises the leads closest to aging out.
    bridged = exists().where(
        (SmsLead.tenant_id == tenant_id) & (SmsLead.lead_id == Conversation.lead_id)
    )
    convos = (
        db.query(Conversation)
        .filter(
            Conversation.tenant_id == tenant_id,
            Conversation.id.in_(inbound_convos.select()),
            ~bridged,
        )
        .order_by(Conversation.last_message_at.asc().nullsfirst())
        .limit(limit)
        .all()
    )

    # In-run dedup only — SQL already excludes previously-pooled leads. This just
    # guards the rare case of one lead having two un-pooled conversations in this
    # same batch, so we don't create two sms_leads for it.
    already: set[str] = set()

    ingested = 0
    skipped_not_positive = 0
    skipped_dnc = 0
    blocked = 0
    from app.sms_queue.services.queue_service import add_to_dnc
    from app.sms_queue.services.block_words import block_reason
    for convo in convos:
        if str(convo.lead_id) in already:
            continue
        lead = db.query(Lead).filter(Lead.id == convo.lead_id).first()
        if not lead:
            continue

        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == convo.id)
            .order_by(Message.created_at.asc())
            .all()
        )
        inbound = [m for m in msgs if m.sender == "customer"]
        last_inbound = inbound[-1] if inbound else None

        # Coverage rule: pool every lead that REPLIED, except opt-out / profanity /
        # scam messages (parked as Unqualified below). No positive-intent gate — a
        # neutral/confused reply is still a real lead; only a NO reply is skipped.
        if not last_inbound:
            skipped_not_positive += 1
            continue
        intent = fast_classify(last_inbound.content).intent if (last_inbound.content or "").strip() else None
        reason = block_reason(last_inbound.content)
        # No DNC skip on inbound: a replied lead lands in the pool (or Parked-
        # Unqualified if it's an opt-out), never nowhere. DNC suppresses OUTBOUND only.

        last_inbound_at = last_inbound.created_at if last_inbound else None
        last_msg = msgs[-1].content if msgs else None
        name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()

        sl = SmsLead(
            tenant_id=tenant_id,
            lead_id=lead.id,
            phone_number=lead.phone,
            customer_name=name or None,
            last_message=last_msg,
            priority=PRIORITY_BY_INTENT.get(intent, _priority(last_inbound_at)),
            status="QUEUED",
            message_count=len(msgs),
        )
        # Blocked message -> park as Unqualified (never enters the pool) + DNC.
        if reason is not None:
            sl.status = "DISPOSITIONED"
            sl.disposition = "UNQUALIFIED"
            sl.dispositioned_at = _now()
            add_to_dnc(db, tenant_id, lead.phone, "UNQUALIFIED")
        db.add(sl)
        db.flush()

        for m in msgs:
            direction = "INBOUND" if m.sender == "customer" else "OUTBOUND"
            status = STATUS_MAP.get(
                (m.delivery_status or "").lower(),
                "RECEIVED" if direction == "INBOUND" else "SENT",
            )
            db.add(
                SmsMessage(
                    tenant_id=tenant_id,
                    sms_lead_id=sl.id,
                    phone_number=lead.phone,
                    direction=direction,
                    body=m.content or "",
                    sender_type=SENDER_TYPE.get(m.sender, "SYSTEM"),
                    status=status,
                    provider=m.provider,
                    created_at=m.created_at,
                )
            )
        already.add(str(lead.id))
        if reason is not None:
            blocked += 1
        else:
            ingested += 1

    db.commit()
    return {
        "ingested": ingested,
        "scanned": len(convos),
        "skipped_not_positive": skipped_not_positive,
        "skipped_dnc": skipped_dnc,
        "blocked": blocked,
    }
