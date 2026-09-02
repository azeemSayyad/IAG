"""
Predictive Lead Scoring (Step 11.1)

Predicts:
- Booking likelihood (0-100)
- Conversion likelihood (0-100)

Uses historical data to train models and make predictions.
Starts with rule-based approach, can be upgraded to ML models.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.lead import Lead
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.message import Message


# Feature weights (can be learned from data)
BOOKING_FEATURE_WEIGHTS = {
    "source_quality": 0.20,
    "response_speed": 0.15,
    "engagement_level": 0.20,
    "demographic_fit": 0.15,
    "timing_score": 0.10,
    "sentiment_score": 0.10,
    "objection_count": 0.10,
}

CONVERSION_FEATURE_WEIGHTS = {
    "lead_score": 0.15,
    "engagement_depth": 0.20,
    "appointment_kept": 0.20,
    "sentiment_trend": 0.15,
    "source_quality": 0.10,
    "response_time": 0.10,
    "agent_quality": 0.10,
}


def extract_booking_features(
    db: Session,
    lead: Lead,
) -> Dict[str, float]:
    """
    Extract features for booking prediction.
    """
    features = {}

    # Source quality
    source_scores = {
        "referral": 0.95,
        "organic": 0.85,
        "google": 0.80,
        "facebook": 0.70,
        "webhook": 0.65,
        "api": 0.60,
        "csv_import": 0.55,
        "manual": 0.50,
    }
    features["source_quality"] = source_scores.get(lead.source, 0.50)

    # Response speed (how fast lead replied)
    conversation = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id)
        .first()
    )

    response_speed = 0.5  # Default
    if conversation and conversation.last_message_at and lead.last_contacted_at:
        reply_time = (conversation.last_message_at - lead.last_contacted_at).total_seconds()
        if reply_time < 300:  # < 5 min
            response_speed = 1.0
        elif reply_time < 3600:  # < 1 hour
            response_speed = 0.8
        elif reply_time < 86400:  # < 24 hours
            response_speed = 0.6
        else:
            response_speed = 0.3
    features["response_speed"] = response_speed

    # Engagement level
    message_count = 0
    if conversation:
        message_count = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .count()
        )
    features["engagement_level"] = min(message_count / 10, 1.0)

    # Demographic fit
    demographic_score = 0.5
    if lead.state:
        high_value_states = {"FL", "TX", "CA", "NY", "NJ", "PA", "OH", "IL", "GA", "NC"}
        if lead.state.upper() in high_value_states:
            demographic_score = 0.8
    features["demographic_fit"] = demographic_score

    # Timing score
    hour = lead.created_at.hour if lead.created_at else 12
    if 9 <= hour <= 18:
        features["timing_score"] = 0.8
    elif 7 <= hour <= 21:
        features["timing_score"] = 0.6
    else:
        features["timing_score"] = 0.4

    # Sentiment score
    sentiment_score = 0.5
    if conversation and conversation.sentiment:
        if conversation.sentiment == "positive":
            sentiment_score = 0.9
        elif conversation.sentiment == "negative":
            sentiment_score = 0.2
    features["sentiment_score"] = sentiment_score

    # Objection count (negative = more objections = lower score)
    objection_count = 0
    if conversation and conversation.ai_context:
        objections = conversation.ai_context.get("objections", [])
        objection_count = len(objections)
    features["objection_count"] = max(1 - (objection_count * 0.2), 0)

    return features


def extract_conversion_features(
    db: Session,
    lead: Lead,
) -> Dict[str, float]:
    """
    Extract features for conversion prediction.
    """
    features = {}

    # Lead score (normalized)
    features["lead_score"] = lead.lead_score / 100

    # Engagement depth
    conversation = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id)
        .first()
    )

    message_count = 0
    if conversation:
        message_count = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .count()
        )
    features["engagement_depth"] = min(message_count / 15, 1.0)

    # Appointment kept
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.lead_id == lead.id,
            Appointment.status == "completed",
        )
        .count()
    )
    features["appointment_kept"] = 1.0 if appointments > 0 else 0.0

    # Sentiment trend
    sentiment_trend = 0.5
    if conversation and conversation.ai_context:
        sentiment_history = conversation.ai_context.get("sentiment_history", [])
        if len(sentiment_history) >= 2:
            recent = sentiment_history[-3:]
            scores = [s.get("score", 0.5) for s in recent]
            avg = sum(scores) / len(scores)
            sentiment_trend = avg
    features["sentiment_trend"] = sentiment_trend

    # Source quality
    source_scores = {
        "referral": 0.95,
        "organic": 0.85,
        "google": 0.80,
        "facebook": 0.70,
    }
    features["source_quality"] = source_scores.get(lead.source, 0.50)

    # Response time
    features["response_time"] = 0.5  # Default

    # Agent quality (based on agent win rate)
    agent_quality = 0.5
    if appointments > 0:
        agent_quality = 0.7  # Has completed appointment
    features["agent_quality"] = agent_quality

    return features


def calculate_booking_probability(
    features: Dict[str, float],
) -> float:
    """
    Calculate booking probability from features.
    """
    score = 0.0
    for feature, weight in BOOKING_FEATURE_WEIGHTS.items():
        value = features.get(feature, 0.5)
        score += value * weight

    return min(max(score * 100, 0), 100)


def calculate_conversion_probability(
    features: Dict[str, float],
) -> float:
    """
    Calculate conversion probability from features.
    """
    score = 0.0
    for feature, weight in CONVERSION_FEATURE_WEIGHTS.items():
        value = features.get(feature, 0.5)
        score += value * weight

    return min(max(score * 100, 0), 100)


def predict_lead_scores(
    db: Session,
    lead_id: UUID,
) -> Dict[str, float]:
    """
    Predict booking and conversion probabilities for a lead.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        return {"error": "Lead not found"}

    # Extract features
    booking_features = extract_booking_features(db, lead)
    conversion_features = extract_conversion_features(db, lead)

    # Calculate probabilities
    booking_prob = calculate_booking_probability(booking_features)
    conversion_prob = calculate_conversion_probability(conversion_features)

    return {
        "lead_id": str(lead_id),
        "booking_probability": round(booking_prob, 1),
        "conversion_probability": round(conversion_prob, 1),
        "booking_features": {k: round(v, 3) for k, v in booking_features.items()},
        "conversion_features": {k: round(v, 3) for k, v in conversion_features.items()},
    }


def batch_predict(
    db: Session,
    tenant_id: str,
    limit: int = 100,
) -> List[Dict]:
    """
    Batch predict scores for all active leads.
    """
    leads = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.status.in_(["new", "contacted", "replied", "qualified"]),
            Lead.deleted_at.is_(None),
        )
        .limit(limit)
        .all()
    )

    results = []
    for lead in leads:
        prediction = predict_lead_scores(db, lead.id)
        prediction["lead_name"] = f"{lead.first_name} {lead.last_name}"
        prediction["current_score"] = lead.lead_score
        results.append(prediction)

    # Sort by booking probability
    results.sort(key=lambda x: x.get("booking_probability", 0), reverse=True)

    return results
