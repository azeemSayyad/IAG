"""
Idle Gap Minimization (Step 17.5)

AI-powered appointment clustering to reduce agent idle time.

Goals:
- Cluster appointments back-to-back when possible
- Minimize gaps between appointments
- Fill idle slots with shorter appointments
- Suggest optimal booking times based on existing schedule
"""

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.appointment import Appointment
from app.models.agent import Agent
from app.models.agent_availability import AgentAvailability


def get_agent_schedule_gaps(
    db: Session,
    agent_id: UUID,
    date: datetime,
) -> List[Dict]:
    """
    Find idle gaps in an agent's schedule for a given date.

    Returns list of gaps with start_time, end_time, and duration_minutes.
    """
    # Get agent's appointments for the day
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= day_start,
            Appointment.start_time < day_end,
            Appointment.status.in_(["pending", "confirmed"]),
        )
        .order_by(Appointment.start_time)
        .all()
    )

    if not appointments:
        return []

    # Get agent availability for the day
    availability = (
        db.query(AgentAvailability)
        .filter(
            AgentAvailability.agent_id == agent_id,
            AgentAvailability.start_time < day_end,
            AgentAvailability.end_time > day_start,
            AgentAvailability.availability_status == "available",
        )
        .first()
    )

    if not availability:
        return []

    # Find gaps between appointments
    gaps = []
    current_time = max(availability.start_time, day_start)

    for appt in appointments:
        if appt.start_time > current_time:
            gap_duration = (appt.start_time - current_time).total_seconds() / 60
            if gap_duration >= 15:  # Only report gaps of 15+ minutes
                gaps.append({
                    "start_time": current_time.isoformat(),
                    "end_time": appt.start_time.isoformat(),
                    "duration_minutes": int(gap_duration),
                    "can_fit_15min": gap_duration >= 15,
                    "can_fit_30min": gap_duration >= 30,
                    "can_fit_60min": gap_duration >= 60,
                })
        current_time = max(current_time, appt.end_time)

    # Check gap after last appointment until end of availability
    if current_time < availability.end_time:
        gap_duration = (availability.end_time - current_time).total_seconds() / 60
        if gap_duration >= 15:
            gaps.append({
                "start_time": current_time.isoformat(),
                "end_time": availability.end_time.isoformat(),
                "duration_minutes": int(gap_duration),
                "can_fit_15min": gap_duration >= 15,
                "can_fit_30min": gap_duration >= 30,
                "can_fit_60min": gap_duration >= 60,
            })

    return gaps


def find_best_cluster_slot(
    db: Session,
    agent_id: UUID,
    date: datetime,
    preferred_time: Optional[datetime] = None,
) -> Optional[Dict]:
    """
    Find the best slot that minimizes idle time by clustering with existing appointments.

    Strategy:
    1. If no appointments, suggest start of business day
    2. If preferred_time given, check if it creates a gap < 15 min (bad) or clusters well
    3. Otherwise, suggest slot adjacent to existing appointments
    """
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= day_start,
            Appointment.start_time < day_end,
            Appointment.status.in_(["pending", "confirmed"]),
        )
        .order_by(Appointment.start_time)
        .all()
    )

    # Get availability
    availability = (
        db.query(AgentAvailability)
        .filter(
            AgentAvailability.agent_id == agent_id,
            AgentAvailability.start_time < day_end,
            AgentAvailability.end_time > day_start,
            AgentAvailability.availability_status == "available",
        )
        .first()
    )

    if not availability:
        return None

    business_start = max(availability.start_time, day_start)

    # If no appointments, suggest start of business day
    if not appointments:
        return {
            "start_time": business_start.isoformat(),
            "end_time": (business_start + timedelta(minutes=15)).isoformat(),
            "reason": "first_appointment_of_day",
            "idle_score": 0,
        }

    # If preferred time given, evaluate it
    if preferred_time:
        slot_end = preferred_time + timedelta(minutes=15)
        creates_bad_gap = False

        for appt in appointments:
            # Check if slot creates a small gap (< 15 min) which is wasteful
            if appt.end_time < preferred_time and (preferred_time - appt.end_time).total_seconds() / 60 < 15:
                creates_bad_gap = True
                break
            if appt.start_time > slot_end and (appt.start_time - slot_end).total_seconds() / 60 < 15:
                creates_bad_gap = True
                break

        if not creates_bad_gap:
            return {
                "start_time": preferred_time.isoformat(),
                "end_time": slot_end.isoformat(),
                "reason": "preferred_time_clean",
                "idle_score": 0,
            }

    # Find best clustering slot
    best_slot = None
    best_score = float('inf')

    for i, appt in enumerate(appointments):
        # Slot right after this appointment
        after_start = appt.end_time
        after_end = after_start + timedelta(minutes=15)

        if after_end <= availability.end_time:
            # Calculate idle score (lower is better)
            idle_score = 0

            # Check gap to next appointment
            if i + 1 < len(appointments):
                next_appt = appointments[i + 1]
                gap_to_next = (next_appt.start_time - after_end).total_seconds() / 60
                # If gap is too small to be useful, it's wasted
                if 0 < gap_to_next < 15:
                    idle_score += 100  # Penalty for creating unusable gap

            if idle_score < best_score:
                best_score = idle_score
                best_slot = {
                    "start_time": after_start.isoformat(),
                    "end_time": after_end.isoformat(),
                    "reason": "cluster_after_appointment",
                    "idle_score": idle_score,
                }

        # Slot right before this appointment
        before_end = appt.start_time
        before_start = before_end - timedelta(minutes=15)

        if before_start >= business_start:
            idle_score = 0

            # Check gap from previous appointment
            if i > 0:
                prev_appt = appointments[i - 1]
                gap_from_prev = (before_start - prev_appt.end_time).total_seconds() / 60
                if 0 < gap_from_prev < 15:
                    idle_score += 100

            if idle_score < best_score:
                best_score = idle_score
                best_slot = {
                    "start_time": before_start.isoformat(),
                    "end_time": before_end.isoformat(),
                    "reason": "cluster_before_appointment",
                    "idle_score": idle_score,
                }

    return best_slot


def calculate_utilization_score(
    appointments: List[Dict],
    availability_start: datetime,
    availability_end: datetime,
) -> float:
    """
    Calculate utilization score for an agent's schedule.

    Returns 0-100 percentage of available time that is booked.
    """
    if not appointments:
        return 0.0

    total_available = (availability_end - availability_start).total_seconds() / 60
    if total_available <= 0:
        return 0.0

    total_booked = sum(
        (datetime.fromisoformat(a["end_time"]) - datetime.fromisoformat(a["start_time"])).total_seconds() / 60
        for a in appointments
    )

    return min(100.0, (total_booked / total_available) * 100)


def suggest_slot_for_lead(
    db: Session,
    tenant_id: UUID,
    preferred_time: Optional[datetime] = None,
) -> Optional[Dict]:
    """
    Suggest the best slot across all agents that minimizes idle time.

    Returns the slot with the best clustering score.
    """
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        )
        .all()
    )

    if not agents:
        return None

    date = preferred_time or datetime.now(timezone.utc) + timedelta(days=1)
    best_slot = None
    best_score = float('inf')

    for agent in agents:
        slot = find_best_cluster_slot(
            db=db,
            agent_id=agent.id,
            date=date,
            preferred_time=preferred_time,
        )

        if slot and slot.get("idle_score", 0) < best_score:
            best_score = slot["idle_score"]
            best_slot = {**slot, "agent_id": str(agent.id)}

    return best_slot
