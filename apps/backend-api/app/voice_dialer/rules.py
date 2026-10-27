"""Dialing rules that do not need the database: calling hours.

Calls are allowed only inside the window (default 8 am – 8 pm, Florida's rule)
in the CLIENT's local time. A state spanning several time zones must be inside
the window in ALL of them; an unknown state must be inside it in every
contiguous-US zone (i.e. 11 am – 8 pm Eastern) — always the conservative side.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET, CT, MT, PT = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"
AZ, AK, HI = "America/Phoenix", "America/Anchorage", "Pacific/Honolulu"

STATE_ZONES: dict[str, tuple[str, ...]] = {
    "AL": (CT,), "AK": (AK,), "AZ": (AZ,), "AR": (CT,), "CA": (PT,), "CO": (MT,),
    "CT": (ET,), "DE": (ET,), "DC": (ET,), "FL": (ET, CT), "GA": (ET,), "HI": (HI,),
    "ID": (MT, PT), "IL": (CT,), "IN": (ET, CT), "IA": (CT,), "KS": (CT, MT),
    "KY": (ET, CT), "LA": (CT,), "ME": (ET,), "MD": (ET,), "MA": (ET,), "MI": (ET, CT),
    "MN": (CT,), "MS": (CT,), "MO": (CT,), "MT": (MT,), "NE": (CT, MT), "NV": (PT,),
    "NH": (ET,), "NJ": (ET,), "NM": (MT,), "NY": (ET,), "NC": (ET,), "ND": (CT, MT),
    "OH": (ET,), "OK": (CT,), "OR": (PT, MT), "PA": (ET,), "RI": (ET,), "SC": (ET,),
    "SD": (CT, MT), "TN": (ET, CT), "TX": (CT, MT), "UT": (MT,), "VT": (ET,), "VA": (ET,),
    "WA": (PT,), "WV": (ET,), "WI": (CT,), "WY": (MT,), "PR": ("America/Puerto_Rico",),
}
UNKNOWN_ZONES = (ET, CT, MT, PT)

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR",
}


def state_code(raw: str | None) -> str | None:
    s = (raw or "").strip().upper().replace(".", "")
    if not s:
        return None
    if s in STATE_ZONES:
        return s
    return STATE_NAMES.get(s)


def zones_for(state: str | None) -> tuple[str, ...]:
    code = state_code(state)
    return STATE_ZONES.get(code, UNKNOWN_ZONES) if code else UNKNOWN_ZONES


def in_call_window(now_utc: datetime, state: str | None, start_hour: int = 8, end_hour: int = 20) -> bool:
    """True when it is within [start_hour, end_hour) in every zone of `state`."""
    for z in zones_for(state):
        local = now_utc.astimezone(ZoneInfo(z))
        if not (start_hour <= local.hour < end_hour):
            return False
    return True
