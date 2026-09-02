"""
NY-anchored slot generation + display helpers.

The AGENT's America/New_York (Eastern) availability is the sole source of truth
for slots; appointments are stored in UTC. Customer-facing times are shown in the
lead's timezone, which is Eastern for every state EXCEPT Texas (Central) — derived
from state only, no ZIP/Geoapify lookup. The agent always sees Eastern, so the two
views point at the same UTC moment (Florida is 1 hour ahead of Texas).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger(__name__)


def _valid_tz(name: Optional[str]) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


# Customer-facing display timezone. The whole system runs on Eastern (Florida)
# EXCEPT Texas leads, who see their own Central time in the SMS. Florida is 1 hour
# ahead of (most of) Texas. Agents always see Eastern, so the two stay in sync on
# the same UTC moment. Only Texas is special — every other state is Eastern.
EASTERN_TZ = "America/New_York"
TEXAS_TZ = "America/Chicago"


def lead_display_timezone(state: Optional[str]) -> str:
    """The timezone a lead's appointment times are shown in (customer-facing)."""
    if state and state.strip().upper() == "TX":
        return TEXAS_TZ
    return EASTERN_TZ


# ---- US federal holidays (computed per year so it stays correct automatically) ----
from datetime import date as _date

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _date:
    """The n-th `weekday` (Mon=0..Sun=6) of `month` in `year` (1-based n)."""
    first = _date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _date:
    nextm = _date(year + 1, 1, 1) if month == 12 else _date(year, month + 1, 1)
    last = nextm - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: _date) -> _date:
    """Federal observed rule: Sat -> the Friday before, Sun -> the Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


_holiday_cache: Dict[int, set] = {}

def us_federal_holidays(year: int) -> set:
    """Set of dates the office is closed (federal holidays + observed dates)."""
    if year in _holiday_cache:
        return _holiday_cache[year]
    days = set()
    # Fixed-date holidays (+ their observed shift when they land on a weekend).
    for d in (_date(year, 1, 1), _date(year, 6, 19), _date(year, 7, 4),
              _date(year, 11, 11), _date(year, 12, 25)):
        days.add(d)
        days.add(_observed(d))
    days.add(_nth_weekday(year, 1, 0, 3))    # MLK Day — 3rd Mon Jan
    days.add(_nth_weekday(year, 2, 0, 3))    # Washington's Birthday — 3rd Mon Feb
    days.add(_last_weekday(year, 5, 0))      # Memorial Day — last Mon May
    days.add(_nth_weekday(year, 9, 0, 1))    # Labor Day — 1st Mon Sep
    days.add(_nth_weekday(year, 10, 0, 2))   # Columbus Day — 2nd Mon Oct
    days.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving — 4th Thu Nov
    _holiday_cache[year] = days
    return days


def is_holiday(d: _date) -> bool:
    return d in us_federal_holidays(d.year)


# ---- display helpers (everything resolves to Eastern) ----
def lead_zone(tz_name: Optional[str] = None) -> ZoneInfo:
    """Return a ZoneInfo. The system is single-timezone, so this is always the
    agent/Eastern zone (settings.AGENT_TZ). The optional argument is accepted for
    backwards compatibility but only honored if it is a valid zone."""
    try:
        return ZoneInfo(tz_name) if _valid_tz(tz_name) else ZoneInfo(settings.AGENT_TZ)
    except Exception:
        return ZoneInfo(settings.AGENT_TZ)


def format_in_tz(dt_utc: datetime, tz_name: Optional[str] = None) -> str:
    """e.g. 'Wed, Jun 4 at 7:00 AM EDT'. Formats in tz_name if valid, else Eastern."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    local = dt_utc.astimezone(lead_zone(tz_name))
    try:
        return local.strftime("%a, %b %-d at %-I:%M %p %Z")
    except ValueError:
        return local.strftime("%a, %b %d at %I:%M %p %Z")


def _round_up_to_slot(dt: datetime, slot_minutes: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % slot_minutes
    if rem:
        dt += timedelta(minutes=slot_minutes - rem)
    return dt


def generate_ny_anchored_slots(
    now_utc: datetime,
    lead_tz_name: Optional[str],
    is_taken: Callable[[datetime, datetime], bool],
    count: int = 3,
    max_days: int = 14,
) -> List[Dict]:
    """
    Generate up to `count` bookable 15-min slots anchored to the AGENT's NY working
    hours (settings.AGENT_START_HOUR..AGENT_END_HOUR ET, lunch excluded).

    Each slot carries two labels for the SAME UTC moment: `label` in the lead's
    timezone (`lead_tz_name` — Central for Texas, Eastern for everyone else) for the
    customer-facing SMS, and `et_label` in Eastern for the agent. They stay in sync.

    Behavior:
      - skip weekends if settings.SCHEDULING_SKIP_WEEKENDS
      - auto-rollover to the next business day if settings.SCHEDULING_AUTO_ROLLOVER
        (if False, only today's remaining slots are offered)
      - never offer a past slot (start must be > now)
    """
    ny = ZoneInfo(settings.AGENT_TZ)
    now_ny = now_utc.astimezone(ny)
    start_hour = settings.AGENT_START_HOUR
    end_hour = settings.AGENT_END_HOUR
    lunch_start = settings.LUNCH_START_HOUR
    lunch_end = settings.LUNCH_END_HOUR
    slot_minutes = settings.SLOT_MINUTES
    skip_weekends = settings.SCHEDULING_SKIP_WEEKENDS
    skip_holidays = getattr(settings, "SCHEDULING_SKIP_HOLIDAYS", True)
    rollover = settings.SCHEDULING_AUTO_ROLLOVER

    slots: List[Dict] = []
    day0 = now_ny.date()
    days = (max_days + 1) if rollover else 1

    for offset in range(days):
        day = day0 + timedelta(days=offset)
        if skip_weekends and day.weekday() >= 5:
            continue
        if skip_holidays and is_holiday(day):
            continue
        base = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=ny)
        end = datetime(day.year, day.month, day.day, end_hour, 0, tzinfo=ny)
        t = base
        while t < end:
            slot_end = t + timedelta(minutes=slot_minutes)
            in_lunch = lunch_start <= t.hour < lunch_end
            if not in_lunch and t > now_ny:           # never past; rollover is implicit
                start_utc = t.astimezone(timezone.utc)
                end_utc = slot_end.astimezone(timezone.utc)
                if not is_taken(start_utc, end_utc):
                    et_label = format_in_tz(start_utc, settings.AGENT_TZ)
                    slots.append({
                        "index": len(slots) + 1,
                        "agent_local": t.strftime("%Y-%m-%d %H:%M ET"),
                        "start_time": start_utc.isoformat(),
                        "end_time": end_utc.isoformat(),
                        # Customer sees their own timezone (Central for TX, Eastern
                        # otherwise); the agent always sees Eastern. Same UTC moment.
                        "label": format_in_tz(start_utc, lead_tz_name),
                        "et_label": et_label,
                    })
                    if len(slots) >= count:
                        return slots
            t = slot_end
    return slots
