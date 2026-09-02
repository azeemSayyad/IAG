"""
Conversation Router (Step 16.6)

Endpoints:
- GET /conversations — List conversations
- GET /conversations/{id} — Get conversation with messages
- POST /conversations — Create conversation
- PATCH /conversations/{id} — Update conversation status
- POST /conversations/{id}/messages — Add message to conversation
- GET /conversations/{id}/messages — Get conversation messages
"""

from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.lead import Lead
from app.core.audit import log_create, log_update
from app.ai.services.communication_provider import communication_service
from app.realtime.websocket import emit_to_tenant


router = APIRouter(prefix="/conversations", tags=["conversations"])


# --- Schemas ---

class ConversationCreate(BaseModel):
    lead_id: UUID
    status: str = "initiated"


class ConversationUpdate(BaseModel):
    status: Optional[str] = None
    intent: Optional[str] = None
    sentiment: Optional[str] = None


class MessageCreate(BaseModel):
    content: str
    sender: str = "customer"
    message_type: str = "sms"
    intent: Optional[str] = None
    sentiment: Optional[str] = None
    metadata: dict = {}
    send_sms: bool = True


# --- Endpoints ---

@router.get("", response_model=dict)
def list_conversations(
    lead_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List conversations with filtering and pagination."""
    tenant_id = str(current_user.tenant_id)
    query = db.query(Conversation).filter(Conversation.tenant_id == tenant_id)

    # Privacy: operators (agent / team leader / manager) may only see
    # conversations for leads THEY have an appointment with — every inbox is
    # private (the AI's full history stays hidden). Head managers / admins /
    # super admins see all conversations in the tenant (oversight).
    if getattr(current_user, "role", None) in ("agent", "lead", "manager"):
        from app.models.agent import Agent
        from app.models.appointment import Appointment
        agent = db.query(Agent).filter(Agent.user_id == current_user.id).first()
        appt_lead_ids = []
        if agent:
            appt_lead_ids = [
                r[0]
                for r in db.query(Appointment.lead_id)
                .filter(Appointment.tenant_id == tenant_id, Appointment.agent_id == agent.id)
                .distinct()
                .all()
            ]
        if not appt_lead_ids:
            return {"items": [], "total": 0, "page": page, "size": size}
        query = query.filter(Conversation.lead_id.in_(appt_lead_ids))

    if lead_id:
        query = query.filter(Conversation.lead_id == lead_id)
    if status:
        query = query.filter(Conversation.status == status)

    total = query.count()
    conversations = (
        query.order_by(Conversation.last_message_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    lead_ids = [c.lead_id for c in conversations if c.lead_id]
    leads = {str(L.id): L for L in db.query(Lead).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}

    items = []
    for c in conversations:
        lead = leads.get(str(c.lead_id)) if c.lead_id else None
        last_msg = (
            db.query(Message.content)
            .filter(Message.conversation_id == c.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        items.append({
            "id": str(c.id),
            "tenant_id": str(c.tenant_id),
            "lead_id": str(c.lead_id),
            "lead_name": (lead.first_name + ' ' + lead.last_name).strip() if lead else None,
            "lead_phone": lead.phone if lead else None,
            "lead_score": lead.lead_score if lead else None,
            "priority": 'hot' if lead and lead.lead_score >= 70 else 'warm' if lead and lead.lead_score >= 30 else 'cool' if lead else 'medium',
            "status": c.status,
            "intent": c.intent,
            "sentiment": c.sentiment,
            "message_count": c.message_count,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "last_message": last_msg[0] if last_msg else None,
            "last_message_from": c.last_message_from,
            "created_at": c.created_at.isoformat(),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_conversation(
    data: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new conversation for a lead."""
    tenant_id = str(current_user.tenant_id)

    # Verify lead exists
    lead = db.query(Lead).filter(
        Lead.id == data.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    conversation = Conversation(
        tenant_id=tenant_id,
        lead_id=data.lead_id,
        status=data.status,
        message_count=0,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    log_create(
        tenant_id=tenant_id,
        user_id=str(current_user.id),
        resource_type="conversation",
        resource_id=str(conversation.id),
        details={"lead_id": str(data.lead_id)},
    )

    return {
        "id": str(conversation.id),
        "tenant_id": str(conversation.tenant_id),
        "lead_id": str(conversation.lead_id),
        "status": conversation.status,
        "message_count": conversation.message_count,
        "created_at": conversation.created_at.isoformat(),
    }


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a conversation by ID."""
    tenant_id = str(current_user.tenant_id)
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": str(conversation.id),
        "tenant_id": str(conversation.tenant_id),
        "lead_id": str(conversation.lead_id),
        "status": conversation.status,
        "intent": conversation.intent,
        "sentiment": conversation.sentiment,
        "message_count": conversation.message_count,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "last_message_from": conversation.last_message_from,
        "created_at": conversation.created_at.isoformat(),
    }


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: UUID,
    data: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update a conversation."""
    tenant_id = str(current_user.tenant_id)
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    update_data = data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(conversation, field, value)

    db.commit()
    db.refresh(conversation)

    log_update(
        tenant_id=tenant_id,
        user_id=str(current_user.id),
        resource_type="conversation",
        resource_id=str(conversation.id),
        details=update_data,
    )

    return {
        "id": str(conversation.id),
        "tenant_id": str(conversation.tenant_id),
        "lead_id": str(conversation.lead_id),
        "status": conversation.status,
        "intent": conversation.intent,
        "sentiment": conversation.sentiment,
        "message_count": conversation.message_count,
        "created_at": conversation.created_at.isoformat(),
    }


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def add_message(
    conversation_id: UUID,
    data: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Add a message to a conversation."""
    tenant_id = str(current_user.tenant_id)
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    lead = db.query(Lead).filter(
        Lead.id == conversation.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    provider = None
    provider_message_sid = None
    delivery_status = None
    delivery_error_code = None
    delivery_error_message = None
    message_metadata = dict(data.metadata or {})

    should_send_sms = data.send_sms and data.message_type == "sms" and data.sender in ("agent", "ai")
    if should_send_sms:
        result = communication_service.send_sms(
            to=lead.phone,
            body=data.content,
            tenant_id=tenant_id,
            lead_id=str(lead.id),
        )
        provider = result.get("provider") or communication_service.provider_name
        if result.get("error"):
            # Provider send failed (e.g. credentials not configured or a transient
            # outage). Don't lose the agent's typed message with a hard 502 —
            # persist it and record the failure so the UI can show "not delivered"
            # and ops can retry. (Compliance/TCPA blocks above still hard-stop.)
            delivery_status = "failed"
            delivery_error_message = result.get("error")
            message_metadata[provider] = {
                "status": "failed",
                "error": result.get("error"),
                "to": lead.phone,
            }
        else:
            provider_message_sid = result.get("message_sid")
            delivery_status = result.get("status")
            message_metadata[provider] = {
                "message_sid": provider_message_sid,
                "status": delivery_status,
                "to": lead.phone,
            }

    message = Message(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        sender=data.sender,
        content=data.content,
        message_type=data.message_type,
        intent=data.intent,
        sentiment=data.sentiment,
        msg_metadata=message_metadata,
        provider=provider,
        provider_message_sid=provider_message_sid,
        delivery_status=delivery_status,
        delivery_error_code=delivery_error_code,
        delivery_error_message=delivery_error_message,
    )
    db.add(message)

    # Update conversation
    conversation.message_count += 1
    conversation.last_message_from = data.sender
    conversation.last_message_at = datetime.now(timezone.utc)

    if data.intent:
        conversation.intent = data.intent
    if data.sentiment:
        conversation.sentiment = data.sentiment

    db.commit()
    db.refresh(message)

    response = {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "sender": message.sender,
        "content": message.content,
        "message_type": message.message_type,
        "intent": message.intent,
        "sentiment": message.sentiment,
        "metadata": message.msg_metadata or {},
        "provider": message.provider,
        "provider_message_sid": message.provider_message_sid,
        "delivery_status": message.delivery_status,
        "created_at": message.created_at.isoformat(),
    }
    await emit_to_tenant(tenant_id, "conversation_message_created", response)
    return response


@router.get("/{conversation_id}/messages")
def get_messages(
    conversation_id: UUID,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get messages for a conversation."""
    tenant_id = str(current_user.tenant_id)
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    query = db.query(Message).filter(Message.conversation_id == conversation_id)
    total = query.count()
    messages = (
        query.order_by(Message.created_at)
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    items = []
    for m in messages:
        meta = m.msg_metadata if m.msg_metadata else {}
        if not isinstance(meta, dict):
            meta = {}
        items.append({
            "id": str(m.id),
            "conversation_id": str(m.conversation_id),
            "sender": m.sender,
            "content": m.content,
            "message_type": m.message_type,
            "intent": m.intent,
            "sentiment": m.sentiment,
            "metadata": meta,
            "provider": m.provider,
            "provider_message_sid": m.provider_message_sid,
            "delivery_status": m.delivery_status,
            "delivery_error_code": m.delivery_error_code,
            "delivery_error_message": m.delivery_error_message,
            "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
            "created_at": m.created_at.isoformat(),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }
