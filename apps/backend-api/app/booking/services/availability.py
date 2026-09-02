"""
Availability Engine (Step 6.2)

Tracks:
- Agent shifts (working hours)
- Breaks
- Holidays
- Booked slots
"""

from datetime import datetime, date, timedelta, time, timezone
from typing import List, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_availability import AgentAvailability
from app.models.appointment import Appointment
from app.booking.services.slots import TimeSlot, generate_slots_for_date


def get_agent_availability(
    db: Session,
    agent_id: UUID,
    target_date: date,
) -> List[AgentAvailability]:
    """
    Get an agent's availability records for a specific date.
    """
    start_of_day = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end_of_day = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    return (
        db.query(AgentAvailability)
        .filter(
            AgentAvailability.agent_id == agent_id,
            AgentAvailability.start_time < end_of_day,
            AgentAvailability.end_time > start_of_day,
        )
        .all()
    )


def get_agent_booked_slots(
    db: Session,
    agent_id: UUID,
    target_date: date,
) -> List[Tuple[datetime, datetime]]:
    """
    Get an agent's booked appointment slots for a specific date.
    """
    start_of_day = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end_of_day = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time < end_of_day,
            Appointment.end_time > start_of_day,
        )
        .all()
    )

    return [(apt.start_time, apt.end_time) for apt in appointments]


def get_available_slots_for_agent(
    db: Session,
    agent_id: UUID,
    target_date: date,
    timezone_offset: int = 0,
) -> List[TimeSlot]:
    """
    Get available slots for a specific agent on a specific date.

    Takes into account:
    - Agent's working hours (availability records)
    - Existing bookings
    - Breaks
    """
    # Get all possible slots for the date
    all_slots = generate_slots_for_date(target_date, timezone_offset)

    # Get agent's availability records
    availability_records = get_agent_availability(db, agent_id, target_date)

    # Get booked slots
    booked_slots = get_agent_booked_slots(db, agent_id, target_date)

    # Filter based on availability
    available_slots = []

    for slot in all_slots:
        # Check if slot falls within any availability window
        is_within_availability = False

        if not availability_records:
            # If no availability records, assume standard business hours
            is_within_availability = True
        else:
            for avail in availability_records:
                if avail.availability_status == "available":
                    if slot.start_time >= avail.start_time and slot.end_time <= avail.end_time:
                        is_within_availability = True
                        break
                elif avail.availability_status == "break":
                    # During break, slot is not available
                    if slot.start_time >= avail.start_time and slot.end_time <= avail.end_time:
                        is_within_availability = False
                        break

        if not is_within_availability:
            continue

        # Check if slot overlaps with any booked slot
        is_booked = False
        for booked_start, booked_end in booked_slots:
            if slot.start_time < booked_end and slot.end_time > booked_start:
                is_booked = True
                break

        if not is_booked:
            slot.is_available = True
            slot.agent_id = str(agent_id)
            available_slots.append(slot)

    return available_slots


def get_available_slots_all_agents(
    db: Session,
    tenant_id: str,
    target_date: date,
    timezone_offset: int = 0,
) -> Dict[str, List[TimeSlot]]:
    """
    Get available slots for all active agents in a tenant.
    """
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        )
        .all()
    )

    result = {}
    for agent in agents:
        slots = get_available_slots_for_agent(db, agent.id, target_date, timezone_offset)
        if slots:
            result[str(agent.id)] = slots

    return result


def get_merged_available_slots(
    db: Session,
    tenant_id: str,
    target_date: date,
    timezone_offset: int = 0,
) -> List[TimeSlot]:
    """
    Get merged available slots across all agents.
    Returns slots with agent assignments.
    """
    agent_slots = get_available_slots_all_agents(db, tenant_id, target_date, timezone_offset)

    # Merge and deduplicate by time
    time_to_agents = {}

    for agent_id, slots in agent_slots.items():
        for slot in slots:
            key = slot.key
            if key not in time_to_agents:
                time_to_agents[key] = {
                    "slot": slot,
                    "agents": [],
                }
            time_to_agents[key]["agents"].append(agent_id)

    # Return slots sorted by time
    merged = []
    for key in sorted(time_to_agents.keys()):
        entry = time_to_agents[key]
        slot = entry["slot"]
        # Store available agents in the slot
        slot.agent_id = entry["agents"][0]  # Default to first agent
        merged.append(slot)

    return merged


def set_agent_availability(
    db: Session,
    agent_id: UUID,
    tenant_id: str,
    start_time: datetime,
    end_time: datetime,
    status: str = "available",
    notes: str = None,
) -> AgentAvailability:
    """
    Set an agent's availability for a time period.
    """
    availability = AgentAvailability(
        agent_id=agent_id,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        availability_status=status,
        notes=notes,
    )
    db.add(availability)
    db.commit()
    return availability


def set_agent_break(
    db: Session,
    agent_id: UUID,
    tenant_id: str,
    break_start: datetime,
    break_end: datetime,
) -> AgentAvailability:
    """
    Set a break period for an agent.
    """
    return set_agent_availability(
        db=db,
        agent_id=agent_id,
        tenant_id=tenant_id,
        start_time=break_start,
        end_time=break_end,
        status="break",
        notes="Break time",
    )


def set_holiday(
    db: Session,
    agent_id: UUID,
    tenant_id: str,
    holiday_date: date,
) -> AgentAvailability:
    """
    Mark a date as a holiday for an agent.
    """
    start = datetime.combine(holiday_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(holiday_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    return set_agent_availability(
        db=db,
        agent_id=agent_id,
        tenant_id=tenant_id,
        start_time=start,
        end_time=end,
        status="holiday",
        notes="Holiday",
    )
