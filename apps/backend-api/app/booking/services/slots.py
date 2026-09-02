"""
Slot Generation Engine (Step 6.1)

Rules:
- Business hours: 10 AM to 9 PM
- Slot duration: 15 minutes
- 4 appointments per hour
- Book up to 3 days ahead
"""

from datetime import datetime, timedelta, time, timezone, date
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.core.config import settings

# Configuration — aligned with the agent ET availability rules (single source of
# truth = settings). Agent works 10:00-19:00 ET, lunch 14:00-15:00 excluded, 15-min.
BUSINESS_START_HOUR = settings.AGENT_START_HOUR  # 10 AM ET
BUSINESS_END_HOUR = settings.AGENT_END_HOUR      # 7 PM ET (last slot start 18:45)
LUNCH_START_HOUR = settings.LUNCH_START_HOUR     # 2 PM ET
LUNCH_END_HOUR = settings.LUNCH_END_HOUR         # 3 PM ET
SLOT_DURATION_MINUTES = settings.SLOT_MINUTES
MAX_DAYS_AHEAD = 5
SLOTS_PER_HOUR = 60 // SLOT_DURATION_MINUTES  # 4


@dataclass
class TimeSlot:
    """Represents a bookable time slot."""
    start_time: datetime
    end_time: datetime
    is_available: bool = True
    agent_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "start_display": self.start_time.strftime("%I:%M %p").lstrip("0"),
            "end_display": self.end_time.strftime("%I:%M %p").lstrip("0"),
            "is_available": self.is_available,
            "agent_id": self.agent_id,
        }

    @property
    def key(self) -> str:
        """Unique key for this slot."""
        return f"{self.start_time.strftime('%Y%m%d_%H%M')}"


def generate_slots_for_date(
    target_date: date,
    timezone_offset: int = 0,
) -> List[TimeSlot]:
    """
    Generate all possible time slots for a given date.

    Args:
        target_date: The date to generate slots for
        timezone_offset: Hours offset from UTC

    Returns:
        List of TimeSlot objects
    """
    slots = []
    # Slots are anchored to the AGENT's New York timezone (agent ET availability is
    # the source of truth). `timezone_offset` is kept for backward-compat but ignored.
    tz = ZoneInfo(settings.AGENT_TZ)
    now = datetime.now(timezone.utc)

    # Skip weekends if configured (no availability on Sat/Sun).
    if settings.SCHEDULING_SKIP_WEEKENDS and target_date.weekday() >= 5:
        return slots

    start_hour = BUSINESS_START_HOUR
    end_hour = BUSINESS_END_HOUR

    current = datetime.combine(target_date, time(start_hour, 0), tzinfo=tz)
    end = datetime.combine(target_date, time(end_hour, 0), tzinfo=tz)

    while current + timedelta(minutes=SLOT_DURATION_MINUTES) <= end:
        slot_end = current + timedelta(minutes=SLOT_DURATION_MINUTES)
        in_lunch = LUNCH_START_HOUR <= current.hour < LUNCH_END_HOUR      # exclude 14:00-14:45 ET
        if not in_lunch and current.astimezone(timezone.utc) > now:       # never offer past slots
            slots.append(TimeSlot(start_time=current, end_time=slot_end))
        current = slot_end

    return slots


def generate_slots_for_range(
    start_date: date,
    days: int = MAX_DAYS_AHEAD,
    timezone_offset: int = 0,
) -> Dict[date, List[TimeSlot]]:
    """
    Generate slots for multiple days.

    Args:
        start_date: Start date
        days: Number of days to generate
        timezone_offset: Hours offset from UTC

    Returns:
        Dict mapping date to list of TimeSlots
    """
    result = {}
    for i in range(days):
        target_date = start_date + timedelta(days=i)
        result[target_date] = generate_slots_for_date(target_date, timezone_offset)
    return result


def get_available_slots(
    all_slots: List[TimeSlot],
    booked_slots: List[Tuple[datetime, datetime]],
    locked_slots: List[str] = None,
) -> List[TimeSlot]:
    """
    Filter slots to only available ones.

    Args:
        all_slots: All generated slots
        booked_slots: List of (start, end) tuples for booked slots
        locked_slots: List of slot keys that are locked

    Returns:
        List of available TimeSlots
    """
    locked_slots = locked_slots or []
    available = []

    for slot in all_slots:
        # Check if slot overlaps with any booked slot
        is_booked = False
        for booked_start, booked_end in booked_slots:
            if slot.start_time < booked_end and slot.end_time > booked_start:
                is_booked = True
                break

        # Check if slot is locked
        is_locked = slot.key in locked_slots

        if not is_booked and not is_locked:
            slot.is_available = True
            available.append(slot)
        else:
            slot.is_available = False

    return available


def format_slot_options(slots: List[TimeSlot], count: int = 3) -> List[Dict]:
    """
    Format slots as numbered options for customer.

    Returns:
        List of dicts with number, display text, and slot data
    """
    options = []
    for i, slot in enumerate(slots[:count], start=1):
        options.append({
            "number": i,
            "display": slot.start_time.strftime("%I:%M %p").lstrip("0"),
            "date_display": slot.start_time.strftime("%A, %B %d"),
            "start_time": slot.start_time.isoformat(),
            "end_time": slot.end_time.isoformat(),
            "slot_key": slot.key,
        })
    return options


def parse_slot_selection(
    reply: str,
    options: List[Dict],
) -> Optional[Dict]:
    """
    Parse a customer's slot selection from their reply.

    Supports:
    - Number: "2", "2."
    - Text: "11:30 AM", "11:30"

    Returns:
        Selected option dict or None if invalid
    """
    reply = reply.strip().lower()

    # Try number selection
    for opt in options:
        if reply in (str(opt["number"]), f"{opt['number']}.", f"option {opt['number']}"):
            return opt

    # Try time text matching
    for opt in options:
        display_lower = opt["display"].lower()
        if reply in display_lower or display_lower in reply:
            return opt

    # Try parsing as time
    try:
        # Handle formats like "11:30", "11:30 AM", "11:30AM"
        for fmt in ["%I:%M %p", "%I:%M%p", "%H:%M", "%I %p"]:
            try:
                parsed_time = datetime.strptime(reply, fmt).time()
                for opt in options:
                    slot_time = datetime.fromisoformat(opt["start_time"]).time()
                    if slot_time.hour == parsed_time.hour and slot_time.minute == parsed_time.minute:
                        return opt
            except ValueError:
                continue
    except Exception:
        pass

    return None
