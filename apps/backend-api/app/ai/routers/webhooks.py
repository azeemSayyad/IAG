"""
Webhook Router

Endpoints:
- POST /webhooks/engage-clouds — Receive Engage Clouds webhook events
- POST /webhooks/twilio/inbound — Legacy compatibility alias
- POST /webhooks/twilio/status — Legacy compatibility alias
- GET /ai/status — Check AI system status
- GET /ai/queues — View queue sizes
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.ai.services.communication_provider import communication_service
from app.ai.services.orchestrator import process_incoming_message
from app.ai.services.ollama import ollama_client
from app.ai.services.queue import get_all_queue_sizes
from app.ai.services.reply_polling import poll_provider_replies_once
from app.ingestion.services.events import on_lead_replied
from app.ingestion.services.validation import normalize_phone
from app.core.audit import log_ai_action
from app.realtime.websocket import emit_to_tenant


router = APIRouter(tags=["webhooks", "ai"])


async def _read_webhook_payload(request: Request) -> tuple[dict, bytes]:
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        import json
        return json.loads(body.decode("utf-8") or "{}"), body
    form = await request.form()
    payload = dict(form)
    return payload, body


async def _process_provider_webhook(request: Request, db: Session, validator=None) -> dict:
    payload, raw_body = await _read_webhook_payload(request)
    # `validator` lets a 2nd provider (engage2) authenticate this SAME lead-inbound flow
    # with its OWN webhook secret; default = Sinch, so the existing endpoint is unchanged.
    _validate = validator or communication_service.validate_webhook
    if not _validate(raw_body, dict(request.headers)):
        raise HTTPException(status_code=403, detail="Invalid Engage Clouds webhook signature")
    if not communication_service.mark_replay_seen(payload):
        return {"status": "duplicate"}

    event = communication_service.parse_webhook(payload)
    if event["kind"] == "delivery_status":
        return await _process_delivery_event(event, db)

    message_data = event
    from_number = message_data["from"]
    message_body = message_data["body"]

    if not from_number or not message_body:
        return {"status": "ignored"}

    # Cross-path dedup: the reply-polling fallback may also pick up this exact
    # reply. Claim a shared per-(phone, body) key first so we never process the
    # same inbound twice (which sent duplicate / out-of-order AI replies).
    if not communication_service.mark_inbound_seen(from_number, message_body):
        return {"status": "duplicate_inbound"}

    # Hiree (applicant) reply: a message addressed to the DEDICATED applicant number
    # belongs to the recruiting inbox, never the lead pipeline. Route it and stop.
    from app.applicant_inbox.inbound import route_inbound_if_applicant

    if route_inbound_if_applicant(db, message_data.get("to"), from_number, message_body):
        return {"status": "applicant_reply"}

    # Match the inbound to a lead with format-agnostic matching — tolerates the
    # assorted phone formats / NULL phone_normalized from messy CSV uploads, so a
    # reply that arrives a day (or more) later still lands. If the sender is
    # genuinely unknown, auto-create a lead so EVERY Sinch-inbox message surfaces
    # in the Lead Pool. Inbound capture only: this never sends — a first_template
    # (the lone send the lockdown allows) is enqueued only by the CSV/campaign/
    # drip paths, never by creating a Lead row.
    from app.ai.services.inbound_lead import find_lead_for_inbound, create_inbound_lead

    lead = find_lead_for_inbound(db, from_number)
    if not lead:
        lead = create_inbound_lead(db, from_number, message_data.get("to"))
        if not lead:
            return {"status": "no_lead_found"}

    # Find active conversation
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.lead_id == lead.id,
            Conversation.status.in_(["active", "initiated", "booking"]),
        )
        .first()
    )
    if not conversation:
        # Create new conversation
        conversation = Conversation(
            tenant_id=lead.tenant_id,
            lead_id=lead.id,
            status="active",
        )
        db.add(conversation)
        db.flush()

    # Process the incoming message
    result = process_incoming_message(
        db=db,
        lead=lead,
        conversation=conversation,
        message_text=message_body,
        inbound_metadata=message_data,
    )

    # Trigger lead replied event
    on_lead_replied(db, lead, conversation)
    await emit_to_tenant(str(lead.tenant_id), "engage_cloud_inbound_processed", {
        "lead_id": str(lead.id),
        "conversation_id": str(conversation.id),
        "from": from_number,
        "message_sid": message_data.get("message_sid"),
        "result": result,
    })

    # Instantly mirror this reply into the SMS human queue/chat (isolated,
    # additive — runs on its own session and never raises).
    from app.sms_queue.services.inbound_sync import mirror_inbound_to_sms

    mirror_inbound_to_sms(lead, conversation, message_body)

    return {"status": "processed", "result": result}


async def _process_delivery_event(status_data: dict, db: Session) -> dict:
    message = None
    if status_data.get("message_sid"):
        message = db.query(Message).filter(
            Message.provider.in_([communication_service.provider_name, "twilio"]),
            Message.provider_message_sid == status_data["message_sid"],
        ).first()
    if message:
        message.delivery_status = status_data.get("status")
        message.delivery_error_code = status_data.get("error_code")
        message.delivery_error_message = status_data.get("error_message")
        if status_data.get("status") == "delivered":
            from datetime import datetime, timezone
            message.delivered_at = datetime.now(timezone.utc)
        db.commit()
        await emit_to_tenant(str(message.tenant_id), "message_delivery_updated", {
            "message_id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "provider_message_sid": message.provider_message_sid,
            "delivery_status": message.delivery_status,
            "delivery_error_code": message.delivery_error_code,
            "delivery_error_message": message.delivery_error_message,
        })

    # Log delivery status. (resource_id is REQUIRED by log_ai_action — its
    # absence here caused a 500 on every provider delivery-status webhook.)
    if status_data.get("error_code"):
        msg_sid = status_data.get("message_sid") or "unknown"
        try:
            log_ai_action(
                tenant_id="system",
                action="sms_delivery_failed",
                resource_type="message",
                resource_id=msg_sid,
                details={
                    "message_sid": msg_sid,
                    "error_code": status_data.get("error_code"),
                    "error_message": status_data.get("error_message"),
                },
            )
        except Exception:
            # Never let logging break webhook ingestion.
            pass

    return {"status": "ok"}


@router.post("/webhooks/engage-clouds")
async def engage_clouds_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive inbound, status, and thread events from Engage Clouds (Sinch)."""
    return await _process_provider_webhook(request, db)


