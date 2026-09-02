"""
Conversation State Machine (Step 18.3)

Manages conversation lifecycle through defined states.

States:
- NEW_LEAD → OUTREACH → WAITING_REPLY → INTERESTED → BOOKING → BOOKED → FOLLOW_UP

Transitions are triggered by events:
- lead_created → NEW_LEAD
- outreach_sent → OUTREACH
- customer_replied → WAITING_REPLY
- intent_positive → INTERESTED
- booking_started → BOOKING
- appointment_booked → BOOKED
- disposition_set → FOLLOW_UP

Each state has:
- Allowed next states
- Actions to perform on entry
- Timeout for auto-transition
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.core.audit import log_ai_action


class ConversationState(str, Enum):
    """Conversation states."""
    NEW_LEAD = "new_lead"
    OUTREACH = "outreach"
    WAITING_REPLY = "waiting_reply"
    INTERESTED = "interested"
    SKEPTICAL = "skeptical"
    BOOKING = "booking"
    BOOKED = "booked"
    FOLLOW_UP = "follow_up"
    NURTURE = "nurture"
    STOPPED = "stopped"
    CLOSED = "closed"


class ConversationEvent(str, Enum):
    """Events that trigger state transitions."""
    LEAD_CREATED = "lead_created"
    OUTREACH_SENT = "outreach_sent"
    CUSTOMER_REPLIED = "customer_replied"
    INTENT_POSITIVE = "intent_positive"
    INTENT_INTERESTED = "intent_interested"
    INTENT_SKEPTICAL = "intent_skeptical"
    INTENT_NEGATIVE = "intent_negative"
    INTENT_STOP = "intent_stop"
    INTENT_BOOK_NOW = "intent_book_now"
    INTENT_QUESTION = "intent_question"
    INTENT_RESCHEDULE = "intent_reschedule"
    BOOKING_STARTED = "booking_started"
    SLOT_SELECTED = "slot_selected"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_COMPLETED = "appointment_completed"
    DISPOSITION_SET = "position_set"
    NO_REPLY = "no_reply"
    TIMEOUT = "timeout"
    MANUAL_OVERRIDE = "manual_override"


LEGACY_STATE_ALIASES = {
    "new": ConversationState.NEW_LEAD,
    "active": ConversationState.WAITING_REPLY,
    "initiated": ConversationState.WAITING_REPLY,
    "paused": ConversationState.FOLLOW_UP,
    "completed": ConversationState.CLOSED,
}


def parse_conversation_state(status: str) -> ConversationState:
    """Parse current conversation status, including legacy API statuses."""
    normalized = str(status or "").strip().lower()
    if normalized in LEGACY_STATE_ALIASES:
        return LEGACY_STATE_ALIASES[normalized]
    return ConversationState(normalized)


# State transition rules
TRANSITIONS: Dict[ConversationState, Dict[ConversationEvent, ConversationState]] = {
    ConversationState.NEW_LEAD: {
        ConversationEvent.LEAD_CREATED: ConversationState.NEW_LEAD,
        ConversationEvent.OUTREACH_SENT: ConversationState.OUTREACH,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.OUTREACH: {
        ConversationEvent.CUSTOMER_REPLIED: ConversationState.WAITING_REPLY,
        ConversationEvent.INTENT_POSITIVE: ConversationState.INTERESTED,
        ConversationEvent.INTENT_INTERESTED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.INTENT_NEGATIVE: ConversationState.NURTURE,
        ConversationEvent.NO_REPLY: ConversationState.OUTREACH,
        ConversationEvent.TIMEOUT: ConversationState.NURTURE,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.WAITING_REPLY: {
        ConversationEvent.INTENT_POSITIVE: ConversationState.INTERESTED,
        ConversationEvent.INTENT_INTERESTED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_SKEPTICAL: ConversationState.SKEPTICAL,
        ConversationEvent.INTENT_NEGATIVE: ConversationState.NURTURE,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_QUESTION: ConversationState.WAITING_REPLY,
        ConversationEvent.INTENT_RESCHEDULE: ConversationState.BOOKING,
        ConversationEvent.NO_REPLY: ConversationState.WAITING_REPLY,
        ConversationEvent.TIMEOUT: ConversationState.FOLLOW_UP,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.INTERESTED: {
        ConversationEvent.BOOKING_STARTED: ConversationState.BOOKING,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_SKEPTICAL: ConversationState.SKEPTICAL,
        ConversationEvent.INTENT_NEGATIVE: ConversationState.NURTURE,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.INTENT_QUESTION: ConversationState.INTERESTED,
        ConversationEvent.NO_REPLY: ConversationState.FOLLOW_UP,
        ConversationEvent.TIMEOUT: ConversationState.FOLLOW_UP,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.SKEPTICAL: {
        ConversationEvent.INTENT_POSITIVE: ConversationState.INTERESTED,
        ConversationEvent.INTENT_INTERESTED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_NEGATIVE: ConversationState.NURTURE,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.INTENT_QUESTION: ConversationState.SKEPTICAL,
        ConversationEvent.NO_REPLY: ConversationState.FOLLOW_UP,
        ConversationEvent.TIMEOUT: ConversationState.NURTURE,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.BOOKING: {
        ConversationEvent.SLOT_SELECTED: ConversationState.BOOKING,
        ConversationEvent.APPOINTMENT_BOOKED: ConversationState.BOOKED,
        ConversationEvent.APPOINTMENT_CANCELLED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.INTENT_RESCHEDULE: ConversationState.BOOKING,
        ConversationEvent.TIMEOUT: ConversationState.FOLLOW_UP,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.BOOKED: {
        ConversationEvent.APPOINTMENT_COMPLETED: ConversationState.FOLLOW_UP,
        ConversationEvent.APPOINTMENT_CANCELLED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_RESCHEDULE: ConversationState.BOOKING,
        ConversationEvent.DISPOSITION_SET: ConversationState.FOLLOW_UP,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.FOLLOW_UP: {
        ConversationEvent.CUSTOMER_REPLIED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_POSITIVE: ConversationState.INTERESTED,
        ConversationEvent.INTENT_INTERESTED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_NEGATIVE: ConversationState.NURTURE,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.NO_REPLY: ConversationState.NURTURE,
        ConversationEvent.TIMEOUT: ConversationState.NURTURE,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.NURTURE: {
        ConversationEvent.CUSTOMER_REPLIED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_POSITIVE: ConversationState.INTERESTED,
        ConversationEvent.INTENT_INTERESTED: ConversationState.INTERESTED,
        ConversationEvent.INTENT_BOOK_NOW: ConversationState.BOOKING,
        ConversationEvent.INTENT_STOP: ConversationState.STOPPED,
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.STOPPED: {
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
    ConversationState.CLOSED: {
        ConversationEvent.MANUAL_OVERRIDE: ConversationState.CLOSED,
    },
}

# Actions to perform on state entry
STATE_ENTRY_ACTIONS: Dict[ConversationState, List[str]] = {
    ConversationState.OUTREACH: ["send_initial_outreach"],
    ConversationState.WAITING_REPLY: ["start_reply_timer"],
    ConversationState.INTERESTED: ["send_booking_push"],
    ConversationState.SKEPTICAL: ["send_objection_response"],
    ConversationState.BOOKING: ["show_available_slots"],
    ConversationState.BOOKED: ["send_confirmation", "schedule_reminders"],
    ConversationState.FOLLOW_UP: ["send_follow_up"],
    ConversationState.NURTURE: ["add_to_nurture_campaign"],
    ConversationState.STOPPED: ["mark_as_stopped"],
    ConversationState.CLOSED: ["close_conversation"],
}

# Timeout durations (in hours)
STATE_TIMEOUTS: Dict[ConversationState, int] = {
    ConversationState.OUTREACH: 48,
    ConversationState.WAITING_REPLY: 72,
    ConversationState.INTERESTED: 48,
    ConversationState.SKEPTICAL: 48,
    ConversationState.BOOKING: 24,
    ConversationState.FOLLOW_UP: 72,
}


def get_allowed_transitions(current_state: ConversationState) -> Dict[ConversationEvent, ConversationState]:
    """Get allowed transitions from current state."""
    return TRANSITIONS.get(current_state, {})


def can_transition(current_state: ConversationState, event: ConversationEvent) -> bool:
    """Check if transition is allowed."""
    allowed = get_allowed_transitions(current_state)
    return event in allowed


def get_next_state(current_state: ConversationState, event: ConversationEvent) -> Optional[ConversationState]:
    """Get next state for a transition."""
    allowed = get_allowed_transitions(current_state)
    return allowed.get(event)


def get_entry_actions(state: ConversationState) -> List[str]:
    """Get actions to perform when entering a state."""
    return STATE_ENTRY_ACTIONS.get(state, [])


def get_state_timeout(state: ConversationState) -> Optional[int]:
    """Get timeout in hours for a state."""
    return STATE_TIMEOUTS.get(state)


def map_intent_to_event(intent: str) -> Optional[ConversationEvent]:
    """Map an intent string to a conversation event."""
    intent_event_map = {
        "POSITIVE": ConversationEvent.INTENT_POSITIVE,
        "INTERESTED": ConversationEvent.INTENT_INTERESTED,
        "SKEPTICAL": ConversationEvent.INTENT_SKEPTICAL,
        "NEGATIVE": ConversationEvent.INTENT_NEGATIVE,
        "STOP": ConversationEvent.INTENT_STOP,
        "BOOK_NOW": ConversationEvent.INTENT_BOOK_NOW,
        "QUESTION": ConversationEvent.INTENT_QUESTION,
        "RESCHEDULE": ConversationEvent.INTENT_RESCHEDULE,
    }
    return intent_event_map.get(intent.upper())


def transition_conversation(
    db: Session,
    conversation_id: UUID,
    event: ConversationEvent,
    tenant_id: str,
    details: Optional[Dict] = None,
) -> Dict:
    """
    Transition a conversation to a new state.

    Returns:
        Dict with success, previous_state, new_state, actions
    """
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return {"success": False, "error": "Conversation not found"}

    current_state = parse_conversation_state(conversation.status)

    # Check if transition is allowed
    if not can_transition(current_state, event):
        return {
            "success": False,
            "error": f"Transition from {current_state} with {event} not allowed",
            "current_state": current_state,
            "allowed_events": [e.value for e in get_allowed_transitions(current_state).keys()],
        }

    # Get next state
    new_state = get_next_state(current_state, event)

    # Update conversation
    conversation.status = new_state.value
    conversation.updated_at = datetime.now(timezone.utc)

    # Get entry actions
    actions = get_entry_actions(new_state)

    db.commit()

    # Log transition
    log_ai_action(
        tenant_id=tenant_id,
        action="conversation_state_transition",
        resource_type="conversation",
        resource_id=str(conversation_id),
        details={
            "previous_state": current_state.value,
            "new_state": new_state.value,
            "event": event.value,
            "actions": actions,
            **(details or {}),
        },
    )

    return {
        "success": True,
        "previous_state": current_state.value,
        "new_state": new_state.value,
        "event": event.value,
        "actions": actions,
    }


def transition_by_intent(
    db: Session,
    conversation_id: UUID,
    intent: str,
    tenant_id: str,
    details: Optional[Dict] = None,
) -> Dict:
    """
    Transition conversation based on detected intent.

    Convenience function that maps intent to event and transitions.
    """
    event = map_intent_to_event(intent)
    if not event:
        return {"success": False, "error": f"Unknown intent: {intent}"}

    return transition_conversation(db, conversation_id, event, tenant_id, details)


def get_conversation_summary(
    db: Session,
    conversation_id: UUID,
) -> Dict:
    """Get conversation state summary."""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return {"error": "Conversation not found"}

    current_state = parse_conversation_state(conversation.status)
    allowed = get_allowed_transitions(current_state)
    timeout = get_state_timeout(current_state)

    return {
        "conversation_id": str(conversation_id),
        "current_state": current_state.value,
        "allowed_transitions": {e.value: s.value for e, s in allowed.items()},
        "timeout_hours": timeout,
        "message_count": conversation.message_count,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "intent": conversation.intent,
        "sentiment": conversation.sentiment,
    }
