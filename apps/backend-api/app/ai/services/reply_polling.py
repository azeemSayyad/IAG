"""No-webhook inbound reply polling.

This is the fallback path for providers/accounts that do not have inbound
webhooks enabled yet. It polls the provider reply API, sends each reply through
the normal AI conversation orchestrator, persists all messages/appointments,
emits realtime events, and confirms replies with the provider after success.
"""

import asyncio
import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.ai.services.communication_provider import communication_service
from app.ai.services.orchestrator import process_incoming_message
from app.core.redis import redis_service
from app.ingestion.services.events import on_lead_replied
from app.ingestion.services.validation import normalize_phone
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.realtime.websocket import emit_to_tenant

logger = logging.getLogger(__name__)


def _emit_to_tenant_safe(tenant_id: str, event: str, payload: Dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emit_to_tenant(tenant_id, event, payload))
    except RuntimeError:
        try:
            asyncio.run(emit_to_tenant(tenant_id, event, payload))
        except Exception as exc:
            logger.warning("Failed to emit %s during reply polling: %s", event, exc)
    except Exception as exc:
        logger.warning("Failed to schedule %s during reply polling: %s", event, exc)


def _find_lead_by_reply_phone(db: Session, from_number: str) -> Lead | None:
    # Format-agnostic matching (see app.ai.services.inbound_lead) so replies from
    # leads stored in odd formats / with a NULL phone_normalized still match —
    # including ones that arrive a day or more after the lead was uploaded.
    from app.ai.services.inbound_lead import find_lead_for_inbound

    return find_lead_for_inbound(db, from_number)


def _get_or_create_conversation(db: Session, lead: Lead) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.lead_id == lead.id,
            Conversation.status.in_(["active", "initiated", "booking"]),
        )
        .first()
    )
    if conversation:
        return conversation

    conversation = Conversation(
        tenant_id=lead.tenant_id,
        lead_id=lead.id,
        status="active",
    )
    db.add(conversation)
    db.flush()
    return conversation


def poll_provider_replies_once(db: Session, service=None) -> Dict[str, Any]:
    """Poll provider replies once and process them through the AI flow.

    `service` selects which provider account to poll — defaults to the Sinch
    `communication_service` (unchanged); pass `engage2_service` to poll the 2nd
    provider. Both route replies into the same lead pipeline."""
    service = service or communication_service
    fetched = service.fetch_replies()
    if not fetched.get("success"):
        return {
            "processed": 0,
            "confirmed": 0,
            "failed": 1,
            "skipped": 0,
            "available": 0,
            "error": fetched.get("error"),
            "status_code": fetched.get("status_code"),
        }

    processed = 0
    failed = 0
    skipped = 0
    confirm_ids: list[str] = []

    for reply in fetched.get("replies", []):
        reply_id = reply.get("reply_id")
        from_number = reply.get("from")
        body = reply.get("body")
        if not reply_id or not from_number or not body:
            skipped += 1
            continue

        # Short-lived LOCK so two overlapping polls don't grab the same reply at
        # once — NOT a 24h dedup. The durable dedup is confirm_replies() removing
        # the reply from /replies after success. A long TTL here caused replies to
        # get STUCK: if a poll was interrupted after locking but before confirming
        # (e.g. a deploy restart), the reply stayed in /replies AND stayed locked,
        # so every later poll skipped it for 24h. 300s lets an interrupted reply
        # re-process within minutes.
        replay_key = f"{getattr(service, '_provider', 'sinch')}:reply:{reply_id}"
        try:
            first_seen = redis_service.client.set(replay_key, "processing", nx=True, ex=300)
        except Exception:
            first_seen = True
        if not first_seen:
            skipped += 1
            continue

        # Cross-path dedup with the webhook: if the webhook already handled this
        # exact reply, don't process it again — but DO confirm it to the provider
        # so it stops re-delivering.
        if not communication_service.mark_inbound_seen(from_number, body):
            skipped += 1
            confirm_ids.append(reply_id)
            continue

        # Hiree (applicant) reply: a message to the DEDICATED applicant number belongs
        # to the recruiting inbox, never the lead pipeline. Route it, confirm, move on.
        from app.applicant_inbox.inbound import route_inbound_if_applicant

        if route_inbound_if_applicant(db, reply.get("to"), from_number, body):
            confirm_ids.append(reply_id)
            processed += 1
            continue

        lead = _find_lead_by_reply_phone(db, from_number)
        if not lead:
            # Unknown sender — auto-create so every Sinch-inbox message lands in
            # the Lead Pool (inbound capture only; never sends).
            from app.ai.services.inbound_lead import create_inbound_lead

            lead = create_inbound_lead(db, from_number, reply.get("to"))
        if not lead:
            failed += 1
            try:
                redis_service.client.delete(replay_key)
            except Exception:
                pass
            continue

        try:
            conversation = _get_or_create_conversation(db, lead)
            result = process_incoming_message(
                db=db,
                lead=lead,
                conversation=conversation,
                message_text=body,
                inbound_metadata=reply,
            )
            on_lead_replied(db, lead, conversation)
            db.commit()
            confirm_ids.append(reply_id)
            processed += 1

            # Instantly mirror this reply into the SMS human queue/chat (isolated,
            # additive — runs on its own session and never raises).
            from app.sms_queue.services.inbound_sync import mirror_inbound_to_sms

            mirror_inbound_to_sms(lead, conversation, body)

            _emit_to_tenant_safe(
                str(lead.tenant_id),
                "engage_cloud_inbound_processed",
                {
                    "lead_id": str(lead.id),
                    "conversation_id": str(conversation.id),
                    "from": from_number,
                    "message_sid": reply.get("message_sid"),
                    "reply_id": reply_id,
                    "result": result,
                },
            )
        except Exception as exc:
            db.rollback()
            failed += 1
            try:
                redis_service.client.delete(replay_key)
            except Exception:
                pass
            logger.error("Failed to process provider reply %s: %s", reply_id, exc)

    confirmed = service.confirm_replies(confirm_ids)
    if not confirmed.get("success"):
        return {
            "processed": processed,
            "confirmed": 0,
            "failed": failed + 1,
            "skipped": skipped,
            "available": len(fetched.get("replies", [])),
            "error": confirmed.get("error"),
            "status_code": confirmed.get("status_code"),
        }

    return {
        "processed": processed,
        "confirmed": confirmed.get("confirmed", 0),
        "failed": failed,
        "skipped": skipped,
        "available": len(fetched.get("replies", [])),
    }
