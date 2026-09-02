"""
Calendar Views Service (Step 7.2)

Provides calendar data for:
- Daily view
- Weekly view
- Agenda view

Formats data for FullCalendar.js frontend.
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.agent_availability import AgentAvailability


def get_calendar_events(
    db: Session,
    agent_id: UUID,
    start_date: date,
    end_date: date,
) -> List[Dict]:
    """
    Get calendar events for an agent in a date range.
    Returns events formatted for FullCalendar.js.
    """
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= start_dt,
            Appointment.start_time < end_dt,
            Appointment.status.in_(["pending", "confirmed", "completed"]),
        )
        .order_by(Appointment.start_time)
        .all()
    )

    events = []
    for apt in appointments:
        lead = db.query(Lead).filter(Lead.id == apt.lead_id).first()

        # Color based on status
        color_map = {
            "pending": "#f59e0b",      # amber
            "confirmed": "#3b82f6",    # blue
            "completed": "#10b981",    # green
            "cancelled": "#ef4444",    # red
            "no_show": "#6b7280",      # gray
        }

        from app.core.timezones import format_in_tz
        from app.core.config import settings as _s
        events.append({
            "id": str(apt.id),
            "title": f"{lead.first_name} {lead.last_name}" if lead else "Appointment",
            "start": apt.start_time.isoformat(),
            "end": apt.end_time.isoformat(),
            # Agent-facing display is always Eastern (agent timezone).
            "start_et": format_in_tz(apt.start_time, _s.AGENT_TZ),
            "end_et": format_in_tz(apt.end_time, _s.AGENT_TZ),
            "color": color_map.get(apt.status, "#3b82f6"),
            "extendedProps": {
                "status": apt.status,
                "disposition": apt.disposition,
                "lead_id": str(apt.lead_id) if apt.lead_id else None,
                "lead_name": f"{lead.first_name} {lead.last_name}" if lead else None,
                "lead_score": lead.lead_score if lead else 0,
                "notes": apt.notes,
            },
        })

    return events


def get_daily_view(
    db: Session,
    agent_id: UUID,
    target_date: date,
) -> Dict:
    """
    Get daily calendar view data.
    """
    events = get_calendar_events(db, agent_id, target_date, target_date)
    availability = get_availability_blocks(db, agent_id, target_date, target_date)

    return {
        "date": target_date.isoformat(),
        "events": events,
        "availability": availability,
        "total_events": len(events),
    }


def get_weekly_view(
    db: Session,
    agent_id: UUID,
    week_start: date = None,
) -> Dict:
    """
    Get weekly calendar view data.
    """
    if not week_start:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday

    week_end = week_start + timedelta(days=6)  # Sunday

    events = get_calendar_events(db, agent_id, week_start, week_end)
    availability = get_availability_blocks(db, agent_id, week_start, week_end)

    # Group events by day
    daily_counts = {}
    for i in range(7):
        day = week_start + timedelta(days=i)
        daily_counts[day.isoformat()] = 0

    for event in events:
        event_date = event["start"][:10]
        if event_date in daily_counts:
            daily_counts[event_date] += 1

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "events": events,
        "availability": availability,
        "daily_counts": daily_counts,
        "total_events": len(events),
    }


def get_agenda_view(
    db: Session,
    agent_id: UUID,
    start_date: date,
    days: int = 7,
) -> Dict:
    """
    Get agenda view data (list of upcoming events).
    """
    end_date = start_date + timedelta(days=days)
    events = get_calendar_events(db, agent_id, start_date, end_date)

    # Group by date
    agenda = {}
    for event in events:
        event_date = event["start"][:10]
        if event_date not in agenda:
            agenda[event_date] = {
                "date": event_date,
                "display_date": datetime.fromisoformat(event["start"]).strftime("%A, %B %d"),
                "events": [],
            }
        agenda[event_date]["events"].append(event)

    return {
        "start_date": start_date.isoformat(),
        "days": days,
        "agenda": list(agenda.values()),
        "total_events": len(events),
    }


def get_availability_blocks(
    db: Session,
    agent_id: UUID,
    start_date: date,
    end_date: date,
) -> List[Dict]:
    """
    Get availability blocks for calendar display.
    """
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    availability = (
        db.query(AgentAvailability)
        .filter(
            AgentAvailability.agent_id == agent_id,
            AgentAvailability.start_time < end_dt,
            AgentAvailability.end_time > start_dt,
        )
        .all()
    )

    blocks = []
    for avail in availability:
        color_map = {
            "available": "#d1fae5",  # light green
            "break": "#fef3c7",      # light yellow
            "offline": "#e5e7eb",    # light gray
            "holiday": "#fce7f3",    # light pink
        }

        blocks.append({
            "id": str(avail.id),
            "start": avail.start_time.isoformat(),
            "end": avail.end_time.isoformat(),
            "color": color_map.get(avail.availability_status, "#e5e7eb"),
            "title": avail.availability_status.title(),
            "extendedProps": {
                "status": avail.availability_status,
                "notes": avail.notes,
            },
        })

    return blocks
