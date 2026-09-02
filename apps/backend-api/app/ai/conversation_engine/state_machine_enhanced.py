"""
Enhanced Conversation State Machine (Step 36.7)

9 states with intelligent AI-driven transitions:

NEW → CONTACTED → ENGAGED → INTERESTED → BOOKING → CONFIRMED → NO_SHOW → NURTURE → SUPPRESSED

AI transitions intelligently based on:
- Customer intent
- Sentiment analysis
- Objection patterns
- Response timing
- Engagement level
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.core.audit import log_ai_action


class ConvState(str, Enum):
    """Conversation states."""
    NEW = "new"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    INTERESTED = "interested"
    BOOKING = "booking"
    CONFIRMED = "confirmed"
    NO_SHOW = "no_show"
    NURTURE = "nurture"
    SUPPRESSED = "suppressed"


class ConvEvent(str, Enum):
    """Events that trigger transitions."""
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
    BOOKING_STARTED = "booking_started"
    SLOT_SELECTED = "slot_selected"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_MISSED = "appointment_missed"
    APPOINTMENT_COMPLETED = "appointment_completed"
    DISPOSITION_WON = "disposition_won"
    DISPOSITION_LOST = "disposition_lost"
    NO_REPLY_TIMEOUT = "no_reply_timeout"
    NURTURE_TRIGGERED = "nurture_triggered"


# Valid state transitions
TRANSITIONS: Dict[ConvState, Dict[ConvEvent, ConvState]] = {
    ConvState.NEW: {
        ConvEvent.LEAD_CREATED: ConvState.NEW,
        ConvEvent.OUTREACH_SENT: ConvState.CONTACTED,
    },
    ConvState.CONTACTED: {
        ConvEvent.CUSTOMER_REPLIED: ConvState.ENGAGED,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
        ConvEvent.NO_REPLY_TIMEOUT: ConvState.NURTURE,
    },
    ConvState.ENGAGED: {
        ConvEvent.INTENT_POSITIVE: ConvState.INTERESTED,
        ConvEvent.INTENT_INTERESTED: ConvState.INTERESTED,
        ConvEvent.INTENT_BOOK_NOW: ConvState.BOOKING,
        ConvEvent.INTENT_SKEPTICAL: ConvState.ENGAGED,  # Stay engaged
        ConvEvent.INTENT_NEGATIVE: ConvState.NURTURE,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
        ConvEvent.INTENT_QUESTION: ConvState.ENGAGED,
    },
    ConvState.INTERESTED: {
        ConvEvent.BOOKING_STARTED: ConvState.BOOKING,
        ConvEvent.INTENT_BOOK_NOW: ConvState.BOOKING,
        ConvEvent.INTENT_SKEPTICAL: ConvState.ENGAGED,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
        ConvEvent.INTENT_NEGATIVE: ConvState.NURTURE,
    },
    ConvState.BOOKING: {
        ConvEvent.SLOT_SELECTED: ConvState.BOOKING,
        ConvEvent.APPOINTMENT_BOOKED: ConvState.CONFIRMED,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
    },
    ConvState.CONFIRMED: {
        ConvEvent.APPOINTMENT_COMPLETED: ConvState.CONFIRMED,
        ConvEvent.APPOINTMENT_MISSED: ConvState.NO_SHOW,
        ConvEvent.DISPOSITION_WON: ConvState.CONFIRMED,
        ConvEvent.DISPOSITION_LOST: ConvState.NURTURE,
    },
    ConvState.NO_SHOW: {
        ConvEvent.APPOINTMENT_BOOKED: ConvState.CONFIRMED,  # Rescheduled
        ConvEvent.NURTURE_TRIGGERED: ConvState.NURTURE,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
    },
    ConvState.NURTURE: {
        ConvEvent.CUSTOMER_REPLIED: ConvState.ENGAGED,
        ConvEvent.INTENT_STOP: ConvState.SUPPRESSED,
        ConvEvent.LEAD_CREATED: ConvState.NEW,  # Re-engagement
    },
    ConvState.SUPPRESSED: {
        # No transitions out of suppressed
    },
}


# State metadata
STATE_METADATA: Dict[ConvState, Dict] = {
    ConvState.NEW: {
        "label": "New Lead",
        "description": "Lead just entered the system",
        "ai_behavior": "Prepare for outreach",
        "timeout_hours": None,
        "next_actions": ["send_outreach"],
    },
    ConvState.CONTACTED: {
        "label": "Contacted",
        "description": "Initial outreach sent, waiting for response",
        "ai_behavior": "Wait for reply, follow up if needed",
        "timeout_hours": 72,
        "timeout_event": ConvEvent.NO_REPLY_TIMEOUT,
        "next_actions": ["wait", "follow_up"],
    },
    ConvState.ENGAGED: {
        "label": "Engaged",
        "description": "Customer is responding and interacting",
        "ai_behavior": "Build rapport, qualify, push toward booking",
        "timeout_hours": 48,
        "next_actions": ["qualify", "handle_objection", "push_booking"],
    },
    ConvState.INTERESTED: {
        "label": "Interested",
        "description": "Customer has expressed interest",
        "ai_behavior": "Push for booking, offer time slots",
        "timeout_hours": 24,
        "next_actions": ["offer_slots", "book_appointment"],
    },
    ConvState.BOOKING: {
        "label": "Booking",
        "description": "Customer is selecting a time slot",
        "ai_behavior": "Help with slot selection, confirm booking",
        "timeout_hours": 1,
        "next_actions": ["present_slots", "confirm_booking"],
    },
    ConvState.CONFIRMED: {
        "label": "Confirmed",
        "description": "Appointment booked and confirmed",
        "ai_behavior": "Send reminders, build excitement",
        "timeout_hours": None,
        "next_actions": ["send_reminders", "prepare_agent"],
    },
    ConvState.NO_SHOW: {
        "label": "No Show",
        "description": "Customer missed their appointment",
        "ai_behavior": "Follow up, offer reschedule",
        "timeout_hours": 72,
        "next_actions": ["follow_up", "offer_reschedule"],
    },
    ConvState.NURTURE: {
        "label": "Nurture",
        "description": "Long-term nurturing for cold leads",
        "ai_behavior": "Periodic check-ins, value offers",
        "timeout_hours": None,
        "next_actions": ["periodic_check_in", "value_offer"],
    },
    ConvState.SUPPRESSED: {
        "label": "Suppressed",
        "description": "Customer opted out, do not contact",
        "ai_behavior": "Do not send any messages",
        "timeout_hours": None,
        "next_actions": [],
    },
}


def get_state_metadata(state: str) -> Dict:
    """Get metadata for a conversation state."""
    try:
        conv_state = ConvState(state)
        return STATE_METADATA.get(conv_state, {})
    except ValueError:
        return {"label": state, "description": "Unknown state"}


def map_intent_to_event(intent: str) -> Optional[ConvEvent]:
    """Map an AI-detected intent to a conversation event."""
    mapping = {
        "STOP": ConvEvent.INTENT_STOP,
        "POSITIVE": ConvEvent.INTENT_POSITIVE,
        "BOOK_NOW": ConvEvent.INTENT_BOOK_NOW,
        "INTERESTED": ConvEvent.INTENT_INTERESTED,
        "SKEPTICAL": ConvEvent.INTENT_SKEPTICAL,
        "NEGATIVE": ConvEvent.INTENT_NEGATIVE,
        "QUESTION": ConvEvent.INTENT_QUESTION,
        "RESCHEDULE": ConvEvent.BOOKING_STARTED,
    }
    return mapping.get(intent)


def can_transition(current_state: str, event: str) -> bool:
    """Check if a transition is valid."""
    try:
        state = ConvState(current_state)
        conv_event = ConvEvent(event)
        return conv_event in TRANSITIONS.get(state, {})
    except ValueError:
        return False


def get_next_state(current_state: str, event: str) -> Optional[str]:
    """Get the next state for a given current state and event."""
    try:
        state = ConvState(current_state)
        conv_event = ConvEvent(event)
        next_state = TRANSITIONS.get(state, {}).get(conv_event)
        return next_state.value if next_state else None
    except ValueError:
        return None


def get_timeout_state(state: str) -> Optional[Tuple[str, int]]:
    """
    Get the timeout event and hours for a state.

    Returns (event, hours) or None if no timeout.
    """
    try:
        conv_state = ConvState(state)
        metadata = STATE_METADATA.get(conv_state, {})
        timeout_hours = metadata.get("timeout_hours")
        timeout_event = metadata.get("timeout_event")

        if timeout_hours and timeout_event:
            return (timeout_event.value, timeout_hours)
    except ValueError:
        pass
    return None


def transition(
    db: Session,
    conversation: Conversation,
    event: str,
    tenant_id: str,
) -> Dict:
    """
    Execute a state transition on a conversation.

    Args:
        db: Database session
        conversation: Conversation to transition
        event: Event triggering the transition
        tenant_id: Tenant ID for audit logging

    Returns:
        Dict with success, old_state, new_state
    """
    current_state = conversation.status or ConvState.NEW.value

    if not can_transition(current_state, event):
        return {
            "success": False,
            "old_state": current_state,
            "error": f"Invalid transition: {current_state} + {event}",
        }

    new_state = get_next_state(current_state, event)

    if not new_state:
        return {
            "success": False,
            "old_state": current_state,
            "error": f"No target state for {current_state} + {event}",
        }

    old_state = conversation.status
    conversation.status = new_state
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()

    log_ai_action(
        tenant_id=tenant_id,
        action="conversation_state_transition",
        resource_type="conversation",
        resource_id=str(conversation.id),
        details={
            "old_state": old_state,
            "new_state": new_state,
            "event": event,
        },
    )

    return {
        "success": True,
        "old_state": old_state,
        "new_state": new_state,
        "event": event,
    }


def transition_by_intent(
    db: Session,
    conversation_id: str,
    intent: str,
    tenant_id: str,
) -> Dict:
    """
    Transition a conversation based on detected intent.

    Convenience function that maps intent to event and executes transition.
    """
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id,
    ).first()

    if not conversation:
        return {"success": False, "error": "Conversation not found"}

    event = map_intent_to_event(intent)
    if not event:
        return {
            "success": False,
            "error": f"No event mapping for intent: {intent}",
        }

    return transition(db, conversation, event.value, tenant_id)


def check_timeouts(db: Session, tenant_id: str) -> List[Dict]:
    """
    Check for conversations that have timed out and need transition.

    Called by Celery beat to auto-transition stale conversations.
    """
    now = datetime.now(timezone.utc)
    transitions = []

    conversations = db.query(Conversation).filter(
        Conversation.tenant_id == tenant_id,
        Conversation.status.in_([
            ConvState.CONTACTED.value,
            ConvState.ENGAGED.value,
            ConvState.INTERESTED.value,
            ConvState.BOOKING.value,
            ConvState.NO_SHOW.value,
        ]),
    ).all()

    for conv in conversations:
        timeout_info = get_timeout_state(conv.status)
        if not timeout_info:
            continue

        event, hours = timeout_info
        last_activity = conv.last_message_at or conv.created_at

        if last_activity and (now - last_activity) > timedelta(hours=hours):
            result = transition(db, conv, event, tenant_id)
            if result["success"]:
                transitions.append({
                    "conversation_id": str(conv.id),
                    "old_state": result["old_state"],
                    "new_state": result["new_state"],
                    "event": event,
                })

    return transitions
