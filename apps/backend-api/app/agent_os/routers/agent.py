"""
Agent OS Router

Endpoints:
- GET /agent/dashboard — Dashboard data
- GET /agent/calendar/daily — Daily calendar view
- GET /agent/calendar/weekly — Weekly calendar view
- GET /agent/calendar/agenda — Agenda view
- GET /agent/lead/{id}/summary — Lead summary
- POST /agent/appointment/{id}/disposition — Set disposition
- GET /agent/dispositions — List dispositions
- GET /agent/stats — Disposition stats
"""

from typing import Optional
from uuid import UUID
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.models.agent import Agent
from app.agent_os.services.dashboard import get_dashboard_data
from app.agent_os.services.calendar_views import (
    get_daily_view,
    get_weekly_view,
    get_agenda_view,
)
from app.agent_os.services.lead_summary import generate_lead_summary
from app.agent_os.services.disposition import (
    get_dispositions,
    set_disposition,
    get_disposition_stats,
)
from app.agent_os.services.post_call import process_post_call

router = APIRouter(prefix="/agent", tags=["agent"])


class DispositionRequest(BaseModel):
    disposition: str
    notes: Optional[str] = None
    call_duration_seconds: Optional[int] = None


def get_agent_for_user(db: Session, user: User) -> Agent:
    """Get the agent record for a user."""
    agent = db.query(Agent).filter(Agent.user_id == user.id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent profile not found")
    return agent


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agent dashboard data."""
    agent = get_agent_for_user(db, current_user)
    return get_dashboard_data(db, agent.id)


@router.get("/calendar/daily")
def calendar_daily(
    target_date: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get daily calendar view."""
    agent = get_agent_for_user(db, current_user)
    if not target_date:
        target_date = date.today()
    return get_daily_view(db, agent.id, target_date)


@router.get("/calendar/weekly")
def calendar_weekly(
    week_start: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get weekly calendar view."""
    agent = get_agent_for_user(db, current_user)
    return get_weekly_view(db, agent.id, week_start)


@router.get("/calendar/agenda")
def calendar_agenda(
    start_date: Optional[date] = None,
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agenda view."""
    agent = get_agent_for_user(db, current_user)
    if not start_date:
        start_date = date.today()
    return get_agenda_view(db, agent.id, start_date, days)


@router.get("/lead/{lead_id}/summary")
def lead_summary(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get AI-generated lead summary."""
    return generate_lead_summary(db, lead_id)


@router.get("/dispositions")
def list_dispositions(
    current_user: User = Depends(get_current_active_user),
):
    """List all available dispositions."""
    return {"dispositions": get_dispositions()}


@router.post("/appointment/{appointment_id}/disposition")
def set_appointment_disposition(
    appointment_id: UUID,
    request: DispositionRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Set disposition for a completed call and trigger post-call automation."""
    # Set disposition
    result = set_disposition(
        db=db,
        appointment_id=appointment_id,
        tenant_id=tenant_id,
        disposition=request.disposition,
        notes=request.notes,
        call_duration_seconds=request.call_duration_seconds,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    # Trigger post-call automation
    from app.models.appointment import Appointment
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if appointment:
        automation_result = process_post_call(db, appointment, request.disposition)
        result["automation"] = automation_result

    return result


@router.get("/stats")
def disposition_stats(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get disposition statistics."""
    agent = get_agent_for_user(db, current_user)
    return get_disposition_stats(db, tenant_id, agent.id, start_date, end_date)


class ManualAppointmentRequest(BaseModel):
    lead_id: UUID
    start_time: datetime          # ISO 8601 (UTC)
    duration_minutes: int = 15    # appointments are 15 minutes


@router.post("/appointment")
async def create_manual_appointment(
    request: ManualAppointmentRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Let an agent manually book an appointment with an existing lead.

    Resolves the current agent server-side (so the UI never needs the agent_id),
    enforces no double-booking, and creates a standard appointment. Additive —
    does not alter the CSV->booking pipeline.
    """
    from datetime import timedelta
    from app.models.appointment import Appointment
    from app.models.lead import Lead
    from app.booking.services.reminders import schedule_reminders
    from app.realtime.websocket import emit_to_tenant

    agent = get_agent_for_user(db, current_user)

    lead = db.query(Lead).filter(Lead.id == request.lead_id, Lead.tenant_id == tenant_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    start = request.start_time
    end = start + timedelta(minutes=request.duration_minutes or 15)

    # Prevent double-booking for this agent.
    overlap = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.agent_id == agent.id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time < end,
            Appointment.end_time > start,
        )
        .first()
    )
    if overlap:
        raise HTTPException(status_code=409, detail="That time is already booked for you")

    appt = Appointment(
        tenant_id=tenant_id,
        lead_id=lead.id,
        agent_id=agent.id,
        start_time=start,
        end_time=end,
        status="confirmed",
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    schedule_reminders(db, appt)
    await emit_to_tenant(tenant_id, "appointment_created", {
        "appointment_id": str(appt.id),
        "lead_id": str(appt.lead_id),
        "agent_id": str(appt.agent_id),
        "start_time": appt.start_time.isoformat(),
        "end_time": appt.end_time.isoformat(),
        "status": appt.status,
    })
    return {
        "id": str(appt.id),
        "lead_id": str(appt.lead_id),
        "agent_id": str(appt.agent_id),
        "start_time": appt.start_time.isoformat(),
        "end_time": appt.end_time.isoformat(),
        "status": appt.status,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
    }