@router.post("/webhooks/engage2")
async def engage2_webhook(request: Request, db: Session = Depends(get_db)):
    """Inbound webhook for the SECOND lead-SMS provider (ENGAGE2). Validated with the
    ENGAGE2 webhook secret; routes replies into the SAME lead pipeline as Sinch."""
    from app.ai.services.communication_provider import engage2_service
    return await _process_provider_webhook(request, db, validator=engage2_service.validate_webhook)


@router.post("/webhooks/applicant-engage")
async def applicant_engage_webhook(request: Request, db: Session = Depends(get_db)):
    """Inbound webhook for the DEDICATED hiree Engage Cloud sub-account.

    Fully separate from the lead webhook above: validated with the applicant webhook
    secret (APPLICANT_ENGAGE_CLOUD_WEBHOOK_SECRET) and files every reply into the
    applicant inbox ONLY — it never creates a lead or runs the AI orchestrator. The
    payload shape matches Engage Cloud, so the pure parser is reused.
    """
    from app.applicant_inbox.provider import applicant_provider
    from app.applicant_inbox.inbound import route_inbound_if_applicant

    payload, raw_body = await _read_webhook_payload(request)
    if not applicant_provider.validate_webhook(raw_body, dict(request.headers)):
        raise HTTPException(status_code=403, detail="Invalid applicant webhook signature")
    if not communication_service.mark_replay_seen(payload):
        return {"status": "duplicate"}

    event = communication_service.parse_webhook(payload)
    if event["kind"] == "delivery_status":
        return {"status": "ok"}  # delivery receipts: ack (no provider-sid column yet)

    from_number = event.get("from")
    message_body = event.get("body")
    if not from_number or not message_body:
        return {"status": "ignored"}
    if not communication_service.mark_inbound_seen(from_number, message_body):
        return {"status": "duplicate_inbound"}

    # This endpoint IS the hiree channel, so file every inbound (skip the to-check).
    route_inbound_if_applicant(
        db, event.get("to"), from_number, message_body, require_applicant_number=False
    )
    return {"status": "applicant_reply"}


@router.post("/webhooks/twilio/inbound")
async def twilio_inbound_legacy(request: Request, db: Session = Depends(get_db)):
    """Legacy compatibility alias. Provider handling is Engage Clouds."""
    return await _process_provider_webhook(request, db)


@router.post("/webhooks/twilio/status")
async def twilio_status_legacy(request: Request, db: Session = Depends(get_db)):
    """Legacy compatibility alias. Provider handling is Engage Clouds."""
    return await _process_provider_webhook(request, db)


@router.get("/ai/status")
async def ai_status(current_user: User = Depends(get_current_active_user)):
    """Check AI system status."""
    ollama_available = await ollama_client.is_available()
    models = await ollama_client.list_models() if ollama_available else []

    return {
        "ollama": {
            "available": ollama_available,
            "models": models,
            "base_url": ollama_client.base_url,
        },
        "queues": get_all_queue_sizes(),
    }


@router.get("/ai/queues")
async def ai_queues(current_user: User = Depends(get_current_active_user)):
    """View queue sizes."""
    return get_all_queue_sizes()


@router.post("/ai/replies/poll")
async def poll_replies_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Poll provider replies immediately.

    Use this when inbound webhooks are not configured yet. It uses the provider
    replies API, processes customer messages through the same AI/booking flow,
    persists messages and appointments, emits realtime events, and confirms
    replies with the provider after successful processing.

    Also polls the dedicated hiree (applicant) account so its replies land in the
    applicant inbox (separate account; no-op when unconfigured).
    """
    lead = poll_provider_replies_once(db)
    try:
        from app.applicant_inbox.reply_polling import poll_applicant_replies_once
        applicant = poll_applicant_replies_once(db)
    except Exception:
        applicant = {"error": "applicant_poll_failed"}
    return {"lead": lead, "applicant": applicant}
