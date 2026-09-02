"""
No-show Prediction (Step 17.7)

Predicts the probability of a lead not showing up for their appointment.

Features used:
- Lead engagement score
- Time since booking
- Time of day
- Day of week
- Lead source
- Previous no-show history
- Confirmation status
- Reminder response

Risk levels:
- Low: < 20% probability
- Medium: 20-50% probability
- High: 50-80% probability
- Critical: > 80% probability
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message


# Feature weights for no-show prediction
FEATURE_WEIGHTS = {
    "lead_engagement": 0.20,
    "time_since_booking": 0.15,
    "time_of_day": 0.10,
    "day_of_week": 0.05,
    "lead_source": 0.10,
    "no_show_history": 0.15,
    "confirmation_status": 0.15,
    "reminder_response": 0.10,
}


def extract_no_show_features(
    db: Session,
    appointment: Appointment,
    lead: Lead,
) -> Dict[str, float]:
    """
    Extract features for no-show prediction.

    Returns dict of feature_name -> score (0-1, higher = more likely to show).
    """
    features = {}

    # 1. Lead engagement score (based on message count and recency)
    conversation = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id)
        .first()
    )

    if conversation:
        message_count = (
            db.query(func.count(Message.id))
            .filter(Message.conversation_id == conversation.id)
            .scalar()
        )
        # More messages = more engaged = more likely to show
        features["lead_engagement"] = min(1.0, message_count / 10)
    else:
        features["lead_engagement"] = 0.3  # Default for no conversation

    # 2. Time since booking (longer = more likely to forget)
    if appointment.created_at:
        hours_since_booking = (datetime.now(timezone.utc) - appointment.created_at).total_seconds() / 3600
        # Optimal: 24-48 hours, too soon or too far increases risk
        if 24 <= hours_since_booking <= 48:
            features["time_since_booking"] = 1.0
        elif hours_since_booking < 24:
            features["time_since_booking"] = 0.8  # Might be impulsive
        elif hours_since_booking <= 72:
            features["time_since_booking"] = 0.7
        else:
            features["time_since_booking"] = max(0.3, 1.0 - (hours_since_booking - 72) / 168)
    else:
        features["time_since_booking"] = 0.5

    # 3. Time of day (business hours = more likely to show)
    if appointment.start_time:
        hour = appointment.start_time.hour
        if 10 <= hour <= 18:
            features["time_of_day"] = 1.0
        elif 9 <= hour <= 20:
            features["time_of_day"] = 0.8
        else:
            features["time_of_day"] = 0.5
    else:
        features["time_of_day"] = 0.5

    # 4. Day of week (weekdays slightly better)
    if appointment.start_time:
        day = appointment.start_time.weekday()
        if day < 5:  # Monday-Friday
            features["day_of_week"] = 0.8
        else:  # Weekend
            features["day_of_week"] = 0.7
    else:
        features["day_of_week"] = 0.5

    # 5. Lead source (some sources have higher show rates)
    source_scores = {
        "referral": 0.9,
        "organic": 0.8,
        "website": 0.75,
        "csv_import": 0.6,
        "webhook": 0.65,
        "api": 0.7,
        "manual": 0.8,
    }
    features["lead_source"] = source_scores.get(lead.source, 0.6)

    # 6. No-show history
    previous_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.lead_id == lead.id,
            Appointment.status.in_(["completed", "no_show"]),
        )
        .all()
    )

    if previous_appointments:
        no_shows = sum(1 for a in previous_appointments if a.status == "no_show")
        no_show_rate = no_shows / len(previous_appointments)
        features["no_show_history"] = max(0.2, 1.0 - no_show_rate)
    else:
        features["no_show_history"] = 0.7  # Default for first appointment

    # 7. Confirmation status
    if appointment.reminder_24h_sent or appointment.reminder_1h_sent:
        features["confirmation_status"] = 0.9  # Reminders sent
    else:
        features["confirmation_status"] = 0.5  # No reminders yet

    # 8. Reminder response (based on lead's last contact)
    if lead.last_contacted_at:
        hours_since_contact = (datetime.now(timezone.utc) - lead.last_contacted_at).total_seconds() / 3600
        if hours_since_contact < 24:
            features["reminder_response"] = 0.9
        elif hours_since_contact < 48:
            features["reminder_response"] = 0.7
        else:
            features["reminder_response"] = 0.5
    else:
        features["reminder_response"] = 0.4

    return features


def predict_no_show_probability(
    features: Dict[str, float],
) -> Tuple[float, str]:
    """
    Predict no-show probability from features.

    Returns (probability, risk_level).
    probability: 0-1 (higher = more likely to NOT show)
    risk_level: "low", "medium", "high", "critical"
    """
    # Calculate weighted show probability
    show_probability = sum(
        features.get(feature, 0.5) * weight
        for feature, weight in FEATURE_WEIGHTS.items()
    )

    # Convert to no-show probability (invert)
    no_show_probability = 1.0 - show_probability

    # Determine risk level
    if no_show_probability < 0.20:
        risk_level = "low"
    elif no_show_probability < 0.50:
        risk_level = "medium"
    elif no_show_probability < 0.80:
        risk_level = "high"
    else:
        risk_level = "critical"

    return round(no_show_probability, 3), risk_level


def predict_appointment_no_show(
    db: Session,
    appointment_id: UUID,
) -> Dict:
    """
    Predict no-show probability for a specific appointment.

    Returns prediction with features and risk level.
    """
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        return {"error": "Appointment not found"}

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if not lead:
        return {"error": "Lead not found"}

    features = extract_no_show_features(db, appointment, lead)
    probability, risk_level = predict_no_show_probability(features)

    return {
        "appointment_id": str(appointment_id),
        "lead_id": str(lead.id),
        "no_show_probability": probability,
        "risk_level": risk_level,
        "features": features,
        "recommendations": get_risk_recommendations(risk_level, features),
    }


def predict_batch_no_shows(
    db: Session,
    tenant_id: UUID,
    date: Optional[datetime] = None,
) -> List[Dict]:
    """
    Predict no-show probability for all appointments on a given date.

    Useful for preemptive outreach to high-risk appointments.
    """
    if date is None:
        date = datetime.now(timezone.utc)

    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.start_time >= day_start,
            Appointment.start_time < day_end,
            Appointment.status.in_(["pending", "confirmed"]),
        )
        .all()
    )

    predictions = []
    for appt in appointments:
        prediction = predict_appointment_no_show(db, appt.id)
        if "error" not in prediction:
            predictions.append(prediction)

    # Sort by risk (highest first)
    predictions.sort(key=lambda x: x["no_show_probability"], reverse=True)

    return predictions


def get_risk_recommendations(
    risk_level: str,
    features: Dict[str, float],
) -> List[str]:
    """
    Get recommendations based on risk level and feature scores.
    """
    recommendations = []

    if risk_level in ["high", "critical"]:
        recommendations.append("Send confirmation SMS 2 hours before appointment")
        recommendations.append("Consider calling to confirm 1 hour before")

    if features.get("lead_engagement", 1.0) < 0.5:
        recommendations.append("Engagement is low - send a reminder about appointment value")

    if features.get("time_since_booking", 1.0) < 0.5:
        recommendations.append("Long gap since booking - send day-before reminder")

    if features.get("no_show_history", 1.0) < 0.5:
        recommendations.append("Lead has no-show history - consider requiring confirmation")

    if risk_level == "critical":
        recommendations.append("Consider overbooking this time slot")

    if not recommendations:
        recommendations.append("Low risk - standard reminders should suffice")

    return recommendations
