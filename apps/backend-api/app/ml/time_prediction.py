"""
Best Time Prediction (Step 11.2)

Predicts:
- Best time to send outreach
- Best time to book appointments

Analyzes historical response rates by hour and day.
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.lead import Lead
from app.models.message import Message
from app.models.appointment import Appointment
from app.models.conversation import Conversation


def analyze_response_times(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> Dict[int, Dict]:
    """
    Analyze response rates by hour of day.

    Returns:
        Dict mapping hour -> {sent, replied, response_rate}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get AI messages
    ai_messages = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
            Message.created_at >= cutoff,
        )
        .all()
    )

    hourly_stats = {h: {"sent": 0, "replied": 0} for h in range(24)}

    for msg in ai_messages:
        hour = msg.created_at.hour
        hourly_stats[hour]["sent"] += 1

        # Check for reply within 24 hours
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

    # Calculate rates
    for hour, stats in hourly_stats.items():
        if stats["sent"] > 0:
            stats["response_rate"] = round(stats["replied"] / stats["sent"] * 100, 1)
        else:
            stats["response_rate"] = 0

    return hourly_stats


def analyze_day_of_week(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> Dict[str, Dict]:
    """
    Analyze response rates by day of week.
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

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    daily_stats = {day: {"sent": 0, "replied": 0} for day in day_names}

    for msg in ai_messages:
        day = day_names[msg.created_at.weekday()]
        daily_stats[day]["sent"] += 1

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
            daily_stats[day]["replied"] += 1

    for day, stats in daily_stats.items():
        if stats["sent"] > 0:
            stats["response_rate"] = round(stats["replied"] / stats["sent"] * 100, 1)
        else:
            stats["response_rate"] = 0

    return daily_stats


def analyze_appointment_times(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> Dict[int, Dict]:
    """
    Analyze appointment success rates by time of day.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= cutoff,
        )
        .all()
    )

    hourly_stats = {h: {"booked": 0, "completed": 0, "won": 0} for h in range(24)}

    for apt in appointments:
        hour = apt.start_time.hour
        hourly_stats[hour]["booked"] += 1

        if apt.status == "completed":
            hourly_stats[hour]["completed"] += 1
        if apt.disposition == "won":
            hourly_stats[hour]["won"] += 1

    for hour, stats in hourly_stats.items():
        if stats["booked"] > 0:
            stats["completion_rate"] = round(stats["completed"] / stats["booked"] * 100, 1)
            stats["win_rate"] = round(stats["won"] / stats["booked"] * 100, 1)
        else:
            stats["completion_rate"] = 0
            stats["win_rate"] = 0

    return hourly_stats


def get_best_outreach_time(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Get the best time to send outreach messages.
    """
    hourly = analyze_response_times(db, tenant_id)
    daily = analyze_day_of_week(db, tenant_id)

    # Find best hour
    best_hour = max(
        ((h, s) for h, s in hourly.items() if s["sent"] >= 5),
        key=lambda x: x[1]["response_rate"],
        default=(10, {"response_rate": 0}),
    )

    # Find best day
    best_day = max(
        ((d, s) for d, s in daily.items() if s["sent"] >= 5),
        key=lambda x: x[1]["response_rate"],
        default=("Tuesday", {"response_rate": 0}),
    )

    return {
        "best_hour": best_hour[0],
        "best_hour_rate": best_hour[1]["response_rate"],
        "best_day": best_day[0],
        "best_day_rate": best_day[1]["response_rate"],
        "hourly_data": hourly,
        "daily_data": daily,
        "recommendation": f"Send outreach on {best_day[0]}s at {best_hour[0]}:00 for best response rate ({best_hour[1]['response_rate']}%)",
    }


def get_best_appointment_time(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Get the best time to schedule appointments.
    """
    hourly = analyze_appointment_times(db, tenant_id)

    # Find best hour for completion
    best_completion = max(
        ((h, s) for h, s in hourly.items() if s["booked"] >= 3),
        key=lambda x: x[1]["completion_rate"],
        default=(10, {"completion_rate": 0}),
    )

    # Find best hour for winning
    best_win = max(
        ((h, s) for h, s in hourly.items() if s["booked"] >= 3),
        key=lambda x: x[1]["win_rate"],
        default=(10, {"win_rate": 0}),
    )

    return {
        "best_completion_hour": best_completion[0],
        "best_completion_rate": best_completion[1]["completion_rate"],
        "best_win_hour": best_win[0],
        "best_win_rate": best_win[1]["win_rate"],
        "hourly_data": hourly,
        "recommendation": f"Schedule appointments at {best_win[0]}:00 for highest win rate ({best_win[1]['win_rate']}%)",
    }


def predict_best_time_for_lead(
    db: Session,
    lead: Lead,
) -> Dict:
    """
    Predict the best outreach time for a specific lead.
    """
    # Get general best time
    general = get_best_outreach_time(db, str(lead.tenant_id))

    # Check if lead has specific patterns
    conversation = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id)
        .first()
    )

    lead_preferred_hour = general["best_hour"]
    if conversation and conversation.last_message_at:
        # If lead replied before, use that time
        last_reply = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.sender == "customer",
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if last_reply:
            lead_preferred_hour = last_reply.created_at.hour

    return {
        "lead_id": str(lead.id),
        "recommended_hour": lead_preferred_hour,
        "general_best_hour": general["best_hour"],
        "general_best_day": general["best_day"],
    }
