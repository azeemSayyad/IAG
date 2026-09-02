"""
Booking Conversation Engine (Step 6.5)

Handles the full booking flow:
1. Get available slots
2. Present 3 numbered options to customer
3. Customer replies with number
4. Lock slot
5. Assign agent
6. Create appointment
7. Send confirmation
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.booking.services.slots import (
    TimeSlot,
    get_available_slots,
    format_slot_options,
    parse_slot_selection,
)
from app.booking.services.availability import get_merged_available_slots
from app.booking.services.locking import acquire_slot_lock, release_slot_lock
from app.booking.services.assignment import assign_agent
from app.core.redis import redis_service
from app.core.audit import log_ai_action


class BookingResult:
    def __init__(self, success: bool, message: str = "", data: Dict = None):
        self.success = success
        self.message = message
        self.data = data or {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            **self.data,
        }


def start_booking_flow(
    db: Session,
    tenant_id: str,
    lead: Lead,
    conversation: Conversation,
) -> BookingResult:
    """
    Start the booking flow for a lead.

    1. Get available slots for next 3 days
    2. Present 3 options
    """
    from datetime import date, timedelta

    # Get available slots for today and next 2 days
    all_slots = []
    for i in range(3):
        target_date = date.today() + timedelta(days=i)
        day_slots = get_merged_available_slots(db, tenant_id, target_date)
        all_slots.extend(day_slots)

    if not all_slots:
        return BookingResult(
            success=False,
            message="No available slots at the moment. Please try again later.",
        )

    # Format as options
    options = format_slot_options(all_slots, count=3)

    if not options:
        return BookingResult(
            success=False,
            message="No available slots at the moment. Please try again later.",
        )

    # Store options in conversation context
    memory = conversation.ai_context or {}
    memory["booking_options"] = options
    memory["booking_state"] = "awaiting_selection"
    conversation.ai_context = memory
    db.commit()

    # Format message
    options_text = "\n".join([f"{opt['number']}. {opt['display']} ({opt['date_display']})" for opt in options])
    message = f"Here are some available times:\n{options_text}\nJust reply with the number!"

    return BookingResult(
        success=True,
        message=message,
        data={"options": options},
    )


def process_slot_selection(
    db: Session,
    tenant_id: str,
    lead: Lead,
    conversation: Conversation,
    reply: str,
) -> BookingResult:
    """
    Process a customer's slot selection.

    1. Parse the selection
    2. Lock the slot
    3. Assign agent
    4. Create appointment
    5. Send confirmation
    """
    # Get stored options
    memory = conversation.ai_context or {}
    options = memory.get("booking_options", [])

    if not options:
        return BookingResult(
            success=False,
            message="No booking options available. Let me get some new times for you.",
        )

    # Parse selection
    selected = parse_slot_selection(reply, options)

    if not selected:
        return BookingResult(
            success=False,
            message="I didn't catch that. Please reply with a number (1, 2, or 3).",
        )

    # Parse times
    start_time = datetime.fromisoformat(selected["start_time"])
    end_time = datetime.fromisoformat(selected["end_time"])

    # Find an available agent and lock the slot
    agent = assign_agent(
        db=db,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
    )

    if not agent:
        return BookingResult(
            success=False,
            message="Sorry, that slot just got taken. Let me get you some new options.",
        )

    # Acquire slot lock
    slot_key = selected.get("slot_key", start_time.strftime("%Y%m%d_%H%M"))
    lock_acquired = acquire_slot_lock(
        tenant_id=tenant_id,
        agent_id=str(agent.id),
        slot_key=slot_key,
        lead_id=str(lead.id),
    )

    if not lock_acquired:
        return BookingResult(
            success=False,
            message="Sorry, that slot was just taken by someone else. Let me get you some new options.",
        )

    # Create appointment
    appointment = Appointment(
        tenant_id=tenant_id,
        lead_id=lead.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        start_time=start_time,
        end_time=end_time,
        status="confirmed",
    )
    db.add(appointment)

    # Update lead status
    lead.status = "booked"

    # Update conversation
    conversation.status = "booked"

    # Clear booking state
    memory["booking_state"] = "completed"
    memory["appointment_id"] = str(appointment.id)
    conversation.ai_context = memory

    db.commit()
    db.refresh(appointment)

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="appointment_created",
        resource_type="appointment",
        resource_id=str(appointment.id),
        details={
            "lead_id": str(lead.id),
            "agent_id": str(agent.id),
            "start_time": start_time.isoformat(),
        },
    )

    # Format confirmation message
    display_time = start_time.strftime("%A, %B %d at %I:%M %p").lstrip("0")
    confirmation = f"You're all set, {lead.first_name}! Your appointment is confirmed for {display_time}. Looking forward to it!"

    return BookingResult(
        success=True,
        message=confirmation,
        data={
            "appointment_id": str(appointment.id),
            "agent_id": str(agent.id),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
    )


def cancel_booking(
    db: Session,
    tenant_id: str,
    appointment_id: UUID,
    reason: str = None,
) -> BookingResult:
    """
    Cancel a booking.
    """
    appointment = (
        db.query(Appointment)
        .filter(
            Appointment.id == appointment_id,
            Appointment.tenant_id == tenant_id,
        )
        .first()
    )

    if not appointment:
        return BookingResult(
            success=False,
            message="Appointment not found.",
        )

    if appointment.status in ("cancelled", "completed"):
        return BookingResult(
            success=False,
            message=f"Appointment is already {appointment.status}.",
        )

    # Release slot lock
    slot_key = appointment.start_time.strftime("%Y%m%d_%H%M")
    release_slot_lock(
        tenant_id=tenant_id,
        agent_id=str(appointment.agent_id),
        slot_key=slot_key,
        lead_id=str(appointment.lead_id),
    )

    # Update appointment
    appointment.status = "cancelled"
    appointment.cancelled_reason = reason
    db.commit()

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="appointment_cancelled",
        resource_type="appointment",
        resource_id=str(appointment.id),
        details={"reason": reason},
    )

    return BookingResult(
        success=True,
        message="Appointment cancelled successfully.",
        data={"appointment_id": str(appointment.id)},
    )


def reschedule_booking(
    db: Session,
    tenant_id: str,
    appointment_id: UUID,
    lead: Lead,
    conversation: Conversation,
) -> BookingResult:
    """
    Reschedule a booking — cancel old and start new booking flow.
    """
    # Cancel existing
    cancel_result = cancel_booking(db, tenant_id, appointment_id, reason="rescheduled")
    if not cancel_result.success:
        return cancel_result

    # Start new booking flow
    return start_booking_flow(db, tenant_id, lead, conversation)
