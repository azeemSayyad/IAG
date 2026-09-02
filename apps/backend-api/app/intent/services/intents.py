"""
Intent Classes (Step 5.2)

8 intent classes for customer message classification:

1. POSITIVE — Customer is ready or eager
2. INTERESTED — Customer wants more info
3. SKEPTICAL — Customer has objections
4. NEGATIVE — Customer is not interested
5. STOP — Customer wants to opt out
6. BOOK_NOW — Customer explicitly wants to book
7. QUESTION — Customer asked a specific question
8. RESCHEDULE — Customer wants to change existing booking
"""

from enum import Enum
from typing import Dict, Optional


class Intent(str, Enum):
    POSITIVE = "POSITIVE"
    INTERESTED = "INTERESTED"
    SKEPTICAL = "SKEPTICAL"
    NEGATIVE = "NEGATIVE"
    STOP = "STOP"
    BOOK_NOW = "BOOK_NOW"
    QUESTION = "QUESTION"
    RESCHEDULE = "RESCHEDULE"


# Intent descriptions and next actions
INTENT_METADATA = {
    Intent.POSITIVE: {
        "description": "Customer is ready or eager",
        "next_action": "push_booking",
        "priority": 1,
    },
    Intent.INTERESTED: {
        "description": "Customer wants more information",
        "next_action": "provide_info_then_booking",
        "priority": 2,
    },
    Intent.SKEPTICAL: {
        "description": "Customer has objections or concerns",
        "next_action": "handle_objection_then_booking",
        "priority": 3,
    },
    Intent.NEGATIVE: {
        "description": "Customer is not interested",
        "next_action": "acknowledge_and_soft_close",
        "priority": 5,
    },
    Intent.STOP: {
        "description": "Customer wants to opt out",
        "next_action": "opt_out_immediately",
        "priority": 0,  # Highest priority
    },
    Intent.BOOK_NOW: {
        "description": "Customer explicitly wants to book",
        "next_action": "go_to_booking_flow",
        "priority": 0,  # Highest priority
    },
    Intent.QUESTION: {
        "description": "Customer asked a specific question",
        "next_action": "answer_then_booking",
        "priority": 3,
    },
    Intent.RESCHEDULE: {
        "description": "Customer wants to change existing booking",
        "next_action": "go_to_reschedule_flow",
        "priority": 2,
    },
}


def get_intent_metadata(intent: Intent) -> Dict:
    """Get metadata for an intent."""
    return INTENT_METADATA.get(intent, {})


def get_next_action(intent: Intent) -> str:
    """Get the next action for an intent."""
    return INTENT_METADATA.get(intent, {}).get("next_action", "unknown")


def get_intent_priority(intent: Intent) -> int:
    """Get priority for an intent (lower = higher priority)."""
    return INTENT_METADATA.get(intent, {}).get("priority", 10)
