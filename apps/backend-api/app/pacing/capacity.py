"""Per-state same-day appointment capacity.

"How many more appointments can be booked TODAY?" — computed per state from the
agents licensed in that state. An agent's same-day capacity is
``daily_capacity - appointments already booked today`` (matching how the booking
flow actually fills calendars; it does not require availability records).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.leads.services.distribution import booking_agents_for_state
from app.core.config import settings

logger = logging.getLogger(__name__)

# Appointment statuses that occupy a slot (everything except cancelled).
_ACTIVE_APPT_STATUSES = ("confirmed", "pending", "completed", "no_show", "scheduled")


def _agent_tz():
    name = getattr(settings, "AGENT_TZ", None) or "America/New_York"
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def _today_bounds_utc():
    """[start, end) UTC datetimes spanning 'today' in the agent timezone."""
    tz = _agent_tz()
    now_local = datetime.now(tz) if tz else datetime.utcnow()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    if tz:
        return start_local.astimezone(ZoneInfo("UTC")), end_local.astimezone(ZoneInfo("UTC"))
    return start_local, end_local


def agent_booked_today(db: Session, tenant_id: str, agent_id) -> int:
    start, end = _today_bounds_utc()
    try:
        return (
            db.query(Appointment)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.agent_id == agent_id,
                Appointment.status.in_(_ACTIVE_APPT_STATUSES),
                Appointment.start_time >= start,
                Appointment.start_time < end,
            )
            .count()
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("agent_booked_today failed: %s", exc)
        return 0


def agent_open_slots_today(db: Session, tenant_id: str, agent: Agent) -> int:
    cap = int(getattr(agent, "daily_capacity", 0) or 0)
    booked = agent_booked_today(db, tenant_id, agent.id)
    return max(0, cap - booked)


def licensed_agents(db: Session, tenant_id: str, state: Optional[str]) -> List[Agent]:
    """Agents who may take a booking for ``state``.

    When a lead has a state, only agents licensed there. When there is no state,
    fall back to all active agents (today's behavior for state-less leads).
    """
    if state:
        return booking_agents_for_state(db, tenant_id, state)
    try:
        return db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.status == "active").all()
    except Exception:
        return []


def slots_open_today(db: Session, tenant_id: str, state: Optional[str]) -> int:
    """Total same-day open appointment slots for a state (sum over licensed agents)."""
    return sum(agent_open_slots_today(db, tenant_id, a) for a in licensed_agents(db, tenant_id, state))


def capacity_today(db: Session, tenant_id: str, state: Optional[str]) -> Dict:
    """Full capacity snapshot for a state: agents, total slots, booked, open."""
    agents = licensed_agents(db, tenant_id, state)
    total = sum(int(getattr(a, "daily_capacity", 0) or 0) for a in agents)
    booked = sum(agent_booked_today(db, tenant_id, a.id) for a in agents)
    open_slots = max(0, total - booked)
    return {
        "state": state,
        "licensed_agents": len(agents),
        "slots_total": total,
        "booked": booked,
        "slots_open": open_slots,
        "fill_pct": round(booked / total * 100, 1) if total else 0.0,
    }


def slots_by_state(db: Session, tenant_id: str, states: List[str]) -> Dict[str, int]:
    return {s: slots_open_today(db, tenant_id, s) for s in states}
