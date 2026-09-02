"""
Intent Detection Router

Endpoints:
- POST /intent/detect — Detect intent from a message
- GET /intent/classes — List all intent classes
"""

from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.intent.services.classifier import classify_intent, fast_classify
from app.intent.services.intents import Intent, INTENT_METADATA, get_intent_metadata
from app.intent.services.objections import detect_objection, handle_objection
from app.intent.services.memory import analyze_sentiment

router = APIRouter(prefix="/intent", tags=["intent"])


class IntentDetectRequest(BaseModel):
    text: str
    conversation_history: Optional[List[Dict]] = None
    force_llm: bool = False


class ObjectionHandleRequest(BaseModel):
    text: str
    first_name: str


@router.post("/detect")
async def detect_intent(
    request: IntentDetectRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Detect intent from a message.

    Uses hybrid approach:
    1. Fast classifier (< 100ms) for clear messages
    2. LLM fallback for ambiguous messages
    """
    result = await classify_intent(
        text=request.text,
        conversation_history=request.conversation_history,
        force_llm=request.force_llm,
    )

    # Also detect sentiment
    sentiment, sentiment_score = analyze_sentiment(request.text)

    # Check for objections
    objection_type, objection_confidence = detect_objection(request.text)

    return {
        "intent": result.to_dict(),
        "sentiment": {
            "type": sentiment,
            "score": round(sentiment_score, 3),
        },
        "objection": {
            "type": objection_type.value,
            "confidence": round(objection_confidence, 3),
        },
    }


@router.post("/objection/handle")
async def handle_objection_endpoint(
    request: ObjectionHandleRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Handle an objection and get an appropriate response.
    """
    result = handle_objection(request.text, request.first_name)
    return result


@router.get("/classes")
async def list_intent_classes(
    current_user: User = Depends(get_current_active_user),
):
    """
    List all intent classes with metadata.
    """
    classes = []
    for intent in Intent:
        metadata = get_intent_metadata(intent)
        classes.append({
            "name": intent.value,
            "description": metadata.get("description", ""),
            "next_action": metadata.get("next_action", ""),
            "priority": metadata.get("priority", 0),
        })
    return {"intents": classes}


@router.get("/objections/types")
async def list_objection_types(
    current_user: User = Depends(get_current_active_user),
):
    """
    List all objection types.
    """
    from app.intent.services.objections import ObjectionType

    types = []
    for obj_type in ObjectionType:
        types.append({
            "name": obj_type.value,
            "description": obj_type.value.replace("_", " ").title(),
        })
    return {"objection_types": types}
