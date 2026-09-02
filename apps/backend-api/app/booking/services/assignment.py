"""
Weighted Round Robin Assignment (Step 6.4)

Goals:
- Fair distribution across agents
- Balanced assignment based on capacity
- Maximize agent utilization

Algorithm:
1. Get all active agents for tenant
2. Filter by availability for the requested time slot
3. Calculate weighted score based on:
   - Agent weight (higher = more appointments)
   - Current capacity usage (fewer appointments = higher priority)
   - Skill match (if applicable)
4. Select agent with highest score
"""

from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.agent_availability import AgentAvailability


def get_available_agents_for_slot(
    db: Session,
    tenant_id: str,
    start_time: datetime,
    end_time: datetime,
    required_skills: List[str] = None,
) -> List[Agent]:
    """
    Get all agents available for a specific time slot.

    Filters:
    - Active status
    - No conflicting appointments
    - Has availability window
    - Has required skills (if specified)
    """
    # Get all active agents
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        )
        .all()
    )

    available = []

    for agent in agents:
        # Check for conflicting appointments
        conflict = (
            db.query(Appointment)
            .filter(
                Appointment.agent_id == agent.id,
                Appointment.status.in_(["pending", "confirmed"]),
                Appointment.start_time < end_time,
                Appointment.end_time > start_time,
            )
            .first()
        )
        if conflict:
            continue

        # Check availability window
        availability = (
            db.query(AgentAvailability)
            .filter(
                AgentAvailability.agent_id == agent.id,
                AgentAvailability.start_time <= start_time,
                AgentAvailability.end_time >= end_time,
                AgentAvailability.availability_status == "available",
            )
            .first()
        )

        # If no availability record, assume available during business hours
        if not availability:
            hour = start_time.hour
            if 10 <= hour < 21:  # Business hours
                available.append(agent)
            continue

        # Check skills if required
        if required_skills and agent.skills:
            if not any(skill in agent.skills for skill in required_skills):
                continue

        available.append(agent)

    return available


def get_agent_workload(
    db: Session,
    agent_id: UUID,
    target_date: date,
) -> int:
    """
    Get the number of appointments an agent has on a specific date.
    """
    start_of_day = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_of_day = datetime.combine(target_date + __import__("datetime").timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    return (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time >= start_of_day,
            Appointment.start_time < end_of_day,
        )
        .count()
    )


def calculate_agent_score(
    agent: Agent,
    current_workload: int,
    daily_capacity: int,
) -> float:
    """
    Calculate assignment score for an agent.

    Higher score = higher priority for assignment.

    Factors:
    - Agent weight (0-100)
    - Capacity utilization (lower = better)
    """
    # Weight component (normalized to 0-1)
    weight_score = agent.weight / 100.0

    # Capacity component (inverse of utilization)
    if daily_capacity > 0:
        utilization = current_workload / daily_capacity
        capacity_score = 1.0 - utilization
    else:
        capacity_score = 0.0

    # Combined score (weighted average)
    score = (weight_score * 0.6) + (capacity_score * 0.4)

    return score


def assign_agent(
    db: Session,
    tenant_id: str,
    start_time: datetime,
    end_time: datetime,
    required_skills: List[str] = None,
) -> Optional[Agent]:
    """
    Assign the best available agent for a time slot.

    Uses weighted round robin algorithm.

    Returns:
        Best available Agent or None if no agents available
    """
    # Get available agents
    available = get_available_agents_for_slot(
        db=db,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        required_skills=required_skills,
    )

    if not available:
        return None

    # Calculate scores
    target_date = start_time.date()
    scored_agents = []

    for agent in available:
        workload = get_agent_workload(db, agent.id, target_date)
        score = calculate_agent_score(agent, workload, agent.daily_capacity)
        scored_agents.append((agent, score))

    # Sort by score (highest first)
    scored_agents.sort(key=lambda x: x[1], reverse=True)

    return scored_agents[0][0] if scored_agents else None


def get_agent_schedule(
    db: Session,
    agent_id: UUID,
    target_date: date,
) -> Dict:
    """
    Get an agent's schedule for a specific date.
    """
    start_of_day = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_of_day = datetime.combine(target_date + __import__("datetime").timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= start_of_day,
            Appointment.start_time < end_of_day,
            Appointment.status.in_(["pending", "confirmed", "completed"]),
        )
        .order_by(Appointment.start_time)
        .all()
    )

    return {
        "agent_id": str(agent_id),
        "date": target_date.isoformat(),
        "appointments": [
            {
                "id": str(apt.id),
                "start_time": apt.start_time.isoformat(),
                "end_time": apt.end_time.isoformat(),
                "status": apt.status,
                "lead_id": str(apt.lead_id),
            }
            for apt in appointments
        ],
        "total_appointments": len(appointments),
    }
