"""Real-time mirror of inbound customer replies into the SMS human queue.

Called from the inbound choke points (Sinch webhook + 5s poll fallback) right
after the AI pipeline persists an inbound message, so the human SMS Queue/chat
updates instantly instead of waiting for the 60s batch auto-sync
(`ingest_positive_leads`), which never re-syncs an already-bridged lead.

Strictly additive and isolated:
- Uses its OWN DB session, so it can never disturb the caller's transaction.
- Wrapped end-to-end so any failure is swallowed (logged) — the AI inbound
  pipeline is never affected.
- Only ever writes the sms_* tables and emits realtime events.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Write-only Socket.IO emitter — publishes to the same Redis channel the async
# web server reads, so events reach connected browsers from any process.
_rt = None


def _emit(room: str, event: str, data: dict) -> None:
    global _rt
    try:
        if _rt is None:
            import socketio

            from app.core.config import settings

            _rt = socketio.Server(
                client_manager=socketio.RedisManager(settings.REDIS_URL, write_only=True)
            )
        _rt.emit(event, data, room=room)
    except Exception as exc:  # never let realtime break the mirror
        logger.warning("sms inbound mirror emit failed: %s", exc)


def _flush_events(events: list[dict]) -> None:
    """Flush queue_service (to, id, event, data) events to their socket rooms."""
    for e in events or []:
        to, _id, event, data = e.get("to"), e.get("id"), e.get("event"), e.get("data")
        if not _id:
            continue
        if to == "agent":
            _emit(f"agent:{_id}", event, data)
        elif to == "tenant":
            _emit(f"tenant:{_id}", event, data)


def mirror_inbound_to_sms(lead, conversation, body: str) -> dict:
    """Mirror one inbound reply into the SMS queue tables + emit live events.

    `lead` and `conversation` are ORM objects from the caller's session; we read
    their scalar fields up front, then do all work on a fresh session.
    """
    try:
        tenant_id = str(lead.tenant_id)
        lead_id = lead.id
        phone = lead.phone
        name = f"{getattr(lead, 'first_name', '') or ''} {getattr(lead, 'last_name', '') or ''}".strip()
        conversation_id = conversation.id
    except Exception as exc:
        logger.warning("sms inbound mirror: bad lead/conversation: %s", exc)
        return {"mirrored": "error"}

    from app.core.database import get_db

    db = next(get_db())
    try:
        from app.models.sms import SmsLead, SmsMessage

        sl = (
            db.query(SmsLead)
            .filter(SmsLead.tenant_id == tenant_id, SmsLead.lead_id == lead_id)
            .order_by(SmsLead.created_at.desc())
            .first()
        )

        # --- Ongoing conversation: append the reply so the chat updates now. ---
        if sl is not None:
            last = (
                db.query(SmsMessage)
                .filter(
                    SmsMessage.sms_lead_id == sl.id,
                    SmsMessage.direction == "INBOUND",
                )
                .order_by(SmsMessage.created_at.desc())
                .first()
            )
            # Idempotency: the webhook + poll both funnel through here; skip if the
            # latest inbound already carries this exact body.
            if last is not None and (last.body or "") == (body or ""):
                return {"mirrored": "duplicate_skipped"}

            msg = SmsMessage(
                tenant_id=tenant_id,
                sms_lead_id=sl.id,
                phone_number=phone,
                direction="INBOUND",
                body=body or "",
                sender_type="CUSTOMER",
                status="RECEIVED",
            )
            db.add(msg)
            sl.last_message = body
            sl.message_count = (sl.message_count or 0) + 1
            sl.updated_at = _now()
            # Route by the reply itself: an opt-out / profanity / scam message
            # parks the lead (out of the pool); ANY other reply belongs in the pool.
            # So a pooled lead that opts out is parked, and a parked-unqualified
            # lead that replies something workable is sent BACK to the pool — a live
            # reply is never stuck invisibly.
            from app.sms_queue.services.block_words import block_reason
            from app.sms_queue.services.queue_service import add_to_dnc, DNC_DISPOSITIONS
            blocked = block_reason(body)
            if blocked and sl.status == "QUEUED":
                sl.status = "DISPOSITIONED"
                sl.disposition = "UNQUALIFIED"
                sl.dispositioned_at = _now()
                add_to_dnc(db, tenant_id, phone, "UNQUALIFIED")
            elif (not blocked) and sl.status == "DISPOSITIONED" and (sl.disposition in DNC_DISPOSITIONS):
                # Re-engagement from a parked-unqualified lead -> back to the pool.
                sl.status = "QUEUED"
                sl.disposition = None
                sl.dispositioned_at = None
            db.commit()

            payload = {
                "id": str(msg.id),
                "sms_lead_id": str(sl.id),
                "phone_number": phone,
                "direction": "INBOUND",
                "body": body or "",
                "sender_type": "CUSTOMER",
                "status": "RECEIVED",
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            _emit(f"tenant:{tenant_id}", "sms:new_message", payload)
            # Also nudge the pool/manager views so the lead's last message and
            # ordering update instantly (they refresh on sms:queue_updated), not
            # just on the slow poll fallback.
            _emit(f"tenant:{tenant_id}", "sms:queue_updated", {"reason": "inbound_reply"})
            if sl.assigned_agent_id:
                _emit(f"agent:{sl.assigned_agent_id}", "sms:new_message", payload)
            return {"mirrored": "appended"}

        # --- New lead: bridge it instantly if the reply is positive intent. ---
        from app.intent.services.classifier import fast_classify
        from app.sms_queue.services.lead_ingest import (
            POSITIVE_INTENTS,
            PRIORITY_BY_INTENT,
            SENDER_TYPE,
            STATUS_MAP,
        )

        intent = fast_classify(body).intent if (body or "").strip() else None
        # Coverage rule: every reply enters the pool EXCEPT opt-out / profanity /
        # scam messages (hardcoded filter), which are parked as Unqualified below.
        # No positive-intent gate — a neutral/confused reply is still a real lead.
        from app.sms_queue.services.block_words import block_reason
        reason = block_reason(body)

        from app.models.message import Message

        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        # No DNC skip on inbound: a customer's reply must land in the pool (or in
        # Parked-Unqualified if it's an opt-out) — it must never vanish. The DNC
        # list only suppresses OUTBOUND (we don't re-text), not their own reply.
        from app.sms_queue.services.queue_service import add_to_dnc
        sl = SmsLead(
            tenant_id=tenant_id,
            lead_id=lead_id,
            phone_number=phone,
            customer_name=name or None,
            last_message=(msgs[-1].content if msgs else body),
            priority=PRIORITY_BY_INTENT.get(intent, "WARM"),
            status="QUEUED",
            message_count=len(msgs) or 1,
        )
        # Blocked message -> park as Unqualified (never enters the pool) + DNC.
        if reason is not None:
            sl.status = "DISPOSITIONED"
            sl.disposition = "UNQUALIFIED"
            sl.dispositioned_at = _now()
            add_to_dnc(db, tenant_id, phone, "UNQUALIFIED")
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
                    phone_number=phone,
                    direction=direction,
                    body=m.content or "",
                    sender_type=SENDER_TYPE.get(m.sender, "SYSTEM"),
                    status=status,
                    provider=getattr(m, "provider", None),
                    created_at=m.created_at,
                )
            )
        db.commit()
        _emit(f"tenant:{tenant_id}", "sms:queue_updated", {"reason": "inbound_mirror"})

        # Offer it immediately to the longest-idle available agent so the popup
        # fires right away — but only a real pool lead, never a blocked/parked one.
        if reason is None:
            try:
                from app.sms_queue.services import queue_service

                _data, events = queue_service.assign_next(db, tenant_id)
                _flush_events(events)
            except Exception as exc:
                logger.warning("sms inbound mirror assign failed: %s", exc)
        return {"mirrored": "blocked_unqualified" if reason else "created"}
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("sms inbound mirror failed: %s", exc)
        return {"mirrored": "error", "error": str(exc)}
    finally:
        db.close()
