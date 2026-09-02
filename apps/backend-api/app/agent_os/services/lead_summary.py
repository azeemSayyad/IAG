"""
AI Lead Summary Service (Step 7.3)

Generates AI summaries for agents before calls:
- Customer interest level
- Key objections raised
- Best closing angle
- Conversation highlights
"""

from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.intent.services.memory import MemoryEngine


def generate_lead_summary(
    db: Session,
    lead_id: UUID,
) -> Dict:
    """
    Generate a comprehensive lead summary for an agent.

    Returns:
        Dict with lead info, conversation summary, objections, and recommendations.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        return {"error": "Lead not found"}

    # Get conversation
    conversation = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead_id)
        .order_by(Conversation.created_at.desc())
        .first()
    )

    # Get messages
    messages = []
    if conversation:
        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
            .all()
        )

    # Get memory context
    memory_engine = MemoryEngine(db)
    memory_context = {}
    objections = []
    sentiment = {"current": "neutral", "score": 0.5}
    preferences = {}

    if conversation:
        memory_context = memory_engine.get_memory(conversation)
        objections = memory_engine.get_objections(conversation)
        sentiment = memory_engine.get_sentiment(conversation)
        preferences = memory_engine.get_preferences(conversation)

    # Build summary
    summary = {
        "lead": {
            "id": str(lead.id),
            "name": f"{lead.first_name} {lead.last_name}",
            "phone": lead.phone,
            "email": lead.email,
            "source": lead.source,
            "lead_score": lead.lead_score,
            "status": lead.status,
            "created_at": lead.created_at.isoformat(),
        },
        "conversation": {
            "total_messages": len(messages),
            "sentiment": sentiment,
            "objections": objections,
            "preferences": preferences,
        },
        "recommendations": _generate_recommendations(lead, sentiment, objections, preferences),
        "closing_angle": _determine_closing_angle(sentiment, objections, preferences),
        "interest_level": _calculate_interest_level(lead, sentiment, messages),
    }

    return summary


def _generate_recommendations(
    lead: Lead,
    sentiment: Dict,
    objections: List[Dict],
    preferences: Dict,
) -> List[str]:
    """
    Generate recommendations for the agent based on lead context.
    """
    recommendations = []

    # Based on sentiment
    if sentiment.get("current") == "negative":
        recommendations.append("Customer sentiment is negative. Be empathetic and address concerns first.")
    elif sentiment.get("current") == "positive":
        recommendations.append("Customer sentiment is positive. Push for booking/closing.")

    # Based on objections
    objection_types = [o.get("type") for o in objections]
    if "pricing" in objection_types:
        recommendations.append("Customer has pricing concerns. Emphasize value and flexible options.")
    if "trust" in objection_types:
        recommendations.append("Customer has trust concerns. Provide credentials and social proof.")
    if "timing" in objection_types:
        recommendations.append("Customer mentioned timing issues. Offer flexible scheduling.")
    if "already_covered" in objection_types:
        recommendations.append("Customer has existing coverage. Focus on comparison and potential savings.")
    if "spouse_decides" in objection_types:
        recommendations.append("Customer needs spousal approval. Offer joint call or information packet.")

    # Based on lead score
    if lead.lead_score >= 80:
        recommendations.append("High-score lead. Prioritize this call.")
    elif lead.lead_score <= 40:
        recommendations.append("Low-score lead. May need more nurturing before closing.")

    # Based on source
    if lead.source == "referral":
        recommendations.append("Referral lead. Mention the referrer if known.")
    elif lead.source in ("google", "facebook"):
        recommendations.append("Digital lead. They've shown active interest.")

    if not recommendations:
        recommendations.append("Standard approach. Build rapport and identify needs.")

    return recommendations


def _determine_closing_angle(
    sentiment: Dict,
    objections: List[Dict],
    preferences: Dict,
) -> str:
    """
    Determine the best closing angle for the call.
    """
    objection_types = [o.get("type") for o in objections]

    if sentiment.get("current") == "positive" and not objections:
        return "Direct close — customer is ready. Ask for commitment."

    if "pricing" in objection_types:
        return "Value close — emphasize ROI, flexible plans, and long-term savings."

    if "trust" in objection_types:
        return "Trust close — provide credentials, testimonials, and guarantees."

    if "timing" in objection_types:
        return "Convenience close — offer flexible scheduling and minimal time commitment."

    if "already_covered" in objection_types:
        return "Comparison close — offer free policy review and potential savings."

    if "need_to_think" in objection_types:
        return "Urgency close — highlight limited-time offers and rate changes."

    if "spouse_decides" in objection_types:
        return "Joint close — offer to include spouse in the conversation."

    return "Consultative close — identify needs and present tailored solution."


def _calculate_interest_level(
    lead: Lead,
    sentiment: Dict,
    messages: List[Message],
) -> str:
    """
    Calculate customer interest level.
    """
    score = lead.lead_score

    # Adjust based on sentiment
    if sentiment.get("current") == "positive":
        score += 10
    elif sentiment.get("current") == "negative":
        score -= 10

    # Adjust based on message count
    if len(messages) > 5:
        score += 5  # Engaged in conversation

    # Determine level
    if score >= 80:
        return "high"
    elif score >= 60:
        return "medium"
    elif score >= 40:
        return "low"
    else:
        return "very_low"


def get_conversation_highlights(
    db: Session,
    conversation_id: UUID,
    limit: int = 5,
) -> List[Dict]:
    """
    Get key conversation highlights for the agent.
    """
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit * 2)  # Get more to filter
        .all()
    )

    highlights = []
    for msg in messages:
        # Skip very short messages
        if len(msg.content) < 10:
            continue

        # Skip routine messages
        if msg.intent in ("STOP",):
            continue

        highlights.append({
            "sender": msg.sender,
            "content": msg.content[:150],
            "intent": msg.intent,
            "sentiment": msg.sentiment,
            "created_at": msg.created_at.isoformat(),
        })

        if len(highlights) >= limit:
            break

    return highlights
