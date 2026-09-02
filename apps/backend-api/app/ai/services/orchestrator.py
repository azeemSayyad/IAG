"""
AI Orchestrator

Ties together all AI services:
- Prompt generation
- LLM interaction
- Message humanization
- Rate limiting
- SMS sending
- Queue management
- Event handling
"""

import asyncio
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.agent import Agent
from app.models.appointment import Appointment
from app.ai.services.prompts import (
    get_outreach_message,
    get_objection_response,
    get_followup_message,
    get_booking_message,
    get_confirmation_message,
    build_llm_prompt,
)
from app.ai.services.humanizer import humanize_message, adjust_tone_for_context
from app.ai.services.rate_limiter import check_rate_limit, record_sms_sent
from app.ai.services.communication_provider import communication_service, send_sms_to_lead
from app.ai.services.queue import enqueue_outbound_sms, enqueue_followup
from app.booking.services.reminders import schedule_reminders
from app.core.audit import log_ai_action
from app.realtime.websocket import emit_to_tenant


AFFIRMATIVE_BOOKING_WORDS = {
    "absolutely",
    "yes",
    "y",
    "ya",
    "yea",
    "yeah",
    "yep",
    "yup",
    "sure",
    "ok",
    "okay",
    "k",
    "cool",
    "great",
    "interested",
    "book",
    "booking",
    "schedule",
    "scheduled",
    "appointment",
    "appt",
    "call",
    "available",
    "availability",
    "slot",
    "slots",
    "time",
    "times",
}

AFFIRMATIVE_BOOKING_PHRASES = (
    "let's go",
    "lets go",
    "let's do it",
    "lets do it",
    "sounds good",
    "sounds great",
    "that works",
    "works for me",
    "i am in",
    "i'm in",
    "im in",
    "go ahead",
    "send times",
    "send slots",
    "send availability",
    "show times",
    "show slots",
    "what times",
    "what slots",
    "book me",
    "set it up",
    "sign me up",
    "i want to talk",
    "i want a call",
)

SKEPTICAL_BOOKING_PHRASES = (
    "maybe",
    "not sure",
    "tell me more",
    "what do you have",
    "what are the times",
    "when are you available",
    "when can you call",
    "send me the times",
    "send the times",
    "what is this",
    "who is this",
    "how much",
    "is this real",
    "skeptical",
)

STOP_WORDS = {"stop", "unsubscribe", "cancel", "remove", "quit", "end"}
NEGATIVE_WORDS = {"no", "not interested", "don't text", "do not text", "leave me alone"}


