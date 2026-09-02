"""
Booking Router

Endpoints:
- POST /booking/start — Start booking flow for a lead
- POST /booking/select — Process slot selection
- POST /booking/cancel — Cancel a booking
- POST /booking/reschedule — Reschedule a booking
- GET /booking/slots — Get available slots
- GET /booking/reminders — Get pending reminders
- POST /booking/reminders/process — Process pending reminders
- GET /booking/no-show/predict — Predict no-show for appointment
- GET /booking/no-show/batch — Predict no-shows for date
- POST /booking/waitlist — Add to waitlist
- GET /booking/waitlist — Get waitlist
- GET /booking/overflow — Get overflow queue
"""

from typing import Optional, List
from uuid import UUID
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.appointment import Appointment
from app.booking.services.booking import (
    start_booking_flow,
    process_slot_selection,
    cancel_booking,
    reschedule_booking,
)
from app.booking.services.availability import (
    get_merged_available_slots,
    get_available_slots_for_agent,
    set_agent_availability,
    set_agent_break,
)
from app.booking.services.reminders import (
    get_pending_reminders,
    process_pending_reminders,
)

router = APIRouter(prefix="/booking", tags=["booking"])


class BookingStartRequest(BaseModel):
    lead_id: UUID
    conversation_id: Optional[UUID] = None


class SlotSelectRequest(BaseModel):
    lead_id: UUID
    conversation_id: UUID
    reply: str


class CancelRequest(BaseModel):
    appointment_id: UUID
    reason: Optional[str] = None


class RescheduleRequest(BaseModel):
    appointment_id: UUID


class SetAvailabilityRequest(BaseModel):
    agent_id: UUID
    start_time: datetime
    end_time: datetime
    status: str = "available"
    notes: Optional[str] = None


@router.post("/start")
def start_booking(
    request: BookingStartRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Start the booking flow for a lead."""
    lead = db.query(Lead).filter(
        Lead.id == request.lead_id,
        Lead.tenant_id == tenant_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Get or create conversation
    conversation = None
    if request.conversation_id:
        conversation = db.query(Conversation).filter(
            Conversation.id == request.conversation_id,
        ).first()

    if not conversation:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.lead_id == lead.id,
                Conversation.status.in_(["active", "initiated"]),
            )
            .first()
        )

    if not conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            lead_id=lead.id,
            status="active",
        )
        db.add(conversation)
        db.commit()

    result = start_booking_flow(db, tenant_id, lead, conversation)
    return result.to_dict()


@router.post("/select")
def select_slot(
    request: SlotSelectRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process a customer's slot selection."""
    lead = db.query(Lead).filter(
        Lead.id == request.lead_id,
        Lead.tenant_id == tenant_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    conversation = db.query(Conversation).filter(
        Conversation.id == request.conversation_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = process_slot_selection(db, tenant_id, lead, conversation, request.reply)
    return result.to_dict()


@router.post("/cancel")
def cancel(
    request: CancelRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Cancel a booking."""
    result = cancel_booking(db, tenant_id, request.appointment_id, request.reason)
    return result.to_dict()


@router.post("/reschedule")
def reschedule(
    request: RescheduleRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Reschedule a booking."""
    appointment = db.query(Appointment).filter(
        Appointment.id == request.appointment_id,
        Appointment.tenant_id == tenant_id,
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    conversation = db.query(Conversation).filter(
        Conversation.lead_id == lead.id,
        Conversation.status.in_(["active", "booked"]),
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = reschedule_booking(db, tenant_id, request.appointment_id, lead, conversation)
    return result.to_dict()


@router.get("/slots")
def get_slots(
    target_date: Optional[date] = None,
    agent_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get available slots."""
    if not target_date:
        target_date = date.today()

    if agent_id:
        slots = get_available_slots_for_agent(db, agent_id, target_date)
    else:
        slots = get_merged_available_slots(db, tenant_id, target_date)

    return {
        "date": target_date.isoformat(),
        "slots": [s.to_dict() for s in slots],
        "total": len(slots),
    }


@router.get("/reminders")
def list_reminders(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get pending reminders."""
    reminders = get_pending_reminders(db, tenant_id)
    return {"reminders": reminders, "total": len(reminders)}


@router.post("/reminders/process")
def process_reminders(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process pending reminders."""
    result = process_pending_reminders(db)
    return result


# --- No-Show Prediction ---

from app.booking.services.no_show_prediction import (
    predict_appointment_no_show,
    predict_batch_no_shows,
)
from app.booking.services.overflow import (
    add_to_waitlist,
    remove_from_waitlist,
    get_waitlist,
    get_overflow_queue,
)


@router.get("/no-show/predict")
def predict_no_show(
    appointment_id: UUID = Query(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Predict no-show probability for an appointment."""
    result = predict_appointment_no_show(db, appointment_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/no-show/batch")
def predict_no_shows_batch(
    date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Predict no-show probabilities for all appointments on a date."""
    target_date = datetime.combine(date, datetime.min.time()) if date else datetime.now(timezone.utc)
    predictions = predict_batch_no_shows(db, tenant_id, target_date)
    return {"predictions": predictions, "total": len(predictions)}


# --- Waitlist ---

class WaitlistRequest(BaseModel):
    lead_id: UUID
    preferred_time: datetime
    preferred_agent_id: Optional[UUID] = None


@router.post("/waitlist")
def add_waitlist(
    data: WaitlistRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Add a lead to the booking waitlist."""
    result = add_to_waitlist(
        tenant_id=tenant_id,
        lead_id=str(data.lead_id),
        preferred_time=data.preferred_time,
        preferred_agent_id=str(data.preferred_agent_id) if data.preferred_agent_id else None,
    )
    return result


@router.get("/waitlist")
def list_waitlist(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get all waitlist entries."""
    entries = get_waitlist(tenant_id)
    return {"entries": entries, "total": len(entries)}


@router.delete("/waitlist/{lead_id}")
def remove_waitlist(
    lead_id: UUID,
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Remove a lead from the waitlist."""
    result = remove_from_waitlist(tenant_id, str(lead_id))
    return result


# --- Overflow Queue ---

@router.get("/overflow")
def list_overflow(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get overflow queue entries."""
    entries = get_overflow_queue(tenant_id)
    return {"entries": entries, "total": len(entries)}
