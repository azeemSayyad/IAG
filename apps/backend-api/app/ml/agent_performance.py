"""
Agent Performance ML (Step 11.3)

Predicts:
- Best agent for a lead
- Agent performance trends
- Optimal agent scheduling
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.user import User


def calculate_agent_metrics(
    db: Session,
    agent_id: UUID,
    days: int = 90,
) -> Dict:
    """
    Calculate comprehensive metrics for an agent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.agent_id == agent_id,
            Appointment.created_at >= cutoff,
        )
        .all()
    )

    total = len(appointments)
    completed = sum(1 for a in appointments if a.status == "completed")
    no_show = sum(1 for a in appointments if a.status == "no_show")
    cancelled = sum(1 for a in appointments if a.status == "cancelled")
    won = sum(1 for a in appointments if a.disposition == "won")
    lost = sum(1 for a in appointments if a.disposition == "lost")
    follow_up = sum(1 for a in appointments if a.disposition == "follow_up")

    # Calculate rates
    completion_rate = round(completed / total * 100, 1) if total > 0 else 0
    win_rate = round(won / completed * 100, 1) if completed > 0 else 0
    no_show_rate = round(no_show / total * 100, 1) if total > 0 else 0

    # Calculate average call duration
    durations = [
        a.call_duration_seconds
        for a in appointments
        if a.call_duration_seconds and a.status == "completed"
    ]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    # Calculate utilization
    total_minutes = sum(
        (a.end_time - a.start_time).total_seconds() / 60
        for a in appointments
        if a.status in ("confirmed", "completed")
    )
    available_minutes = days * 660  # 11 hours per day
    utilization = round(total_minutes / available_minutes * 100, 1) if available_minutes > 0 else 0

    return {
        "agent_id": str(agent_id),
        "period_days": days,
        "total_appointments": total,
        "completed": completed,
        "no_show": no_show,
        "cancelled": cancelled,
        "won": won,
        "lost": lost,
        "follow_up": follow_up,
        "completion_rate": completion_rate,
        "win_rate": win_rate,
        "no_show_rate": no_show_rate,
        "avg_call_duration": avg_duration,
        "utilization_pct": utilization,
    }


def rank_agents(
    db: Session,
    tenant_id: str,
    days: int = 90,
) -> List[Dict]:
    """
    Rank agents by performance.
    """
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        )
        .all()
    )

    rankings = []
    for agent in agents:
        metrics = calculate_agent_metrics(db, agent.id, days)

        # Calculate composite score
        score = (
            metrics["win_rate"] * 0.4 +
            metrics["completion_rate"] * 0.3 +
            (100 - metrics["no_show_rate"]) * 0.2 +
            metrics["utilization_pct"] * 0.1
        )

        user = agent.user
        rankings.append({
            "agent_id": str(agent.id),
            "name": f"{user.first_name} {user.last_name}" if user else "Unknown",
            "score": round(score, 1),
            "metrics": metrics,
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)

    # Add rank
    for i, rank in enumerate(rankings):
        rank["rank"] = i + 1

    return rankings


def find_best_agent_for_lead(
    db: Session,
    lead: Lead,
) -> Optional[Dict]:
    """
    Find the best agent for a specific lead based on:
    - Agent performance
    - Lead source experience
    - Lead state experience
    """
    agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == lead.tenant_id,
            Agent.status == "active",
        )
        .all()
    )

    if not agents:
        return None

    scored_agents = []

    for agent in agents:
        metrics = calculate_agent_metrics(db, agent.id)

        # Base score from performance
        score = (
            metrics["win_rate"] * 0.5 +
            metrics["completion_rate"] * 0.3 +
            (100 - metrics["no_show_rate"]) * 0.2
        )

        # Bonus for experience with lead source
        source_appointments = (
            db.query(Appointment)
            .join(Lead)
            .filter(
                Appointment.agent_id == agent.id,
                Lead.source == lead.source,
            )
            .count()
        )
        if source_appointments > 5:
            score += 5

        # Bonus for experience with lead state
        if lead.state:
            state_appointments = (
                db.query(Appointment)
                .join(Lead)
                .filter(
                    Appointment.agent_id == agent.id,
                    Lead.state == lead.state,
                )
                .count()
            )
            if state_appointments > 3:
                score += 3

        user = agent.user
        scored_agents.append({
            "agent_id": str(agent.id),
            "name": f"{user.first_name} {user.last_name}" if user else "Unknown",
            "score": round(score, 1),
            "metrics": metrics,
        })

    scored_agents.sort(key=lambda x: x["score"], reverse=True)
    return scored_agents[0] if scored_agents else None


def get_agent_trends(
    db: Session,
    agent_id: UUID,
    weeks: int = 12,
) -> List[Dict]:
    """
    Get agent performance trends over time.
    """
    trends = []
    today = date.today()

    for i in range(weeks):
        week_start = today - timedelta(weeks=weeks - i)
        week_end = week_start + timedelta(days=7)

        start_dt = datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(week_end, datetime.min.time(), tzinfo=timezone.utc)

        appointments = (
            db.query(Appointment)
            .filter(
                Appointment.agent_id == agent_id,
                Appointment.created_at >= start_dt,
                Appointment.created_at < end_dt,
            )
            .all()
        )

        total = len(appointments)
        completed = sum(1 for a in appointments if a.status == "completed")
        won = sum(1 for a in appointments if a.disposition == "won")

        trends.append({
            "week_start": week_start.isoformat(),
            "total": total,
            "completed": completed,
            "won": won,
            "win_rate": round(won / completed * 100, 1) if completed > 0 else 0,
        })

    return trends
