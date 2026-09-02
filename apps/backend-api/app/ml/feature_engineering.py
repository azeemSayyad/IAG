"""
ML Feature Engineering (Step 21.2)

Transforms raw data into ML-ready features.

Features:
- Source features — Lead source encoding
- Campaign features — Campaign performance
- Timezone features — Geographic time features
- Response delay features — Timing-based features
- Appointment timing features — Booking time features
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import math


# Source encoding (one-hot style scores)
SOURCE_FEATURES = {
    "referral": {"quality": 0.9, "volume": 0.3, "cost": 0.1},
    "organic": {"quality": 0.8, "volume": 0.5, "cost": 0.0},
    "website": {"quality": 0.7, "volume": 0.7, "cost": 0.2},
    "csv_import": {"quality": 0.5, "volume": 0.9, "cost": 0.1},
    "webhook": {"quality": 0.6, "volume": 0.8, "cost": 0.3},
    "api": {"quality": 0.65, "volume": 0.6, "cost": 0.2},
    "manual": {"quality": 0.75, "volume": 0.2, "cost": 0.5},
}

# State value scores (insurance market value)
STATE_FEATURES = {
    "CA": 0.9, "TX": 0.85, "FL": 0.8, "NY": 0.9, "IL": 0.7,
    "PA": 0.7, "OH": 0.65, "GA": 0.7, "NC": 0.65, "MI": 0.6,
    "NJ": 0.75, "VA": 0.7, "WA": 0.7, "AZ": 0.65, "MA": 0.75,
    "TN": 0.6, "IN": 0.55, "MO": 0.55, "MD": 0.7, "WI": 0.55,
    "CO": 0.7, "MN": 0.6, "SC": 0.55, "AL": 0.5, "LA": 0.55,
    "KY": 0.5, "OR": 0.65, "OK": 0.5, "CT": 0.7, "UT": 0.6,
    "IA": 0.45, "NV": 0.6, "AR": 0.45, "MS": 0.4, "KS": 0.45,
    "NM": 0.45, "NE": 0.4, "ID": 0.45, "WV": 0.4, "HI": 0.6,
    "NH": 0.55, "ME": 0.5, "MT": 0.4, "RI": 0.55, "DE": 0.5,
    "SD": 0.35, "ND": 0.35, "AK": 0.5, "VT": 0.45, "WY": 0.35,
}


def extract_source_features(source: str) -> Dict[str, float]:
    """
    Extract features from lead source.

    Returns:
        quality: Source quality score (0-1)
        volume: Expected volume (0-1)
        cost: Relative cost (0-1)
    """
    return SOURCE_FEATURES.get(source, {"quality": 0.5, "volume": 0.5, "cost": 0.3})


def extract_state_features(state: Optional[str]) -> Dict[str, float]:
    """
    Extract features from lead state.

    Returns:
        market_value: State market value score (0-1)
    """
    if not state:
        return {"market_value": 0.5}

    return {"market_value": STATE_FEATURES.get(state.upper(), 0.5)}


def extract_time_features(dt: datetime) -> Dict[str, float]:
    """
    Extract time-based features from datetime.

    Returns:
        hour_sin: Cyclical hour encoding (sin)
        hour_cos: Cyclical hour encoding (cos)
        day_sin: Cyclical day encoding (sin)
        day_cos: Cyclical day encoding (cos)
        is_business_hours: Whether within business hours
        is_weekend: Whether weekend
    """
    hour = dt.hour + dt.minute / 60
    day = dt.weekday()

    return {
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
        "day_sin": math.sin(2 * math.pi * day / 7),
        "day_cos": math.cos(2 * math.pi * day / 7),
        "is_business_hours": 1.0 if 10 <= dt.hour <= 20 else 0.0,
        "is_weekend": 1.0 if day >= 5 else 0.0,
    }


def extract_response_delay_features(
    first_contact_at: Optional[datetime],
    first_reply_at: Optional[datetime],
) -> Dict[str, float]:
    """
    Extract response delay features.

    Returns:
        response_delay_hours: Hours until first reply
        response_delay_bucket: Categorical bucket (0-4)
        is_quick_response: Whether replied within 1 hour
    """
    if not first_contact_at or not first_reply_at:
        return {
            "response_delay_hours": -1,
            "response_delay_bucket": -1,
            "is_quick_response": 0.0,
        }

    delay_hours = (first_reply_at - first_contact_at).total_seconds() / 3600

    # Bucket: 0=<1h, 1=1-4h, 2=4-24h, 3=24-72h, 4=>72h
    if delay_hours < 1:
        bucket = 0
    elif delay_hours < 4:
        bucket = 1
    elif delay_hours < 24:
        bucket = 2
    elif delay_hours < 72:
        bucket = 3
    else:
        bucket = 4

    return {
        "response_delay_hours": min(delay_hours, 168),  # Cap at 1 week
        "response_delay_bucket": bucket,
        "is_quick_response": 1.0 if delay_hours < 1 else 0.0,
    }


def extract_appointment_timing_features(
    booked_at: datetime,
    appointment_time: datetime,
) -> Dict[str, float]:
    """
    Extract appointment timing features.

    Returns:
        days_ahead: Days between booking and appointment
        hour_of_day: Appointment hour
        is_morning: Whether morning appointment
        is_afternoon: Whether afternoon appointment
    """
    days_ahead = (appointment_time - booked_at).days

    return {
        "days_ahead": min(days_ahead, 14),  # Cap at 2 weeks
        "hour_of_day": appointment_time.hour,
        "is_morning": 1.0 if 10 <= appointment_time.hour < 13 else 0.0,
        "is_afternoon": 1.0 if 13 <= appointment_time.hour < 17 else 0.0,
        "is_evening": 1.0 if 17 <= appointment_time.hour <= 20 else 0.0,
    }


def extract_engagement_features(
    message_count: int,
    customer_messages: int,
    ai_messages: int,
    conversation_duration_hours: float,
) -> Dict[str, float]:
    """
    Extract engagement features.

    Returns:
        total_messages: Total message count
        customer_engagement: Customer message ratio
        messages_per_hour: Message rate
        is_highly_engaged: Whether highly engaged
    """
    if message_count == 0:
        return {
            "total_messages": 0,
            "customer_engagement": 0,
            "messages_per_hour": 0,
            "is_highly_engaged": 0,
        }

    customer_ratio = customer_messages / message_count if message_count > 0 else 0
    messages_per_hour = message_count / max(conversation_duration_hours, 0.1)

    return {
        "total_messages": min(message_count, 50),  # Cap at 50
        "customer_engagement": customer_ratio,
        "messages_per_hour": min(messages_per_hour, 20),  # Cap at 20/hour
        "is_highly_engaged": 1.0 if customer_messages >= 5 else 0.0,
    }


def extract_lead_features(lead_data: Dict) -> Dict[str, float]:
    """
    Extract all features for a lead.

    Combines source, state, time, and engagement features.
    """
    features = {}

    # Source features
    source = lead_data.get("source", "unknown")
    features.update(extract_source_features(source))

    # State features
    state = lead_data.get("state")
    features.update(extract_state_features(state))

    # Time features
    created_at = lead_data.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        features.update(extract_time_features(created_at))

    # Response delay features
    first_contact = lead_data.get("first_contact_at")
    first_reply = lead_data.get("first_reply_at")
    if first_contact and first_reply:
        if isinstance(first_contact, str):
            first_contact = datetime.fromisoformat(first_contact.replace("Z", "+00:00"))
        if isinstance(first_reply, str):
            first_reply = datetime.fromisoformat(first_reply.replace("Z", "+00:00"))
        features.update(extract_response_delay_features(first_contact, first_reply))

    # Engagement features
    features.update(extract_engagement_features(
        message_count=lead_data.get("message_count", 0),
        customer_messages=lead_data.get("customer_messages", 0),
        ai_messages=lead_data.get("ai_messages", 0),
        conversation_duration_hours=lead_data.get("conversation_duration_hours", 0),
    ))

    # Lead score (if available)
    features["lead_score"] = lead_data.get("lead_score", 50) / 100

    return features


def extract_appointment_features(appt_data: Dict) -> Dict[str, float]:
    """
    Extract all features for an appointment.
    """
    features = {}

    # Timing features
    booked_at = appt_data.get("created_at")
    appointment_time = appt_data.get("start_time")

    if booked_at and appointment_time:
        if isinstance(booked_at, str):
            booked_at = datetime.fromisoformat(booked_at.replace("Z", "+00:00"))
        if isinstance(appointment_time, str):
            appointment_time = datetime.fromisoformat(appointment_time.replace("Z", "+00:00"))
        features.update(extract_appointment_timing_features(booked_at, appointment_time))

    # Source features
    lead_source = appt_data.get("lead_source")
    if lead_source:
        features.update(extract_source_features(lead_source))

    # AI confidence
    features["ai_confidence"] = appt_data.get("ai_confidence", 0.5)

    # Lead score
    features["lead_score"] = (appt_data.get("lead_score", 50) or 50) / 100

    return features


def prepare_training_features(
    lead_outcomes: List[Dict],
    conversation_features: List[Dict],
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    """
    Prepare features for model training.

    Returns:
        X: List of feature dictionaries
        y: List of label dictionaries
    """
    X = []
    y = []

    # Build conversation lookup
    conv_lookup = {}
    for conv in conversation_features:
        conv_lookup[conv["lead_id"]] = conv

    for outcome in lead_outcomes:
        lead_id = outcome["lead_id"]

        # Get conversation features if available
        conv = conv_lookup.get(lead_id, {})

        # Combine features
        features = extract_lead_features({
            **outcome,
            "message_count": conv.get("message_count", 0),
            "customer_messages": conv.get("customer_message_count", 0),
            "ai_messages": conv.get("ai_message_count", 0),
            "conversation_duration_hours": conv.get("duration_hours", 0),
        })

        X.append(features)

        # Labels
        y.append({
            "replied": 1.0 if outcome.get("replied") else 0.0,
            "booked": 1.0 if outcome.get("booked") else 0.0,
            "converted": 1.0 if outcome.get("converted") else 0.0,
            "no_show": 1.0 if outcome.get("no_show") else 0.0,
        })

    return X, y
