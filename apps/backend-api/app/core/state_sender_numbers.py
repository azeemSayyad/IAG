"""Per-state outbound sender numbers.

A lead is messaged from a dedicated number that matches the lead's US state, so
the caller-ID state lines up with the lead (Florida lead -> Florida number,
Texas lead -> Texas number). Only ACTIVE numbers are listed here.

Update this map as numbers go Active in the provider dashboard. States not
listed (or with an empty list) fall back to the global ENGAGECLOUD_FROM_NUMBERS
pool — so adding a new state's numbers here is all that's needed to turn it on.
"""
from __future__ import annotations

from typing import List

# Active dedicated SMS numbers, in E.164. Keyed by 2-letter US state code.
# ⚠️ PREVIOUS OPERATOR'S NUMBERS — these DIDs belong to the company this
# codebase came from and are registered to THEIR 10DLC campaign / provider
# account. Insurance Alliance Group must replace this fleet with its own
# provisioned numbers before enabling outbound SMS (SMS_LIVE_SEND_ENABLED).
# Sending from these would put IAG traffic on another company's account.
STATE_SENDER_NUMBERS: dict[str, List[str]] = {
    # Florida — 39 active (removed +14073093955 and +19413846705: deleted from Sinch inventory)
    "FL": [
        "+17723150754", "+19413893715", "+15612007947", "+17276093796",
        "+17723150751", "+14073093966", "+17276093797", "+12394017410",
        "+17722369488", "+17723150750", "+13523014093", "+17543358154",
        "+13862566357", "+15612007948", "+14073093902", "+17722369490",
        "+19542786224", "+15612007949", "+17276093879", "+12394017387",
        "+17276094271", "+13862566327", "+17543358152", "+19413846702",
        "+14073093912", "+19413846701", "+19413846704", "+17276094269",
        "+18633347808", "+13862566354", "+19413846703", "+19413893712",
        "+13862566356", "+16562450239", "+18633347809", "+18633347806",
        "+17723150753", "+17723150752", "+19183622794",
    ],
    # Texas — 8 active
    "TX": [
        "+12142970834", "+12142970845", "+12142970831", "+12142970841",
        "+12025899170", "+12142970843", "+12142970854", "+12142970829",
    ],
}


def numbers_for_state(state: str | None) -> List[str]:
    """Active sender numbers for a lead's state ([] if none / unmapped)."""
    if not state:
        return []
    return STATE_SENDER_NUMBERS.get(state.strip().upper(), [])


def all_sender_numbers() -> List[str]:
    """Every active dedicated sender number across ALL states, deduped, in a stable
    order.

    The fallback pool for a lead whose state has NO dedicated numbers (e.g. a Georgia
    lead): instead of collapsing onto the single global ENGAGECLOUD_FROM_NUMBERS, the
    sender pool round-robins across the FULL fleet — FL + TX + every other mapped
    state's numbers — so unmatched-state leads spread over all our DIDs and no single
    number is burdened. States that ARE mapped (FL/TX) still send from their own
    numbers; this only feeds the no-match case.
    """
    seen: set[str] = set()
    out: List[str] = []
    for nums in STATE_SENDER_NUMBERS.values():
        for n in nums:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out