def _normalize_reply(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _infer_intent(message_text: str, current_intent: Optional[str], conversation: Conversation) -> str:
    if current_intent:
        return current_intent

    text = _normalize_reply(message_text)
    if not text:
        return "UNKNOWN"
    if text in STOP_WORDS or any(text.startswith(f"{word} ") for word in STOP_WORDS):
        return "STOP"
    # A concrete slot / "Call now" pick during booking wins over a loose negative
    # substring match (e.g. "call now" contains "no") — checked before NEGATIVE.
    if _match_slot_selection(text, conversation) is not None and conversation.status == "booking":
        return "SLOT_SELECTED"
    if any(phrase in text for phrase in NEGATIVE_WORDS):
        return "NEGATIVE"
    if any(phrase in text for phrase in SKEPTICAL_BOOKING_PHRASES):
        return "SKEPTICAL_BOOKING"
    words = set(re.findall(r"[a-z0-9']+", text))
    if words & AFFIRMATIVE_BOOKING_WORDS or any(phrase in text for phrase in AFFIRMATIVE_BOOKING_PHRASES):
        return "BOOK_NOW"
    return "UNKNOWN"


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _parse_slot_selection(message_text: str) -> Optional[int]:
    text = _normalize_reply(message_text)
    match = re.search(r"\b(?:option|slot|choice|number|#)\s*([1-9])(?:\b|[.)])", text)
    if not match:
        word_match = re.search(
            r"\b(?:option|slot|choice|number)\s+(one|two|three|four|five|six|seven|eight|nine)\b",
            text,
        )
        if word_match:
            return NUMBER_WORDS[word_match.group(1)]
    if not match:
        match = re.match(r"^\s*([1-9])(?:\b|[.)])", text)
    if not match:
        return None
    return int(match.group(1))


def _slot_local_start(slot: Dict[str, Any]) -> Optional[datetime]:
    try:
        start = datetime.fromisoformat(slot["start_time"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return start.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None


def _slot_hour_candidates(slot: Dict[str, Any]) -> set[str]:
    label = _normalize_reply(slot.get("label", ""))
    candidates = set()
    for match in re.finditer(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s*m\.?\b", label):
        hour = int(match.group(1))
        minute = match.group(2) or "00"
        meridiem = "am" if match.group(3) == "a" else "pm"
        candidates.add(f"{hour}:{minute} {meridiem}")
        candidates.add(f"{hour} {meridiem}")
        if minute == "00":
            candidates.add(str(hour))

    start = _slot_local_start(slot)
    if start:
        hour_12 = start.hour % 12 or 12
        minute = f"{start.minute:02d}"
        meridiem = "am" if start.hour < 12 else "pm"
        candidates.add(f"{hour_12}:{minute} {meridiem}")
        candidates.add(f"{hour_12} {meridiem}")
        if minute == "00":
            candidates.add(str(hour_12))
    return candidates


MONTH_ALIASES = {
    1: ("jan", "january"),
    2: ("feb", "february"),
    3: ("mar", "march"),
    4: ("apr", "april"),
    5: ("may",),
    6: ("jun", "june"),
    7: ("jul", "july"),
    8: ("aug", "august"),
    9: ("sep", "sept", "september"),
    10: ("oct", "october"),
    11: ("nov", "november"),
    12: ("dec", "december"),
}


def _slot_date_candidates(slot: Dict[str, Any]) -> set[str]:
    label = _normalize_reply(slot.get("label", ""))
    candidates = set()
    start = _slot_local_start(slot)
    if start:
        month_aliases = MONTH_ALIASES.get(start.month, ())
        day = str(start.day)
        for month in month_aliases:
            candidates.add(f"{month} {day}")
            candidates.add(f"{month} {day.zfill(2)}")
        candidates.add(f"{start.month}/{start.day}")
        candidates.add(f"{str(start.month).zfill(2)}/{str(start.day).zfill(2)}")

    for month_aliases in MONTH_ALIASES.values():
        for month in month_aliases:
            for match in re.finditer(rf"\b{month}\s+([0-3]?\d)\b", label):
                candidates.add(f"{month} {int(match.group(1))}")
                candidates.add(f"{month} {str(int(match.group(1))).zfill(2)}")
    return candidates


def _message_date_candidates(text: str) -> set[str]:
    candidates = set()
    for month_number, month_aliases in MONTH_ALIASES.items():
        for month in month_aliases:
            for match in re.finditer(rf"\b{month}\s+([0-3]?\d)(?:st|nd|rd|th)?\b", text):
                day = int(match.group(1))
                candidates.add(f"{month} {day}")
                candidates.add(f"{month} {str(day).zfill(2)}")
                candidates.add(f"{month_number}/{day}")
                candidates.add(f"{str(month_number).zfill(2)}/{str(day).zfill(2)}")

    for match in re.finditer(r"\b(0?[1-9]|1[0-2])\s*/\s*([0-3]?\d)\b", text):
        month = int(match.group(1))
        day = int(match.group(2))
        candidates.add(f"{month}/{day}")
        candidates.add(f"{str(month).zfill(2)}/{str(day).zfill(2)}")
    return candidates


def _message_time_candidates(text: str) -> set[str]:
    candidates = set()
    for match in re.finditer(
        r"\b(1[0-2]|0?[1-9]|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?::([0-5]\d))?\s*(?:o'?clock\s*)?([ap])?\.?\s*m?\.?\b",
        text,
    ):
        hour_token = match.group(1)
        hour = NUMBER_WORDS.get(hour_token, int(hour_token) if hour_token.isdigit() else None)
        if hour is None:
            continue
        minute = match.group(2) or "00"
        meridiem_token = match.group(3)
        if meridiem_token:
            meridiem = "am" if meridiem_token == "a" else "pm"
            candidates.add(f"{hour}:{minute} {meridiem}")
            candidates.add(f"{hour} {meridiem}")
        elif re.search(r"\b(?:at|around|about|by|for|with|take|want|choose|pick|go with)\b", text):
            candidates.add(str(hour))
    return candidates


def _matching_slot_indexes(slots: List[Dict[str, Any]], predicate) -> List[int]:
    indexes = []
    for slot in slots:
        if predicate(slot):
            indexes.append(int(slot["index"]))
    return indexes


def _match_slot_selection(message_text: str, conversation: Conversation) -> Optional[int]:
    text = _normalize_reply(message_text)
    slots = (conversation.ai_context or {}).get("booking_slots") or []
    for slot in slots:
        label = _normalize_reply(slot.get("label", ""))
        if label and (text == label or label in text):
            return int(slot["index"])

    if re.search(r"\b(?:earliest|soonest|first available|next available)\b", text):
        return int(slots[0]["index"]) if slots else None
    if re.search(r"\b(?:latest|last available|last one|final one)\b", text):
        return int(slots[-1]["index"]) if slots else None
    if re.search(r"\b(?:middle one|middle slot)\b", text) and len(slots) >= 2:
        return int(slots[1]["index"])

    time_candidates = _message_time_candidates(text)
    for candidate in time_candidates:
        matches = _matching_slot_indexes(slots, lambda slot: candidate in _slot_hour_candidates(slot))
        if len(matches) == 1:
            return matches[0]

    date_candidates = _message_date_candidates(text)
    for candidate in date_candidates:
        matches = _matching_slot_indexes(slots, lambda slot: candidate in _slot_date_candidates(slot))
        if len(matches) == 1:
            return matches[0]

    day_tokens = {
        "mon": ("mon", "monday"),
        "tue": ("tue", "tues", "tuesday"),
        "wed": ("wed", "wednesday"),
        "thu": ("thu", "thur", "thurs", "thursday"),
        "fri": ("fri", "friday"),
        "sat": ("sat", "saturday"),
        "sun": ("sun", "sunday"),
    }
    for day_key, aliases in day_tokens.items():
        if any(re.search(rf"\b{alias}\b", text) for alias in aliases):
            matches = _matching_slot_indexes(
                slots,
                lambda slot, aliases=aliases: any(
                    re.search(rf"\b{alias}\b", _normalize_reply(slot.get("label", ""))) for alias in aliases
                ),
            )
            if len(matches) == 1:
                return matches[0]

    time_of_day_ranges = {
        "morning": (5, 12),
        "afternoon": (12, 17),
        "evening": (17, 22),
    }
    for token, (start_hour, end_hour) in time_of_day_ranges.items():
        if re.search(rf"\b{token}\b", text):
            matches = _matching_slot_indexes(
                slots,
                lambda slot, start_hour=start_hour, end_hour=end_hour: (
                    (local_start := _slot_local_start(slot)) is not None
                    and start_hour <= local_start.hour < end_hour
                ),
            )
            if len(matches) == 1:
                return matches[0]

    exact_ordinal_phrases = {
        1: (r"\bfirst one\b", r"\bfirst slot\b", r"\bfirst time\b"),
        2: (r"\bsecond one\b", r"\bsecond slot\b", r"\bsecond time\b"),
        3: (r"\bthird one\b", r"\bthird slot\b", r"\bthird time\b"),
    }
    for index, patterns in exact_ordinal_phrases.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return index

    ordinal_patterns = {
        1: (r"\bfirst\b", r"\b1st\b", r"\bone\b"),
        2: (r"\bsecond\b", r"\b2nd\b", r"\btwo\b"),
        3: (r"\bthird\b", r"\b3rd\b", r"\bthree\b"),
    }
    for index, patterns in ordinal_patterns.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return index

    selected_index = _parse_slot_selection(message_text)
    if selected_index is not None:
        return selected_index
    return None


def _lead_timezone(lead: Lead) -> ZoneInfo:
    try:
        return ZoneInfo(lead.timezone or "America/New_York")
    except Exception:
        return ZoneInfo("America/New_York")


def _next_business_slot_start(now_local: datetime) -> datetime:
    candidate = now_local.replace(minute=0, second=0, microsecond=0)
    if now_local.minute or now_local.second or now_local.microsecond:
        candidate += timedelta(hours=1)

    if candidate.hour < 9:
        candidate = candidate.replace(hour=9)
    elif candidate.hour >= 17:
        candidate = (candidate + timedelta(days=1)).replace(hour=9)

    while candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(hour=9)

    return candidate


def _slot_overlaps(db: Session, tenant_id: str, agent_id: str, start_time: datetime, end_time: datetime) -> bool:
    return (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.agent_id == agent_id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time < end_time,
            Appointment.end_time > start_time,
        )
        .first()
        is not None
    )


def _select_booking_agent(db: Session, lead: Lead, conversation: Conversation) -> Optional[Agent]:
    # Compliance guard: an agent may only take this lead if licensed for its state.
    try:
        from app.leads.services.distribution import agent_licensed_for_state
        def _ok(a):
            return a is not None and agent_licensed_for_state(db, a.id, lead.tenant_id, getattr(lead, "state", None))
    except Exception:
        def _ok(a):
            return a is not None

    # 1) The agent the lead is already assigned to (AI auto-distribution result).
    if getattr(lead, "assigned_agent_id", None):
        agent = (
            db.query(Agent)
            .filter(
                Agent.id == lead.assigned_agent_id,
                Agent.tenant_id == lead.tenant_id,
                Agent.status == "active",
            )
            .first()
        )
        if _ok(agent):
            return agent

    context = dict(conversation.ai_context or {})
    slots = context.get("booking_slots") or []
    if slots and slots[0].get("agent_id"):
        agent = (
            db.query(Agent)
            .filter(
                Agent.id == slots[0]["agent_id"],
                Agent.tenant_id == lead.tenant_id,
                Agent.status == "active",
            )
            .first()
        )
        if _ok(agent):
            return agent

    if lead.created_by:
        agent = (
            db.query(Agent)
            .filter(
                Agent.user_id == lead.created_by,
                Agent.tenant_id == lead.tenant_id,
                Agent.status == "active",
            )
            .first()
        )
        if _ok(agent):
            return agent

    # Prefer the first active agent LICENSED for the lead's state.
    active_agents = (
        db.query(Agent)
        .filter(Agent.tenant_id == lead.tenant_id, Agent.status == "active")
        .order_by(Agent.created_at.asc())
        .all()
    )
    for agent in active_agents:
        if _ok(agent):
            return agent
    # No licensed agent found. When state-license enforcement is ON and the lead
    # HAS a state, NEVER fall back to an unlicensed agent — booking is held
    # upstream and an admin is notified. Otherwise (flag off, or the lead has no
    # state) keep the live booking pipeline intact with any active agent.
    from app.core.config import settings
    if settings.STATE_LICENSE_BOOKING_ENFORCED and getattr(lead, "state", None):
        return None
    return active_agents[0] if active_agents else None


def _format_slot(start_time: datetime, tz: ZoneInfo) -> str:
    local = start_time.astimezone(tz)
    return local.strftime("%a, %b %-d at %-I:%M %p %Z")


# Sentinel reason returned when STATE_LICENSE_BOOKING_ENFORCED is on and NO agent
# is licensed for the lead's state — the caller holds the lead + notifies an admin.
NO_LICENSED_AGENT = "__no_licensed_agent__"


def _pacing_enabled() -> bool:
    try:
        from app.core import engine_flags
        return engine_flags.same_day_pacing_enabled()
    except Exception:
        return False


def _autopilot_paused(tenant_id) -> bool:
    """True when Queue-Only Mode is active for this tenant (AI booking reply +
    follow-ups suppressed). Fails to False so the AI keeps working if Redis is
    unreachable — matches the kill-switch's fail-open-for-AI behaviour."""
    try:
        from app.core.sending import is_autopilot_paused
        return is_autopilot_paused(tenant_id)
    except Exception:
        return False


def _pacing_waitlist(db: Session, lead: Lead) -> None:
    """Capacity engine: park an interested lead with no open slot. No-op when off.

    Only paced leads (those that came through a >500 bulk import and carry a
    pacing_status) are waitlisted. Small-upload / single leads are NOT paced —
    they follow the normal multi-day booking path, so we never park them here.
    """
    if not _pacing_enabled() or not getattr(lead, "pacing_status", None):
        return
    try:
        from app.pacing.waitlist import add_to_waitlist
        add_to_waitlist(db, lead)
    except Exception:
        pass


def _pacing_mark_booked(db: Session, lead: Lead) -> None:
    """Capacity engine: flag a lead booked so it leaves the held/waitlist pools.

    Guarded to paced leads only (pacing_status set) so a normal blast lead is
    never tagged with engine state.
    """
    if not _pacing_enabled() or not getattr(lead, "pacing_status", None):
        return
    try:
        from app.pacing.waitlist import mark_booked
        mark_booked(db, lead)
    except Exception:
        pass


def _pick_agent_for_slot(db: Session, tenant_id: str, free_agents):
    """Choose which agent a slot is offered with.

    Capacity engine ON -> least-utilized free agent (even distribution, random
    tiebreak). OFF -> random (today's behavior, unchanged).
    """
    if not free_agents:
        return None
    import random as _random
    pool = list(free_agents)
    _random.shuffle(pool)  # fair tiebreak among equally-loaded agents
    if _pacing_enabled():
        try:
            from app.pacing.capacity import agent_booked_today
            pool.sort(key=lambda a: agent_booked_today(db, tenant_id, a.id))
        except Exception:
            pass
    return pool[0]


def _union_slots_for_agents(
    db: Session,
    lead: Lead,
    agents: List[Agent],
    count: int = 3,
    pool: int = 12,
) -> List[Dict[str, Any]]:
    """Union of available 15-min ET slots across the given (state-licensed) agents.

    A time is available if ANY of the agents is free then; the slot is tagged to
    one free agent. We gather up to `pool` of the earliest available union-times,
    then — if there are more than `count` — RANDOMLY sample `count` of them for
    the SMS (the lead only ever sees 3), and present those chronologically.
    """
    import random
    from app.core.timezones import generate_ny_anchored_slots

    agent_ids = [str(a.id) for a in agents]
    tid = str(lead.tenant_id)

    def _all_busy(start_utc, end_utc):
        # A union slot is "taken" only when EVERY licensed agent is busy then.
        return all(_slot_overlaps(db, tid, aid, start_utc, end_utc) for aid in agent_ids)

    from app.pacing.booking import booking_horizon_days
    raw = generate_ny_anchored_slots(
        now_utc=datetime.now(timezone.utc),
        lead_tz_name=(lead.timezone or None),
        is_taken=_all_busy,
        count=max(pool, count),
        max_days=booking_horizon_days(db, lead),
    )
    if not raw:
        return []

    # Always offer the SOONEST available time (e.g. today's remaining slots) so we
    # never hide an earlier day; randomise the rest so bookings still spread out
    # instead of every lead landing on the same first slot.
    if len(raw) <= count:
        chosen = raw
    else:
        chosen = [raw[0]] + random.sample(raw[1:], count - 1)
    chosen.sort(key=lambda s: s["start_time"])

    # Tag each chosen slot to a licensed agent who is actually free at that time
    # (random among the free ones for fair distribution) and re-index 1..N so the
    # lead's numeric reply maps correctly in _book_selected_slot.
    for i, s in enumerate(chosen, start=1):
        start = datetime.fromisoformat(s["start_time"])
        end = datetime.fromisoformat(s["end_time"])
        free = [a for a in agents if not _slot_overlaps(db, tid, str(a.id), start, end)]
        if not free:
            free = list(agents)
        s["agent_id"] = str(_pick_agent_for_slot(db, tid, free).id)
        s["index"] = i
    return chosen


def _generate_booking_slots(
    db: Session,
    lead: Lead,
    conversation: Conversation,
    count: int = 3,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    from app.core.config import settings
    from app.core.timezones import generate_ny_anchored_slots

    state = getattr(lead, "state", None)

    # State-license-aware booking (flag-gated). Only when ENFORCED *and* the lead
    # has a state: offer the union of availability across agents licensed for that
    # state. No licensed agent → hold (NO_LICENSED_AGENT). Leads with no state, or
    # the flag off, keep today's single-agent behavior so the live pipeline is
    # untouched.
    if settings.STATE_LICENSE_BOOKING_ENFORCED and state:
        from app.leads.services.distribution import booking_agents_for_state
        agents = booking_agents_for_state(db, str(lead.tenant_id), state)
        if not agents:
            return [], NO_LICENSED_AGENT
        slots = _union_slots_for_agents(db, lead, agents, count=count)
        if not slots:
            return [], "No open appointment slots were found for the next several business days."
        return slots, None

    # Default path (licensing NOT enforced): offer the UNION of availability across
    # ALL active agents, so the customer always sees the SOONEST open slot from
    # anyone — consistent with the merged agent calendar. (Previously this booked
    # against a single agent, so a lead could be pushed to a later day when that one
    # agent happened to be full even though another agent was free at an earlier
    # time.) The lead's assigned agent is preferred for ties (kept first in the list).
    agents = (
        db.query(Agent)
        .filter(Agent.tenant_id == lead.tenant_id, Agent.status == "active")
        .order_by(Agent.created_at.asc())
        .all()
    )
    if getattr(lead, "assigned_agent_id", None):
        agents.sort(key=lambda a: a.id != lead.assigned_agent_id)
    if not agents:
        return [], "No active agent is available for booking right now."
    slots = _union_slots_for_agents(db, lead, agents, count=count)
    if not slots:
        return [], "No open appointment slots were found for the next several business days."
    return slots, None


# "Call now" — the always-offered final option. Picking it routes the lead to an
# immediate agent popup + same-instant appointment instead of a scheduled slot.
CALL_NOW_LABEL = "Call now"


def _call_now_index(real_slots: List[Dict[str, Any]]) -> int:
    """The number the Call-now option is offered as: one past the real slots.

    Computed the same way in both the stored slot list and the SMS text so the
    digit the lead replies with always maps back to the Call-now entry.
    """
    nums = [int(s["index"]) for s in (real_slots or []) if s.get("index") is not None]
    return (max(nums) + 1) if nums else 1


def _call_now_slot(real_slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"index": _call_now_index(real_slots), "label": CALL_NOW_LABEL, "call_now": True}


def _build_slots_message(lead: Lead, slots: List[Dict[str, Any]], skeptical: bool = False) -> str:
    # Numbered options only — each `label` is already in the LEAD's local timezone
    # (resolved from their CSV state/ZIP). The customer replies with the number.
    # A final "Call now" option is ALWAYS appended so a hot lead can ask for an
    # immediate call (even when no same-day slots exist).
    real = [s for s in (slots or []) if not s.get("call_now")]
    lines = []
    for i, slot in enumerate(real, start=1):
        n = slot.get("index", i)
        lines.append(f"{n}. {slot['label']}")
    lines.append(f"{_call_now_index(real)}. Call now — talk to an agent right away")
    body = "\n".join(lines)
    if real:
        return f"Great i am available at\n{body}"
    # No same-day slots: offer the immediate call as the way forward.
    return f"I can have an agent call you right now. Reply:\n{body}"


def _store_booking_slots(conversation: Conversation, slots: List[Dict[str, Any]]) -> None:
    # Always store the Call-now option alongside the real slots so the lead's
    # numeric reply (or "call now") resolves to it. `slots` is the real list.
    real = [s for s in (slots or []) if not s.get("call_now")]
    stored = list(real) + [_call_now_slot(real)]
    context = dict(conversation.ai_context or {})
    context["booking_slots"] = stored
    context["booking_requested_at"] = datetime.now(timezone.utc).isoformat()
    context["booking_state"] = "slots_offered"
    conversation.ai_context = context
    conversation.status = "booking"


def _handle_no_licensed_agent(db: Session, lead: Lead, conversation: Conversation) -> str:
    """No agent is licensed for the lead's state (enforcement on): HOLD the lead —
    offer no slots, flag it for an admin (realtime + audit), and reply with a
    non-booking message so the AI never books an unlicensed agent.
    """
    try:
        lead.ai_status = "awaiting_license"
    except Exception:
        pass
    context = dict(conversation.ai_context or {})
    context["booking_state"] = "held_no_licensed_agent"
    conversation.ai_context = context
    _emit_to_tenant_safe(str(lead.tenant_id), "lead_awaiting_license", {
        "lead_id": str(lead.id),
        "lead_name": f"{lead.first_name} {lead.last_name}".strip(),
        "state": getattr(lead, "state", None),
    })
    try:
        log_ai_action(
            tenant_id=str(lead.tenant_id),
            action="lead_awaiting_license",
            resource_type="lead",
            resource_id=str(lead.id),
            details={"state": getattr(lead, "state", None)},
        )
    except Exception:
        pass
    return (
        f"Thanks {lead.first_name}! A licensed agent for your area will reach out "
        "shortly to get your appointment scheduled."
    )


def _emit_to_tenant_safe(tenant_id: str, event: str, payload: Dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emit_to_tenant(tenant_id, event, payload))
    except RuntimeError:
        try:
            asyncio.run(emit_to_tenant(tenant_id, event, payload))
        except Exception:
            pass
    except Exception:
        pass


def _emit_to_agent_safe(user_id: str, event: str, payload: Dict[str, Any]) -> None:
    """Push a realtime event to ONE agent's socket room (`agent:{user_id}`).

    NB: agent rooms are keyed by the agent's USER id (see realtime/websocket.py),
    not the agents.id — callers must pass agent.user_id. Mirrors the sync->async
    bridge used for tenant emits so it works from the (sync) webhook handler.
    """
    from app.realtime.websocket import emit_to_agent
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emit_to_agent(user_id, event, payload))
    except RuntimeError:
        try:
            asyncio.run(emit_to_agent(user_id, event, payload))
        except Exception:
            pass
    except Exception:
        pass


def _call_now_candidates(db: Session, lead: Lead) -> List[Agent]:
    """Agents who may take an immediate call for this lead.

    Mirrors the booking-agent rules so Call-now never routes to anyone the normal
    booking path wouldn't use: when state-license enforcement is ON and the lead
    has a state, only agents licensed for that state are eligible (empty => the
    caller holds the lead). Otherwise (flag off, or no state) any active agent.
    """
    from app.core.config import settings
    if settings.STATE_LICENSE_BOOKING_ENFORCED and getattr(lead, "state", None):
        from app.leads.services.distribution import booking_agents_for_state
        return booking_agents_for_state(db, str(lead.tenant_id), lead.state)
    return (
        db.query(Agent)
        .filter(Agent.tenant_id == lead.tenant_id, Agent.status == "active")
        .all()
    )


def _has_online_call_now_agent(db: Session, lead: Lead) -> bool:
    """True if at least one eligible agent is logged in right now.

    Used to gate the *no-slots* Call-now offer: when today is full AND nobody is
    online there's no one to take a live call, so we don't make the promise.
    """
    candidates = _call_now_candidates(db, lead)
    if not candidates:
        return False
    try:
        from app.realtime.websocket import manager
        return any(manager.is_user_online(str(a.user_id)) for a in candidates)
    except Exception:
        return False


def _pick_call_now_agent(db: Session, lead: Lead) -> Optional[Agent]:
    """Choose the agent the Call-now popup fires at.

    Logged-in-first (per product spec):
      * >=1 candidate currently logged in  -> least-busy-today of them (random
        tiebreak); covers "one available -> that one" and "many -> one of them".
      * none logged in ("all busy")        -> a random candidate, so it still
        lands on someone and shows the moment they open the dashboard.
      * no candidate at all                -> None (caller falls back).
    """
    import random as _random
    candidates = _call_now_candidates(db, lead)
    if not candidates:
        return None
    from app.realtime.websocket import manager
    online = [a for a in candidates if manager.is_user_online(str(a.user_id))]
    if online:
        _random.shuffle(online)  # fair tiebreak among equally-busy agents
        try:
            from app.pacing.capacity import agent_booked_today
            online.sort(key=lambda a: agent_booked_today(db, str(lead.tenant_id), a.id))
        except Exception:
            pass
        return online[0]
    return _random.choice(candidates)


def _book_selected_slot(
    db: Session,
    lead: Lead,
    conversation: Conversation,
    selected_index: int,
) -> Tuple[Optional[Appointment], str]:
    context = dict(conversation.ai_context or {})
    slots = context.get("booking_slots") or []
    selected = next((slot for slot in slots if slot.get("index") == selected_index), None)
    if not selected:
        return None, "That slot is no longer available. Reply YES and I'll send fresh appointment times."

    start_time = datetime.fromisoformat(selected["start_time"])
    end_time = datetime.fromisoformat(selected["end_time"])

    # Defensive re-checks before committing: the slot must still be free AND
    # (under enforcement) the tagged agent must STILL be licensed for the lead's
    # state — a license could have expired/been revoked between offer and pick.
    needs_regen = _slot_overlaps(db, str(lead.tenant_id), selected["agent_id"], start_time, end_time)
    if not needs_regen:
        from app.core.config import settings as _settings
        if _settings.STATE_LICENSE_BOOKING_ENFORCED and getattr(lead, "state", None):
            from app.leads.services.distribution import agent_licensed_for_state
            if not agent_licensed_for_state(db, selected["agent_id"], lead.tenant_id, lead.state):
                needs_regen = True
    if needs_regen:
        slots, error = _generate_booking_slots(db, lead, conversation)
        if slots:
            _store_booking_slots(conversation, slots)
            return None, _build_slots_message(lead, slots)
        if error == NO_LICENSED_AGENT:
            return None, _handle_no_licensed_agent(db, lead, conversation)
        return None, error or "That appointment time was just booked. An agent will follow up with new times."

    appointment = Appointment(
        tenant_id=lead.tenant_id,
        lead_id=lead.id,
        agent_id=UUID(selected["agent_id"]),
        conversation_id=conversation.id,
        start_time=start_time,
        end_time=end_time,
        status="confirmed",
        booking_source="ai_sms",
        ai_confidence=0.95,
    )
    db.add(appointment)
    db.flush()
    schedule_reminders(db, appointment)

    lead.status = "booked"
    lead.lifecycle_stage = "booked"
    # The lead's owner follows the booking — whichever licensed agent took the
    # appointment becomes the assigned agent (keeps ownership and the booked
    # agent consistent).
    lead.assigned_agent_id = appointment.agent_id
    conversation.status = "booked"
    context["booking_state"] = "booked"
    context["booked_slot"] = selected
    conversation.ai_context = context

    _emit_to_tenant_safe(
        str(lead.tenant_id),
        "appointment_created",
        {
            "appointment_id": str(appointment.id),
            "lead_id": str(lead.id),
            "agent_id": selected["agent_id"],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status": appointment.status,
            "source": "ai_sms",
        },
    )
    _emit_to_tenant_safe(
        str(lead.tenant_id),
        "lead_updated",
        {"lead_id": str(lead.id), "status": lead.status, "lifecycle_stage": lead.lifecycle_stage},
    )

    return appointment, "Great i will reach out"


def _agent_display_name(agent: Agent) -> Optional[str]:
    """Best-effort human name for an agent (for admin-facing payloads)."""
    try:
        u = agent.user
        full = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
        return full or getattr(u, "email", None)
    except Exception:
        return None


def _handle_call_now(
    db: Session,
    lead: Lead,
    conversation: Conversation,
) -> Tuple[Optional[Appointment], str]:
    """Lead picked "Call now": pick an agent, create an immediate appointment, and
    fire a realtime popup so the agent calls the lead right now from their phone.

    Returns (appointment, reply_text). On no eligible agent, holds gracefully.
    """
    from app.core.config import settings as _settings

    agent = _pick_call_now_agent(db, lead)
    if agent is None:
        # No eligible agent. Under enforcement+state this is the unlicensed-hold
        # case; otherwise (no active agents at all) a soft follow-up.
        if _settings.STATE_LICENSE_BOOKING_ENFORCED and getattr(lead, "state", None):
            return None, _handle_no_licensed_agent(db, lead, conversation)
        return None, (
            f"Thanks {lead.first_name}! An agent will reach out to call you as soon as possible."
        )

    now = datetime.now(timezone.utc)
    end = now + timedelta(minutes=int(getattr(_settings, "SLOT_MINUTES", 15) or 15))

    # Immediate appointment — distinct booking_source so it reads as a live call,
    # not a scheduled slot. No reminders are scheduled (the call is happening now).
    appointment = Appointment(
        tenant_id=lead.tenant_id,
        lead_id=lead.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        start_time=now,
        end_time=end,
        status="confirmed",
        booking_source="ai_sms_call_now",
        ai_confidence=0.95,
    )
    db.add(appointment)
    db.flush()

    lead.status = "booked"
    lead.lifecycle_stage = "booked"
    lead.assigned_agent_id = agent.id
    conversation.status = "booked"
    context = dict(conversation.ai_context or {})
    context["booking_state"] = "call_now_requested"
    conversation.ai_context = context

    lead_name = f"{lead.first_name} {lead.last_name}".strip()
    agent_name = _agent_display_name(agent)
    payload = {
        "appointment_id": str(appointment.id),
        "lead_id": str(lead.id),
        "lead_name": lead_name,
        "phone": lead.phone,
        "state": getattr(lead, "state", None),
        "city": getattr(lead, "city", None),
        "email": getattr(lead, "email", None),
        "score": getattr(lead, "lead_score", None),
        "agent_id": str(agent.id),
        "agent_user_id": str(agent.user_id),
        "agent_name": agent_name,
        "requested_at": now.isoformat(),
    }

    # Pop up on the chosen agent (their user-id room) AND the tenant (admins see
    # the live call request too).
    _emit_to_agent_safe(str(agent.user_id), "call_now_request", payload)
    _emit_to_tenant_safe(str(lead.tenant_id), "call_now_request", payload)
    # Reuse the existing appointment refresh so it shows in the appointments list.
    _emit_to_tenant_safe(
        str(lead.tenant_id),
        "appointment_created",
        {
            "appointment_id": str(appointment.id),
            "lead_id": str(lead.id),
            "agent_id": str(agent.id),
            "start_time": now.isoformat(),
            "end_time": end.isoformat(),
            "status": appointment.status,
            "source": "ai_sms_call_now",
        },
    )
    _emit_to_tenant_safe(
        str(lead.tenant_id),
        "lead_updated",
        {"lead_id": str(lead.id), "status": lead.status, "lifecycle_stage": lead.lifecycle_stage},
    )

    try:
        log_ai_action(
            tenant_id=str(lead.tenant_id),
            action="call_now_requested",
            resource_type="lead",
            resource_id=str(lead.id),
            details={"agent_id": str(agent.id), "appointment_id": str(appointment.id)},
        )
    except Exception:
        pass

    return appointment, "Perfect — an agent is calling you right now. Please keep your phone handy."


def process_outreach(
    db: Session,
    lead: Lead,
    campaign_settings: Dict = None,
) -> Dict[str, Any]:
    """
    Process outreach to a new lead.

    1. Check rate limits
    2. Generate message
    3. Humanize
    4. Send SMS
    5. Log conversation
    """
    tenant_id = str(lead.tenant_id)
    lead_id = str(lead.id)
    tone = (campaign_settings or {}).get("tone", "friendly")

    # Guard: don't re-send cold outreach to a lead already past the outreach
    # stage (prevents clobbering a booked lead / duplicate conversations).
    if (lead.status or "").lower() in ("booked", "stopped", "unqualified"):
        return {"success": False, "skipped": True, "reason": f"lead already {lead.status}"}

    # Check rate limit
    rate_check = check_rate_limit(tenant_id, lead_id, lead.phone)
    if not rate_check.allowed:
        return {"success": False, "error": rate_check.reason}

    # Generate message
    message = get_outreach_message(
        first_name=lead.first_name,
        tone=tone,
        source=lead.source,
        tenant_id=lead.tenant_id,
    )

    # Outreach copy is an approved template and must be sent exactly as rendered.

    # Send SMS
    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    if result["success"]:
        # Record rate limit
        record_sms_sent(tenant_id, lead_id)

        # Update lead status
        lead.status = "contacted"
        lead.last_contacted_at = datetime.now(timezone.utc)

        # Create or update conversation
        conversation = (
            db.query(Conversation)
            .filter(Conversation.lead_id == lead.id, Conversation.status.in_(["initiated", "active"]))
            .first()
        )
        if not conversation:
            conversation = Conversation(
                tenant_id=lead.tenant_id,
                lead_id=lead.id,
                status="active",
            )
            db.add(conversation)
            db.flush()

        # Log message
        msg = Message(
            conversation_id=conversation.id,
            tenant_id=lead.tenant_id,
            sender="ai",
            content=message,
            message_type="sms",
            provider=result.get("provider") or communication_service.provider_name,
            provider_message_sid=result.get("message_sid"),
            delivery_status=result.get("status"),
            msg_metadata={
                (result.get("provider") or communication_service.provider_name): {
                    "message_sid": result.get("message_sid"),
                    "status": result.get("status"),
                }
            },
        )
        db.add(msg)
        conversation.message_count += 1
        conversation.last_message_at = datetime.now(timezone.utc)
        conversation.last_message_from = "ai"

        db.commit()

        # Audit
        log_ai_action(
            tenant_id=tenant_id,
            action="outreach_sent",
            resource_type="lead",
            resource_id=lead_id,
            details={"message": message[:100]},
        )

        # Realtime: outbound message must update the inbox/dashboard live.
        _emit_to_tenant_safe(tenant_id, "conversation_message_created", {
            "conversation_id": str(conversation.id),
            "lead_id": lead_id,
            "sender": "ai",
            "content": message,
            "message_type": "sms",
            "last_message_from": "ai",
        })
        _emit_to_tenant_safe(tenant_id, "lead_updated", {"lead_id": lead_id, "status": lead.status})

    return result


def process_incoming_message(
    db: Session,
    lead: Lead,
    conversation: Conversation,
    message_text: str,
    intent: str = None,
    inbound_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process an incoming message from a lead.

    1. Log the message
    2. Detect intent (if not provided)
    3. Generate response
    4. Send response
    """
    tenant_id = str(lead.tenant_id)
    lead_id = str(lead.id)
    detected_intent = _infer_intent(message_text, intent, conversation)

    # Log incoming message
    incoming_msg = Message(
        conversation_id=conversation.id,
        tenant_id=lead.tenant_id,
        sender="customer",
        content=message_text,
        message_type="sms",
        intent=detected_intent,
        provider=communication_service.provider_name if inbound_metadata else None,
        provider_message_sid=(inbound_metadata or {}).get("message_sid"),
        delivery_status="received" if inbound_metadata else None,
        msg_metadata={communication_service.provider_name: inbound_metadata} if inbound_metadata else {},
    )
    db.add(incoming_msg)
    conversation.message_count += 1
    conversation.last_message_at = datetime.now(timezone.utc)
    conversation.last_message_from = "customer"

    # Global kill-switch: when sending is paused, FULLY freeze. We still record the
    # customer's inbound message (so nothing is lost and the agent can see it), but
    # we generate NO reply and book NO appointment — the reply is handled when the
    # tenant resumes. Without this, replies from already-contacted leads would keep
    # booking appointments even though "Stop" was pressed.
    try:
        from app.core.sending import is_sending_paused
        if is_sending_paused(tenant_id):
            db.commit()
            return {"success": True, "paused": True, "response": None, "intent": detected_intent}
    except Exception:
        pass

    # Generate response based on intent. Booking is intentionally deterministic here:
    # once a customer is positive or cautiously interested, offer concrete slots.
    response_text = None
    if detected_intent == "STOP":
        # Silent stop: mark the lead so we never message it again, and send
        # NOTHING back (no "unsubscribed" confirmation). response_text stays None,
        # which the send guard below skips.
        conversation.status = "stopped"
        lead.status = "unqualified"
        response_text = None
    elif _autopilot_paused(tenant_id):
        # Queue-Only Mode: the customer's reply is recorded above, but the AI
        # sends NO booking reply and books NO appointment. Positive replies are
        # picked up by the SMS human queue (ingest_positive_leads). STOP was
        # already handled in the branch above, so opt-outs are still honored.
        # The appointment-booking pipeline is untouched and returns the moment
        # this flag is turned back off.
        db.commit()
        return {"success": True, "autopilot_paused": True, "response": None, "intent": detected_intent}
    elif detected_intent == "SLOT_SELECTED":
        selected_index = _match_slot_selection(message_text, conversation)
        _slots = (conversation.ai_context or {}).get("booking_slots") or []
        _selected = next((s for s in _slots if s.get("index") == selected_index), None)
        if _selected and _selected.get("call_now"):
            # Lead picked the "Call now" option -> immediate agent popup + appt.
            appointment, response_text = _handle_call_now(db, lead, conversation)
        else:
            appointment, response_text = _book_selected_slot(db, lead, conversation, selected_index)
        if appointment is not None:
            _pacing_mark_booked(db, lead)
    elif detected_intent in ("POSITIVE", "BOOK_NOW", "INTERESTED", "SKEPTICAL", "SKEPTICAL_BOOKING"):
        slots, error = _generate_booking_slots(db, lead, conversation)
        if slots:
            _store_booking_slots(conversation, slots)
            response_text = _build_slots_message(lead, slots, skeptical=detected_intent in ("SKEPTICAL", "SKEPTICAL_BOOKING"))
        elif error == NO_LICENSED_AGENT:
            response_text = _handle_no_licensed_agent(db, lead, conversation)
        else:
            # Interested lead, no same-day slot -> waitlist (never dropped). Offer
            # an immediate call ONLY if an agent is actually logged in to take it;
            # otherwise fall back to the follow-up message (no empty promise).
            _pacing_waitlist(db, lead)
            if _has_online_call_now_agent(db, lead):
                _store_booking_slots(conversation, [])
                response_text = _build_slots_message(lead, [])
            else:
                response_text = error or "I don't see an open appointment slot right now. An agent will follow up with you."
    elif detected_intent == "NEGATIVE":
        response_text = get_objection_response(lead.first_name, "not_interested")
    elif detected_intent == "QUESTION":
        slots, error = _generate_booking_slots(db, lead, conversation)
        if slots:
            _store_booking_slots(conversation, slots)
            response_text = _build_slots_message(lead, slots, skeptical=True)
        elif error == NO_LICENSED_AGENT:
            response_text = _handle_no_licensed_agent(db, lead, conversation)
        else:
            # No same-day slot for a question/interested lead -> offer the
            # immediate call only if an agent is online to take it.
            if _has_online_call_now_agent(db, lead):
                _store_booking_slots(conversation, [])
                response_text = _build_slots_message(lead, [])
            else:
                response_text = f"Great question, {lead.first_name}! An agent will follow up with details."
    else:
        if conversation.status == "booking" and (conversation.ai_context or {}).get("booking_slots"):
            response_text = "Please reply with one of the appointment times above."
        else:
            response_text = f"Thanks for your message, {lead.first_name}! Reply YES and I'll send available appointment times."

    # Humanize only open-ended marketing/support copy. Booking and compliance copy must stay exact.
    skip_humanize = detected_intent in (
        "STOP",
        "SLOT_SELECTED",
        "BOOK_NOW",
        "POSITIVE",
        "INTERESTED",
        "SKEPTICAL",
        "SKEPTICAL_BOOKING",
        "QUESTION",
    ) or (conversation.status == "booking" and response_text == "Please reply with one of the appointment times above.")
    if response_text and not skip_humanize:
        response_text = humanize_message(response_text)

    # Send response
    if response_text:
        transactional_intents = {
            "SLOT_SELECTED",
            "BOOK_NOW",
            "POSITIVE",
            "INTERESTED",
            "SKEPTICAL",
            "SKEPTICAL_BOOKING",
            "QUESTION",
        }
        result = send_sms_to_lead(
            phone=lead.phone,
            message=response_text,
            tenant_id=tenant_id,
            lead_id=lead_id,
            rate_limit_scope="transactional" if detected_intent in transactional_intents else "marketing",
        )

        if result["success"]:
            record_sms_sent(tenant_id, lead_id)

            # Log outgoing message
            outgoing_msg = Message(
                conversation_id=conversation.id,
                tenant_id=lead.tenant_id,
                sender="ai",
                content=response_text,
                message_type="sms",
                intent=detected_intent,
                provider=result.get("provider") or communication_service.provider_name,
                provider_message_sid=result.get("message_sid"),
                delivery_status=result.get("status"),
                msg_metadata={
                    (result.get("provider") or communication_service.provider_name): {
                        "message_sid": result.get("message_sid"),
                        "status": result.get("status"),
                    }
                },
            )
            db.add(outgoing_msg)
            conversation.message_count += 1
            conversation.last_message_at = datetime.now(timezone.utc)
            conversation.last_message_from = "ai"

            db.commit()

            return {"success": True, "response": response_text, "intent": detected_intent}

        # The send failed (e.g. provider not configured / rate-limited) but a
        # response WAS generated and the booking state is persisted — surface the
        # text so callers/UI see exactly what the AI said.
        db.commit()
        return {
            "success": False,
            "response": response_text,
            "error": (result or {}).get("error") or "send_failed",
            "intent": detected_intent,
        }

    db.commit()
    return {"success": False, "error": "No response generated"}
