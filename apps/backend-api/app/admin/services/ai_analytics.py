"""
AI Analytics Service (Step 9.3)

Measure:
- Best performing prompts
- Best outreach timing
- Best conversation flows
- Intent distribution
- Objection patterns
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.models.campaign import Campaign


def get_ai_performance_metrics(
    db: Session,
    tenant_id: str,
    start_date: date = None,
    end_date: date = None,
) -> Dict:
    """
    Get AI performance metrics.
    """
    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    # Message stats
    total_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )

    ai_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )

    customer_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "customer",
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )

    # Intent distribution
    intent_counts = (
        db.query(
            Message.intent,
            func.count(Message.id).label("count"),
        )
        .filter(
            Message.tenant_id == tenant_id,
            Message.intent.isnot(None),
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .group_by(Message.intent)
        .all()
    )

    intent_distribution = {intent: count for intent, count in intent_counts}

    # Conversation stats
    total_conversations = (
        db.query(Conversation)
        .filter(
            Conversation.tenant_id == tenant_id,
            Conversation.created_at >= start_dt,
            Conversation.created_at < end_dt,
        )
        .count()
    )

    active_conversations = (
        db.query(Conversation)
        .filter(
            Conversation.tenant_id == tenant_id,
            Conversation.status.in_(["active", "initiated", "booking"]),
        )
        .count()
    )

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "messages": {
            "total": total_messages,
            "ai_sent": ai_messages,
            "customer_received": customer_messages,
            "engagement_rate": round(customer_messages / ai_messages * 100, 1) if ai_messages > 0 else 0,
        },
        "conversations": {
            "total": total_conversations,
            "active": active_conversations,
        },
        "intent_distribution": intent_distribution,
    }


def get_best_performing_prompts(
    db: Session,
    tenant_id: str,
    limit: int = 5,
) -> List[Dict]:
    """
    Analyze which prompts/messages get the best response rates.
    """
    # Get AI messages with customer replies
    ai_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
        )
        .order_by(Message.created_at.desc())
        .limit(1000)
        .all()
    )

    # Analyze response rates by message patterns
    prompt_performance = {}

    for msg in ai_messages:
        # Get the next customer message in the same conversation
        customer_reply = (
            db.query(Message)
            .filter(
                Message.conversation_id == msg.conversation_id,
                Message.sender == "customer",
                Message.created_at > msg.created_at,
            )
            .order_by(Message.created_at)
            .first()
        )

        # Categorize the message
        category = _categorize_message(msg.content)

        if category not in prompt_performance:
            prompt_performance[category] = {
                "total_sent": 0,
                "replies_received": 0,
                "sample_message": msg.content[:100],
            }

        prompt_performance[category]["total_sent"] += 1
        if customer_reply:
            prompt_performance[category]["replies_received"] += 1

    # Calculate response rates and sort
    results = []
    for category, stats in prompt_performance.items():
        if stats["total_sent"] > 0:
            response_rate = round(stats["replies_received"] / stats["total_sent"] * 100, 1)
            results.append({
                "category": category,
                "total_sent": stats["total_sent"],
                "replies_received": stats["replies_received"],
                "response_rate": response_rate,
                "sample_message": stats["sample_message"],
            })

    results.sort(key=lambda x: x["response_rate"], reverse=True)
    return results[:limit]


def get_best_outreach_timing(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Analyze best times to send outreach messages.
    """
    # Get messages with replies
    messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
        )
        .all()
    )

    hourly_stats = {h: {"sent": 0, "replied": 0} for h in range(24)}
    daily_stats = {d: {"sent": 0, "replied": 0} for d in range(7)}

    for msg in messages:
        hour = msg.created_at.hour
        day = msg.created_at.weekday()

        hourly_stats[hour]["sent"] += 1
        daily_stats[day]["sent"] += 1

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
            hourly_stats[hour]["replied"] += 1
            daily_stats[day]["replied"] += 1

    # Calculate response rates
    hourly_rates = {}
    for hour, stats in hourly_stats.items():
        if stats["sent"] > 0:
            hourly_rates[hour] = round(stats["replied"] / stats["sent"] * 100, 1)

    daily_rates = {}
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for day, stats in daily_stats.items():
        if stats["sent"] > 0:
            daily_rates[day_names[day]] = round(stats["replied"] / stats["sent"] * 100, 1)

    # Find best times
    best_hour = max(hourly_rates, key=hourly_rates.get) if hourly_rates else None
    best_day = max(daily_rates, key=daily_rates.get) if daily_rates else None

    return {
        "hourly_response_rates": hourly_rates,
        "daily_response_rates": daily_rates,
        "best_hour": best_hour,
        "best_day": best_day,
        "recommendation": f"Best time to send outreach: {best_day}s at {best_hour}:00" if best_hour and best_day else "Not enough data",
    }


def get_objection_patterns(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Analyze objection patterns.
    """
    # Get messages with SKEPTICAL intent
    skeptical_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.intent == "SKEPTICAL",
        )
        .all()
    )

    objection_keywords = {
        "pricing": ["expensive", "cost", "price", "afford", "budget", "cheap"],
        "trust": ["scam", "fake", "trust", "real", "legit", "believe"],
        "timing": ["busy", "time", "later", "not now", "schedule"],
        "already_covered": ["already", "have insurance", "covered", "current"],
        "need_to_think": ["think", "consider", "research", "maybe later"],
    }

    objection_counts = {k: 0 for k in objection_keywords}

    for msg in skeptical_messages:
        lower = msg.content.lower()
        for objection_type, keywords in objection_keywords.items():
            if any(kw in lower for kw in keywords):
                objection_counts[objection_type] += 1

    total_objections = sum(objection_counts.values())

    return {
        "total_objections": total_objections,
        "objection_distribution": objection_counts,
        "objection_rates": {
            k: round(v / total_objections * 100, 1) if total_objections > 0 else 0
            for k, v in objection_counts.items()
        },
        "top_objection": max(objection_counts, key=objection_counts.get) if total_objections > 0 else None,
    }


def _categorize_message(content: str) -> str:
    """Categorize a message by its content type."""
    lower = content.lower()

    if any(kw in lower for kw in ["hey", "hi", "hello", "reaching out"]):
        return "initial_outreach"
    elif any(kw in lower for kw in ["checking in", "just following up", "wanted to"]):
        return "follow_up"
    elif any(kw in lower for kw in ["available", "time", "slot", "book"]):
        return "booking"
    elif any(kw in lower for kw in ["missed", "reschedule", "new time"]):
        return "reschedule"
    elif any(kw in lower for kw in ["thank", "excited", "great"]):
        return "confirmation"
    else:
        return "general"
