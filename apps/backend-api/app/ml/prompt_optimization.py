"""
Prompt Optimization (Step 11.4)

Learns:
- Best wording patterns
- Best messaging structure
- Best objection handling approaches
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.message import Message
from app.models.conversation import Conversation
from app.models.appointment import Appointment


def analyze_message_patterns(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> Dict:
    """
    Analyze which message patterns get the best responses.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    ai_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
            Message.created_at >= cutoff,
        )
        .all()
    )

    patterns = {
        "greeting": {"sent": 0, "replied": 0, "booked": 0},
        "question": {"sent": 0, "replied": 0, "booked": 0},
        "value_prop": {"sent": 0, "replied": 0, "booked": 0},
        "urgency": {"sent": 0, "replied": 0, "booked": 0},
        "social_proof": {"sent": 0, "replied": 0, "booked": 0},
        "direct": {"sent": 0, "replied": 0, "booked": 0},
    }

    for msg in ai_messages:
        lower = msg.content.lower()
        pattern = _classify_message_pattern(lower)

        patterns[pattern]["sent"] += 1

        # Check for reply
        reply = (
            db.query(Message)
            .filter(
                Message.conversation_id == msg.conversation_id,
                Message.sender == "customer",
                Message.created_at > msg.created_at,
                Message.created_at < msg.created_at + timedelta(hours=24),
            )
            .first()
        )

        if reply:
            patterns[pattern]["replied"] += 1

            # Check for eventual booking
            conversation = db.query(Conversation).filter(
                Conversation.id == msg.conversation_id
            ).first()

            if conversation:
                appointment = (
                    db.query(Appointment)
                    .filter(
                        Appointment.lead_id == conversation.lead_id,
                        Appointment.status.in_(["confirmed", "completed"]),
                    )
                    .first()
                )
                if appointment:
                    patterns[pattern]["booked"] += 1

    # Calculate rates
    for pattern, stats in patterns.items():
        if stats["sent"] > 0:
            stats["reply_rate"] = round(stats["replied"] / stats["sent"] * 100, 1)
            stats["booking_rate"] = round(stats["booked"] / stats["sent"] * 100, 1)
        else:
            stats["reply_rate"] = 0
            stats["booking_rate"] = 0

    return patterns


def _classify_message_pattern(content: str) -> str:
    """Classify a message into a pattern category."""
    if any(kw in content for kw in ["hey", "hi", "hello", "greetings"]):
        return "greeting"
    elif any(kw in content for kw in ["?", "what", "how", "when", "interested"]):
        return "question"
    elif any(kw in content for kw in ["save", "benefit", "coverage", "protect"]):
        return "value_prop"
    elif any(kw in content for kw in ["limited", "hurry", "soon", "last chance", "expires"]):
        return "urgency"
    elif any(kw in content for kw in ["helped", "customers", "families", "thousands"]):
        return "social_proof"
    else:
        return "direct"


def analyze_objection_responses(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> Dict:
    """
    Analyze which objection handling approaches work best.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get conversations with SKEPTICAL intent
    skeptical_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.intent == "SKEPTICAL",
            Message.created_at >= cutoff,
        )
        .all()
    )

    objection_types = {
        "pricing": {"raised": 0, "overcome": 0},
        "trust": {"raised": 0, "overcome": 0},
        "timing": {"raised": 0, "overcome": 0},
        "already_covered": {"raised": 0, "overcome": 0},
        "need_to_think": {"raised": 0, "overcome": 0},
    }

    for msg in skeptical_messages:
        lower = msg.content.lower()
        objection_type = _classify_objection(lower)

        objection_types[objection_type]["raised"] += 1

        # Check if objection was overcome (positive intent later)
        positive_reply = (
            db.query(Message)
            .filter(
                Message.conversation_id == msg.conversation_id,
                Message.sender == "customer",
                Message.intent.in_(["POSITIVE", "BOOK_NOW", "INTERESTED"]),
                Message.created_at > msg.created_at,
            )
            .first()
        )

        if positive_reply:
            objection_types[objection_type]["overcome"] += 1

    # Calculate rates
    for obj_type, stats in objection_types.items():
        if stats["raised"] > 0:
            stats["overcome_rate"] = round(stats["overcome"] / stats["raised"] * 100, 1)
        else:
            stats["overcome_rate"] = 0

    return objection_types


def _classify_objection(content: str) -> str:
    """Classify objection type from message content."""
    if any(kw in content for kw in ["expensive", "cost", "price", "afford", "budget"]):
        return "pricing"
    elif any(kw in content for kw in ["scam", "fake", "trust", "real", "legit"]):
        return "trust"
    elif any(kw in content for kw in ["busy", "time", "later", "not now"]):
        return "timing"
    elif any(kw in content for kw in ["already", "have insurance", "covered"]):
        return "already_covered"
    elif any(kw in content for kw in ["think", "consider", "research", "maybe"]):
        return "need_to_think"
    return "trust"  # Default


def get_optimization_recommendations(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Generate optimization recommendations based on data analysis.
    """
    patterns = analyze_message_patterns(db, tenant_id)
    objections = analyze_objection_responses(db, tenant_id)

    recommendations = []

    # Find best performing pattern
    best_pattern = max(
        patterns.items(),
        key=lambda x: x[1]["reply_rate"],
        default=("direct", {"reply_rate": 0}),
    )

    if best_pattern[1]["reply_rate"] > 0:
        recommendations.append({
            "type": "messaging",
            "priority": "high",
            "recommendation": f"Use more '{best_pattern[0]}' style messages - they have {best_pattern[1]['reply_rate']}% response rate",
            "data": best_pattern[1],
        })

    # Find worst performing pattern
    worst_pattern = min(
        ((k, v) for k, v in patterns.items() if v["sent"] > 10),
        key=lambda x: x[1]["reply_rate"],
        default=("direct", {"reply_rate": 100}),
    )

    if worst_pattern[1]["reply_rate"] < 20:
        recommendations.append({
            "type": "messaging",
            "priority": "medium",
            "recommendation": f"Reduce '{worst_pattern[0]}' style messages - only {worst_pattern[1]['reply_rate']}% response rate",
            "data": worst_pattern[1],
        })

    # Find hardest objection to overcome
    hardest_objection = max(
        ((k, v) for k, v in objections.items() if v["raised"] > 5),
        key=lambda x: x[1]["raised"] - x[1]["overcome"],
        default=("trust", {"raised": 0, "overcome": 0}),
    )

    if hardest_objection[1]["raised"] > 0:
        overcome_rate = hardest_objection[1].get("overcome_rate", 0)
        recommendations.append({
            "type": "objection_handling",
            "priority": "high",
            "recommendation": f"Improve '{hardest_objection[0]}' objection handling - only {overcome_rate}% overcome rate",
            "data": hardest_objection[1],
        })

    return {
        "patterns": patterns,
        "objections": objections,
        "recommendations": recommendations,
    }
