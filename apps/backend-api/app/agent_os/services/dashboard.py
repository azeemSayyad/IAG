"""
Agent Dashboard Service (Step 7.1)

Provides dashboard widgets:
- Upcoming calls
- Agent utilization
- Reminders
- Recent notes
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.user import User


def get_upcoming_calls(
    db: Session,
    agent_id: UUID,
    limit: int = 10,
) -> List[Dict]:
    """
    Get upcoming calls for an agent.
    """
    now = datetime.now(timezone.utc)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.status == "confirmed",
            Appointment.start_time > now,
        )
        .order_by(Appointment.start_time)
        .limit(limit)
        .all()
    )

    calls = []
    for apt in appointments:
        lead = db.query(Lead).filter(Lead.id == apt.lead_id).first()
        calls.append({
            "appointment_id": str(apt.id),
            "start_time": apt.start_time.isoformat(),
            "end_time": apt.end_time.isoformat(),
            "start_display": apt.start_time.strftime("%I:%M %p").lstrip("0"),
            "date_display": apt.start_time.strftime("%b %d"),
            "lead": {
                "id": str(lead.id) if lead else None,
                "name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                "phone": lead.phone if lead else None,
                "lead_score": lead.lead_score if lead else 0,
            } if lead else None,
            "time_until": _format_time_until(apt.start_time),
        })

    return calls


def get_agent_utilization(
    db: Session,
    agent_id: UUID,
    target_date: date = None,
) -> Dict:
    """
    Calculate agent utilization for a specific date.
    """
    if not target_date:
        target_date = date.today()

    start_of_day = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_of_day = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    # Get appointments for the day
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= start_of_day,
            Appointment.start_time < end_of_day,
            Appointment.status.in_(["confirmed", "completed"]),
        )
        .all()
    )

    # Calculate metrics
    total_booked = len(appointments)
    total_completed = sum(1 for a in appointments if a.status == "completed")
    total_minutes = sum(
        (a.end_time - a.start_time).total_seconds() / 60
        for a in appointments
    )

    # Business hours: 10 AM to 9 PM = 11 hours = 660 minutes
    available_minutes = 660
    utilization_pct = (total_minutes / available_minutes * 100) if available_minutes > 0 else 0

    return {
        "date": target_date.isoformat(),
        "total_booked": total_booked,
        "total_completed": total_completed,
        "total_minutes": round(total_minutes),
        "utilization_pct": round(utilization_pct, 1),
        "available_minutes": available_minutes,
    }


def get_reminders(
    db: Session,
    agent_id: UUID,
) -> List[Dict]:
    """
    Get reminders for an agent.
    """
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(hours=24)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.status == "confirmed",
            Appointment.start_time > now,
            Appointment.start_time <= tomorrow,
        )
        .order_by(Appointment.start_time)
        .all()
    )

    reminders = []
    for apt in appointments:
        lead = db.query(Lead).filter(Lead.id == apt.lead_id).first()
        time_until = (apt.start_time - now).total_seconds() / 3600

        reminder_type = None
        if time_until <= 0.25:
            reminder_type = "15m"
        elif time_until <= 1:
            reminder_type = "1h"
        elif time_until <= 24:
            reminder_type = "24h"

        if reminder_type:
            reminders.append({
                "appointment_id": str(apt.id),
                "type": reminder_type,
                "start_time": apt.start_time.isoformat(),
                "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
                "time_until": _format_time_until(apt.start_time),
            })

    return reminders


def get_recent_notes(
    db: Session,
    agent_id: UUID,
    limit: int = 10,
) -> List[Dict]:
    """
    Get recent notes from completed appointments.
    """
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.status == "completed",
            Appointment.notes.isnot(None),
            Appointment.notes != "",
        )
        .order_by(Appointment.updated_at.desc())
        .limit(limit)
        .all()
    )

    notes = []
    for apt in appointments:
        lead = db.query(Lead).filter(Lead.id == apt.lead_id).first()
        notes.append({
            "appointment_id": str(apt.id),
            "lead_name": f"{lead.first_name} {lead.last_name}" if lead else "Unknown",
            "disposition": apt.disposition,
            "notes": apt.notes,
            "updated_at": apt.updated_at.isoformat(),
        })

    return notes


def get_dashboard_data(
    db: Session,
    agent_id: UUID,
) -> Dict:
    """
    Get all dashboard data for an agent.
    """
    return {
        "upcoming_calls": get_upcoming_calls(db, agent_id),
        "utilization": get_agent_utilization(db, agent_id),
        "reminders": get_reminders(db, agent_id),
        "recent_notes": get_recent_notes(db, agent_id),
    }


def _format_time_until(target_time: datetime) -> str:
    """Format time until target as human-readable string."""
    now = datetime.now(timezone.utc)
    diff = target_time - now
    total_seconds = diff.total_seconds()

    if total_seconds < 0:
        return "passed"
    elif total_seconds < 60:
        return f"{int(total_seconds)}s"
    elif total_seconds < 3600:
        minutes = int(total_seconds / 60)
        return f"{minutes}m"
    elif total_seconds < 86400:
        hours = int(total_seconds / 3600)
        minutes = int((total_seconds % 3600) / 60)
        return f"{hours}h {minutes}m"
    else:
        days = int(total_seconds / 86400)
        hours = int((total_seconds % 86400) / 3600)
        return f"{days}d {hours}h"
