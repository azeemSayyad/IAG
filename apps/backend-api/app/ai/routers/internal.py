"""
AI Internal APIs (Step 16.7)

Internal endpoints for AI service communication.

These endpoints are used by:
- Workers to process AI tasks
- Other microservices to request AI capabilities
- Background jobs for batch processing

Endpoints:
- POST /internal/intent-detect — Detect intent from message
- POST /internal/generate-response — Generate AI response
- POST /internal/lead-score — Score a lead
- POST /internal/summarize — Generate lead summary
- POST /internal/handle-objection — Handle an objection
"""

from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id
from app.core.config import settings
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.intent.services.classifier import classify_intent
from app.intent.services.objections import handle_objection
from app.ai.services.ollama import OllamaClient
from app.ai.services.prompts import build_llm_prompt
from app.ingestion.services.scoring import calculate_lead_score
from app.agent_os.services.lead_summary import generate_lead_summary
from app.core.audit import log_ai_action


router = APIRouter(prefix="/internal", tags=["internal-ai"])


# --- Schemas ---

class IntentDetectRequest(BaseModel):
    text: str
    conversation_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    context: Optional[Dict[str, Any]] = None


class IntentDetectResponse(BaseModel):
    intent: str
    confidence: float
    method: str
    details: Dict[str, Any]


class GenerateResponseRequest(BaseModel):
    message: str
    conversation_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    system_prompt: Optional[str] = None
    tone: str = "friendly"
    context: Optional[Dict[str, Any]] = None


class GenerateResponseResponse(BaseModel):
    response: str
    model: str
    tokens_used: Optional[int]


class LeadScoreRequest(BaseModel):
    lead_id: UUID


class LeadScoreResponse(BaseModel):
    lead_score: float
    booking_probability: float
    conversion_probability: float
    tier: str


class SummarizeRequest(BaseModel):
    lead_id: UUID


class SummarizeResponse(BaseModel):
    summary: Dict[str, Any]


class ObjectionHandleRequest(BaseModel):
    message: str
    lead_name: str = ""
    tone: str = "friendly"


class ObjectionHandleResponse(BaseModel):
    objection_type: str
    confidence: float
    response: str


# --- Endpoints ---

@router.post("/intent-detect", response_model=IntentDetectResponse)
async def detect_intent(
    data: IntentDetectRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Detect intent from a customer message.

    Uses hybrid approach:
    1. Fast classifier (keyword/regex) for clear messages
    2. LLM fallback for ambiguous messages
    """
    # Get conversation context if provided
    context = data.context or {}
    if data.conversation_id:
        conversation = db.query(Conversation).filter(
            Conversation.id == data.conversation_id,
        ).first()
        if conversation:
            context["conversation_history"] = conversation.ai_context or {}

    result = await classify_intent(
        text=data.text,
        conversation_history=context.get("conversation_history", []) if context else [],
    )

    log_ai_action(
        tenant_id=tenant_id,
        action="intent_detected_internal",
        resource_type="conversation",
        resource_id=str(data.conversation_id) if data.conversation_id else None,
        details={"intent": result.intent, "confidence": result.confidence, "method": result.method},
    )

    return IntentDetectResponse(
        intent=result.intent,
        confidence=result.confidence,
        method=result.method,
        details=result.details if hasattr(result, 'details') else {},
    )


@router.post("/generate-response", response_model=GenerateResponseResponse)
async def generate_response(
    data: GenerateResponseRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Generate an AI response for a customer message.

    Uses Ollama with fallback models.
    """
    ollama = OllamaClient()

    # Get conversation history if available
    conversation_history = []
    if data.conversation_id:
        conversation = db.query(Conversation).filter(
            Conversation.id == data.conversation_id,
        ).first()
        if conversation and conversation.ai_context:
            conversation_history = conversation.ai_context.get("message_history", [])

    lead_data = {}
    if data.lead_id:
        lead = db.query(Lead).filter(
            Lead.id == data.lead_id,
            Lead.tenant_id == tenant_id,
            Lead.deleted_at.is_(None),
        ).first()
        if lead:
            lead_data = {
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "source": lead.source,
                "state": lead.state,
            }

    messages = build_llm_prompt(
        system_context=(data.context or {}).get("system_context", "outreach"),
        lead_data=lead_data,
        conversation_history=conversation_history,
        task=data.message,
        tone=data.tone,
    )
    if data.system_prompt:
        messages[0]["content"] = data.system_prompt

    # Generate response
    try:
        response = await ollama.chat(
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )

        log_ai_action(
            tenant_id=tenant_id,
            action="response_generated_internal",
            resource_type="conversation",
            resource_id=str(data.conversation_id) if data.conversation_id else None,
            details={"model": ollama.base_url, "response_length": len(response)},
        )

        return GenerateResponseResponse(
            response=response,
            model=settings.OLLAMA_MODEL,
            tokens_used=None,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")


@router.post("/lead-score", response_model=LeadScoreResponse)
def score_lead(
    data: LeadScoreRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Score a lead using the ML scoring engine.

    Returns lead_score, booking_probability, conversion_probability.
    """
    lead = db.query(Lead).filter(
        Lead.id == data.lead_id,
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_data = {
        "source": lead.source,
        "state": lead.state,
        "email": lead.email,
        "phone": lead.phone,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "city": lead.city,
        "zip_code": lead.zip_code,
    }

    score = calculate_lead_score(lead_data, created_at=lead.created_at)
    from app.ingestion.services.scoring import get_score_tier
    tier = get_score_tier(score)

    # Update lead score
    lead.lead_score = score
    db.commit()

    log_ai_action(
        tenant_id=tenant_id,
        action="lead_scored_internal",
        resource_type="lead",
        resource_id=str(data.lead_id),
        details={"score": score, "tier": tier},
    )

    return LeadScoreResponse(
        lead_score=score,
        booking_probability=lead.booking_probability or 0,
        conversion_probability=lead.conversion_probability or 0,
        tier=tier,
    )


@router.post("/summarize", response_model=SummarizeResponse)
def summarize_lead(
    data: SummarizeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Generate an AI summary of a lead for agents.

    Includes: interest level, objections, best closing angle, recommendations.
    """
    summary = generate_lead_summary(db, data.lead_id)
    if "error" in summary:
        raise HTTPException(status_code=404, detail=summary["error"])

    return SummarizeResponse(summary=summary)


@router.post("/handle-objection", response_model=ObjectionHandleResponse)
def handle_objection_endpoint(
    data: ObjectionHandleRequest,
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Detect and handle a customer objection.

    Returns objection type, confidence, and suggested response.
    """
    result = handle_objection(
        text=data.message,
        first_name=data.lead_name or "there",
    )

    return ObjectionHandleResponse(
        objection_type=result["objection_type"],
        confidence=result["confidence"],
        response=result["response"],
    )
