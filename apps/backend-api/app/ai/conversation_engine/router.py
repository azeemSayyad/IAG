"""
Conversation Engine Router (Step 36.1)

API endpoints for the AI conversation engine.

Endpoints:
- POST /ai/conversation/message — Process incoming message
- POST /ai/conversation/start — Start new conversation
- GET /ai/conversation/{id}/context — Get conversation context
- GET /ai/conversation/health — Check engine health
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.ai.conversation_engine.engine import ConversationEngine
from app.ai.conversation_engine.context_builder import ContextBuilder

router = APIRouter(prefix="/ai/conversation", tags=["ai-conversation"])


class MessageRequest(BaseModel):
    lead_id: UUID
    message: str
    conversation_id: Optional[UUID] = None
    campaign_id: Optional[UUID] = None
    language: Optional[str] = None  # e.g. "Spanish" — AI replies in this language


class StartConversationRequest(BaseModel):
    lead_id: UUID
    campaign_id: Optional[UUID] = None
    initial_message: Optional[str] = None


class DraftRequest(BaseModel):
    lead_id: UUID
    conversation_id: Optional[UUID] = None
    language: Optional[str] = None


@router.post("/draft")
async def draft_reply(
    request: DraftRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Generate a SUGGESTED reply for the agent based on the conversation so far.

    Unlike /message, this is read-only: it does NOT persist anything, send any
    SMS, or run booking/tool calls. It just returns draft text for the agent to
    review, edit, and send manually. Powers the Inbox "Draft a reply" action.
    """
    from app.models.message import Message
    from app.ai.services.ollama import OllamaClient

    lead = db.query(Lead).filter(
        Lead.id == request.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Pull the recent thread for context (newest 12, chronological).
    msg_q = db.query(Message).filter(Message.tenant_id == tenant_id)
    if request.conversation_id:
        msg_q = msg_q.filter(Message.conversation_id == request.conversation_id)
    else:
        conv_ids = [
            c.id for c in db.query(Conversation.id).filter(Conversation.lead_id == lead.id).all()
        ]
        if conv_ids:
            msg_q = msg_q.filter(Message.conversation_id.in_(conv_ids))
    recent = msg_q.order_by(Message.created_at.desc()).limit(12).all()
    recent = list(reversed(recent))

    def _who(sender: str) -> str:
        s = (sender or "").lower()
        return "Customer" if s in ("lead", "customer", "inbound", "user") else "Agent"

    history = "\n".join(f"{_who(m.sender)}: {m.content}" for m in recent if m.content)
    customer = (lead.first_name or "the customer").strip()

    system = (
        "You are an ACA health-insurance sales agent. Draft the NEXT SMS reply to "
        "send to the customer, based on the conversation so far. Keep it short, "
        "warm, and compliant (no guarantees, no PHI requests over SMS). Move the "
        "conversation toward booking a call when appropriate. Return ONLY the "
        "message text to send — no preamble, quotes, or labels."
    )
    lang = (request.language or "").strip()
    if lang and lang.lower() not in ("english", "english (us)", "english (uk)"):
        system += f" Write the reply in {lang}."
    user = (
        f"Conversation so far with {customer}:\n"
        f"{history or '(no messages yet — this is the opening outreach)'}\n\n"
        f"Draft the next reply to send to {customer}."
    )

    client = OllamaClient()
    try:
        if not await client.is_available():
            raise HTTPException(status_code=503, detail="AI is not available right now")
        draft = await client.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.6,
            max_tokens=300,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI is unavailable: {exc}")

    draft = (draft or "").strip().strip('"')
    if not draft:
        raise HTTPException(status_code=502, detail="Could not generate a draft right now")
    return {"draft": draft}


@router.post("/message")
async def process_message(
    request: MessageRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Process an incoming customer message through the AI conversation engine.

    This is the main endpoint that replaces template-based responses with
    real LLM-driven intelligent conversations.

    Flow:
    1. Build context (lead, conversation, memory, campaign)
    2. Detect intent and sentiment
    3. Generate LLM response
    4. Execute tool calls (booking, updates, etc.)
    5. Update memory
    6. Return response
    """
    # Verify lead exists
    lead = db.query(Lead).filter(
        Lead.id == request.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Create engine and process
    engine = ConversationEngine(db, tenant_id)
    response = await engine.process_message(
        lead_id=request.lead_id,
        message_text=request.message,
        conversation_id=request.conversation_id,
        campaign_id=request.campaign_id,
        language=request.language,
    )

    return response.to_dict()


@router.post("/start")
async def start_conversation(
    request: StartConversationRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Start a new AI-driven conversation with a lead.

    If initial_message is provided, generates an AI outreach message.
    Otherwise, creates the conversation and returns context.
    """
    # Verify lead exists
    lead = db.query(Lead).filter(
        Lead.id == request.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()

    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Check for existing active conversation
    existing = db.query(Conversation).filter(
        Conversation.lead_id == request.lead_id,
        Conversation.status.in_(["active", "initiated", "booking"]),
    ).first()

    if existing:
        return {
            "conversation_id": str(existing.id),
            "status": existing.status,
            "message": "Active conversation already exists",
        }

    # Create new conversation
    conversation = Conversation(
        tenant_id=tenant_id,
        lead_id=request.lead_id,
        status="active",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    result = {
        "conversation_id": str(conversation.id),
        "status": "active",
        "message": "Conversation created",
    }

    # If initial message provided, process it
    if request.initial_message:
        engine = ConversationEngine(db, tenant_id)
        response = await engine.process_message(
            lead_id=request.lead_id,
            message_text=request.initial_message,
            conversation_id=conversation.id,
            campaign_id=request.campaign_id,
        )
        result["ai_response"] = response.to_dict()

    return result


@router.get("/{conversation_id}/context")
async def get_conversation_context(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the current context for a conversation.

    Useful for debugging and monitoring AI state.
    """
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    context_builder = ContextBuilder(db)
    ctx = context_builder.build(
        lead_id=conversation.lead_id,
        conversation_id=conversation_id,
    )

    return {
        "context": ctx.to_dict(),
        "summary": context_builder.build_summary(ctx),
    }


@router.get("/health")
async def engine_health(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Check health of the conversation engine.

    Returns status of LLM, context builder, and tool executor.
    """
    engine = ConversationEngine(db, tenant_id)
    return await engine.health_check()


class AskRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None   # accepted but unused (e.g. 'ask-brain')
    thread_id: Optional[str] = None
    language: Optional[str] = None           # e.g. "Spanish" — AI replies in this language


@router.post("/ask")
async def ask_the_brain(
    request: AskRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """General AI assistant ('Ask the Brain') — a free-form question that is NOT
    tied to a specific lead/conversation. Reuses the existing Ollama client.
    """
    from app.ai.services.ollama import OllamaClient

    q = (request.message or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="message is required")

    first = getattr(current_user, "first_name", None) or "there"
    system = (
        "You are the Insurance Alliance Group assistant, for insurance sales agents at an ACA call "
        "center. Answer the agent's question helpfully and concisely (a few short "
        "sentences or a short list). You are talking to the agent, not a customer. "
        f"The agent's name is {first}. If you don't have data, say so plainly."
    )
    lang = (request.language or "").strip()
    if lang and lang.lower() not in ("english", "english (us)", "english (uk)"):
        system += f" Always respond in {lang}."
    try:
        client = OllamaClient()
        answer = await client.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": q}],
            temperature=0.5,
            max_tokens=500,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI is unavailable: {e}")

    if not answer:
        answer = "I couldn't generate a response right now. Please try again."
    return {"response": answer, "suggestions": []}
