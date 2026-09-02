"""
Lead Scoring ML Engine (Step 3.4)

Inputs:
- Lead source
- State/geography
- Timing (business hours vs off-hours)
- Prior engagement
- Data completeness

Outputs:
- Lead score (0-100)
- Booking probability (0-100)
- Conversion probability (0-100)

Uses a weighted scoring model. Can be upgraded to ML model later.
"""

from datetime import datetime, timezone
from typing import Dict, Optional


# Source quality weights (0-100)
SOURCE_WEIGHTS = {
    "referral": 90,
    "organic": 80,
    "google": 75,
    "facebook": 65,
    "webhook": 60,
    "api": 55,
    "csv_import": 50,
    "manual": 45,
    "cold": 30,
}

# High-conversion states (insurance market hotspots)
HIGH_VALUE_STATES = {
    "FL": 85, "TX": 85, "CA": 80, "NY": 80, "NJ": 75,
    "PA": 70, "OH": 70, "IL": 70, "GA": 70, "NC": 65,
}

# Default state score
DEFAULT_STATE_SCORE = 50


def score_source(source: str) -> int:
    """Score based on lead source quality."""
    return SOURCE_WEIGHTS.get(source.lower().strip(), 50)


def score_state(state: Optional[str]) -> int:
    """Score based on geographic location."""
    if not state:
        return DEFAULT_STATE_SCORE
    return HIGH_VALUE_STATES.get(state.upper().strip(), DEFAULT_STATE_SCORE)


def score_timing(created_at: datetime) -> int:
    """
    Score based on when the lead was created.
    Business hours (9 AM - 6 PM) get higher scores.
    Weekdays get higher scores than weekends.
    """
    if created_at is None:
        return 50

    # Convert to local time if needed
    hour = created_at.hour
    weekday = created_at.weekday()  # 0=Monday, 6=Sunday

    # Time score
    if 9 <= hour <= 18:
        time_score = 80  # Business hours
    elif 7 <= hour <= 21:
        time_score = 60  # Extended hours
    else:
        time_score = 40  # Off hours

    # Day score
    if weekday < 5:
        day_score = 70  # Weekday
    else:
        day_score = 50  # Weekend

    return (time_score + day_score) // 2


def score_completeness(lead_data: dict) -> int:
    """Score based on data completeness."""
    fields = {
        "first_name": 20,
        "last_name": 20,
        "phone": 25,
        "email": 15,
        "state": 10,
        "city": 5,
        "zip_code": 5,
    }

    score = 0
    for field, weight in fields.items():
        if lead_data.get(field):
            score += weight

    return min(score, 100)


def score_engagement(
    message_count: int = 0,
    has_replied: bool = False,
    response_time_seconds: Optional[int] = None,
) -> int:
    """
    Score based on prior engagement.
    Higher engagement = higher score.
    """
    score = 20  # Base engagement score

    if has_replied:
        score += 30

    if message_count > 0:
        score += min(message_count * 5, 25)

    if response_time_seconds is not None:
        if response_time_seconds < 300:  # < 5 min
            score += 25
        elif response_time_seconds < 3600:  # < 1 hour
            score += 15
        elif response_time_seconds < 86400:  # < 24 hours
            score += 5

    return min(score, 100)


def calculate_lead_score(
    lead_data: dict,
    created_at: Optional[datetime] = None,
    message_count: int = 0,
    has_replied: bool = False,
    response_time_seconds: Optional[int] = None,
) -> int:
    """
    Calculate composite lead score (0-100).

    Formula:
    lead_score = (
        source_weight * 0.25 +
        state_score * 0.15 +
        timing_score * 0.15 +
        completeness_score * 0.20 +
        engagement_score * 0.25
    )
    """
    source_score = score_source(lead_data.get("source", "unknown"))
    state_score = score_state(lead_data.get("state"))
    timing_score = score_timing(created_at or datetime.now(timezone.utc))
    completeness_score = score_completeness(lead_data)
    engagement_score = score_engagement(message_count, has_replied, response_time_seconds)

    composite = (
        source_score * 0.25
        + state_score * 0.15
        + timing_score * 0.15
        + completeness_score * 0.20
        + engagement_score * 0.25
    )

    return min(int(composite), 100)


def calculate_booking_probability(lead_score: int, has_replied: bool = False) -> int:
    """
    Estimate booking probability based on lead score and engagement.
    """
    base = lead_score * 0.6
    if has_replied:
        base += 25
    return min(int(base), 100)


def calculate_conversion_probability(
    lead_score: int,
    has_replied: bool = False,
    has_booked: bool = False,
) -> int:
    """
    Estimate conversion probability.
    """
    base = lead_score * 0.4
    if has_replied:
        base += 20
    if has_booked:
        base += 25
    return min(int(base), 100)


def get_score_tier(score: int) -> str:
    """
    Categorize lead into a tier based on score.

    80-100: Hot — immediate outreach, best agents
    60-79:  Warm — standard outreach
    40-59:  Cool — slower cadence
    0-39:   Cold — nurture campaign
    """
    if score >= 80:
        return "hot"
    elif score >= 60:
        return "warm"
    elif score >= 40:
        return "cool"
    else:
        return "cold"
