"""Live agent-availability capacity — the demand signal for capacity-sized pacing.

Reads how many licensed agents are FREE right now per state from the SMS-queue
agent status:
  - AVAILABLE  -> idle / free (ready for a lead)
  - ON_CALL    -> accepted a lead -> BUSY. We have no telephony, so "accepted a
                  lead" IS the busy signal (queue_service.accept sets ON_CALL).
Cross-referenced with the compliance license matrix (booking_agents_for_state) so
an agent only counts toward states they hold an ACTIVE, non-expired license in.

This is the demand input for capacity-sized pacing (release.drip_cycle): drip just
enough fresh leads to keep free agents busy, throttle down as they fill up, and
send NOTHING into a state with no licensed agent (hard compliance gate).

Pure helpers (count_free_licensed / total_demand / gate_states) are split out from
the DB shell so the math unit-tests without a database.
"""
from typing import Dict, List, Set

from sqlalchemy.orm import Session

# SMS-queue agent statuses (see app/models/sms.py SmsQueueAgent.status).
FREE_STATUSES = ("AVAILABLE",)
BUSY_STATUSES = ("ON_CALL",)


def default_states() -> List[str]:
    """The states the engine paces, from settings (CAPACITY_STATES), default NC/SC/FL/TX/GA."""
    from app.core.config import settings
    raw = getattr(settings, "CAPACITY_STATES", "NC,SC,FL,TX,GA")
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]


# ---------------------------------------------------------------------------
# PURE helpers (no DB) — unit-tested directly.
# ---------------------------------------------------------------------------
def count_free_licensed(free_user_ids: Set[str], licensed_by_state: Dict[str, list]) -> Dict[str, int]:
    """Given the set of FREE agent user_ids and {state: [licensed Agent, ...]},
    count how many licensed agents in each state are also free. A multi-state free
    agent counts toward each of their licensed states (self-corrects next tick once
    they accept a lead and flip to busy)."""
    out: Dict[str, int] = {}
    for state, agents in licensed_by_state.items():
        out[state] = sum(1 for a in agents if str(getattr(a, "user_id", "")) in free_user_ids)
    return out


def total_demand(free_by_state: Dict[str, int], buffer: float) -> int:
    """Per-tick release ceiling = total free agents * buffer. Keeps a small reserve
    of fresh leads per free agent without bursting past what they can absorb."""
    return int(round(sum(free_by_state.values()) * max(0.0, buffer)))


def gate_states(licensed_by_state: Dict[str, list]) -> Set[str]:
    """States with >=1 active-licensed agent. A state absent from this set gets
    NOTHING released into it (hard compliance gate, P3)."""
    return {s for s, agents in licensed_by_state.items() if len(agents) > 0}


# ---------------------------------------------------------------------------
# DB shell — thin wrappers that compose the queries, then defer to the pure helpers.
# ---------------------------------------------------------------------------
def _free_user_ids(db: Session, tenant_id: str) -> Set[str]:
    from app.models.sms import SmsQueueAgent
    rows = (
        db.query(SmsQueueAgent)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.status.in_(FREE_STATUSES),
            SmsQueueAgent.current_lead_id.is_(None),
        )
        .all()
    )
    return {str(r.user_id) for r in rows}


def _licensed_by_state(db: Session, tenant_id: str, states: List[str]) -> Dict[str, list]:
    from app.leads.services.distribution import booking_agents_for_state
    return {s: booking_agents_for_state(db, tenant_id, s) for s in states}


def free_licensed_by_state(db: Session, tenant_id: str, states: List[str] = None) -> Dict[str, int]:
    states = states or default_states()
    return count_free_licensed(_free_user_ids(db, tenant_id), _licensed_by_state(db, tenant_id, states))


def states_with_capacity(db: Session, tenant_id: str, states: List[str] = None) -> Set[str]:
    """The hard compliance gate: states that currently have >=1 active-licensed agent."""
    states = states or default_states()
    return gate_states(_licensed_by_state(db, tenant_id, states))


def release_ceiling(db: Session, tenant_id: str, states: List[str] = None) -> int:
    """How many fresh leads the free agents can absorb this tick (0 -> pause)."""
    from app.core.config import settings
    states = states or default_states()
    buf = float(getattr(settings, "CAPACITY_BUFFER", 1.5))
    return total_demand(free_licensed_by_state(db, tenant_id, states), buf)
