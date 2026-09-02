"""
Lead distribution + compliance-aware assignment.

The AI auto-distributes each new lead to an eligible agent:
  * eligible  = agent is active AND (the lead has no state, OR the agent holds
                an ACTIVE, non-expired state license for the lead's state).
  * available = the agent still has open capacity today (assigned active leads
                below daily_capacity).
Among eligible+available agents the lead is given to the least-loaded one
(ties broken randomly) so volume spreads evenly.

Head manager / admin can override via reassign endpoints; the same compliance
rule is enforced there so a lead is never handed to an unlicensed agent.
"""
from datetime import date
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.lead import Lead
from app.models.compliance import AgentStateLicense


def agent_licensed_for_state(db: Session, agent_id, tenant_id, state_code: Optional[str]) -> bool:
    """True if the agent may legally sell to a lead in ``state_code``.

    No state on the lead → unrestricted (cannot determine a licensing barrier).
    """
    if not state_code:
        return True
    today = date.today()
    q = (
        db.query(AgentStateLicense)
        .filter(
            AgentStateLicense.tenant_id == tenant_id,
            AgentStateLicense.agent_id == agent_id,
            func.upper(AgentStateLicense.state_code) == state_code.upper(),
            AgentStateLicense.status == "ACTIVE",
        )
    )
    for lic in q.all():
        if lic.expiration_date is None or lic.expiration_date >= today:
            return True
    return False


def agent_can_handle_lead(db: Session, agent: Agent, lead: Lead) -> bool:
    if getattr(agent, "status", None) != "active":
        return False
    return agent_licensed_for_state(db, agent.id, agent.tenant_id, getattr(lead, "state", None))


def _assigned_active_count(db: Session, agent_id, tenant_id) -> int:
    return (
        db.query(func.count(Lead.id))
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.assigned_agent_id == agent_id,
            Lead.deleted_at.is_(None),
            Lead.lifecycle_stage.notin_(["completed", "unqualified"]),
        )
        .scalar()
        or 0
    )


def booking_agents_for_state(db: Session, tenant_id, state_code: Optional[str]) -> List[Agent]:
    """All ACTIVE agents who hold an ACTIVE, non-expired license for ``state_code``.

    Used to build the pool of agents whose availability may be offered to a lead
    in that state. Returns [] when ``state_code`` is empty (the caller decides the
    no-state fallback). Capacity is NOT enforced here — slot availability already
    reflects each agent's booked times.
    """
    if not state_code:
        return []
    agents = (
        db.query(Agent)
        .filter(Agent.tenant_id == tenant_id, Agent.status == "active")
        .all()
    )
    return [a for a in agents if agent_licensed_for_state(db, a.id, tenant_id, state_code)]


def eligible_agents_for_lead(db: Session, lead: Lead, respect_capacity: bool = True) -> List[Agent]:
    """Active, compliance-eligible agents (optionally with open capacity)."""
    agents = (
        db.query(Agent)
        .filter(Agent.tenant_id == lead.tenant_id, Agent.status == "active")
        .all()
    )
    out = []
    for a in agents:
        if not agent_can_handle_lead(db, a, lead):
            continue
        if respect_capacity:
            load = _assigned_active_count(db, a.id, lead.tenant_id)
            if load >= (a.daily_capacity or 8):
                continue
        out.append(a)
    return out


def auto_assign_lead(db: Session, lead: Lead, commit: bool = True) -> Optional[Agent]:
    """Assign ``lead`` to the least-loaded eligible agent. Returns the agent.

    Falls back to ignoring capacity if every eligible agent is at capacity, so a
    compliant lead is never left unassigned just because the team is busy. If no
    agent is licensed for the lead's state, the lead stays unassigned (a human
    must add licensing) — this is the compliance guarantee.
    """
    import random

    candidates = eligible_agents_for_lead(db, lead, respect_capacity=True)
    if not candidates:
        # Everyone at capacity? retry ignoring capacity (still compliance-gated).
        candidates = eligible_agents_for_lead(db, lead, respect_capacity=False)
    if not candidates:
        return None

    # Least-loaded first; shuffle so equal-load agents are picked fairly.
    random.shuffle(candidates)
    candidates.sort(key=lambda a: _assigned_active_count(db, a.id, lead.tenant_id))
    chosen = candidates[0]

    lead.assigned_agent_id = chosen.id
    if commit:
        db.commit()
    return chosen
