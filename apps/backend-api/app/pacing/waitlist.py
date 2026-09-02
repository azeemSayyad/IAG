"""Waitlist + cancellation refill.

An interested lead with no open same-day slot is parked on the waitlist
(pacing_status='awaiting_slot') with its conversation preserved — never dropped.
When capacity frees up (a cancellation/no-show, or simply the next cycle), the
highest-priority waitlisted leads are re-engaged before any untouched lead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.pacing import capacity, scoring

logger = logging.getLogger(__name__)


def add_to_waitlist(db: Session, lead: Lead) -> None:
    """Park an interested-but-unslotted lead (idempotent)."""
    lead.pacing_status = "awaiting_slot"
    try:
        lead.ai_status = "awaiting_slot"
    except Exception:
        pass
    db.commit()
    logger.info("[pacing] lead %s waitlisted (awaiting_slot) state=%s", lead.id, lead.state)


def mark_booked(db: Session, lead: Lead) -> None:
    """Flag a lead as booked so it leaves the held/waitlist pools."""
    try:
        lead.pacing_status = "booked"
        db.commit()
    except Exception:
        db.rollback()


def waitlisted(db: Session, tenant_id: str, state: Optional[str]):
    q = db.query(Lead).filter(
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
        Lead.pacing_status == "awaiting_slot",
    )
    if state:
        q = q.filter(Lead.state == state)
    return q


def process_waitlist(
    db: Session,
    tenant_id: str,
    state: Optional[str],
    dry_run: bool = False,
) -> int:
    """Re-engage up to `slots_open` top waitlisted leads in a state.

    Re-queues their outreach so the AI re-offers the now-open slot, and moves them
    back in-flight ('released'). Returns the number re-engaged.
    """
    open_slots = capacity.slots_open_today(db, tenant_id, state)
    if open_slots <= 0:
        return 0
    candidates = waitlisted(db, tenant_id, state).order_by(
        Lead.priority_score.desc().nullslast()
    ).limit(max(open_slots * 4, open_slots + 20)).all()
    if not candidates:
        return 0
    now = datetime.now(timezone.utc)
    candidates.sort(key=lambda l: float(l.priority_score or 0) + scoring.aging_bonus(l, now), reverse=True)
    chosen = candidates[:open_slots]

    if dry_run:
        return len(chosen)

    from app.pacing.release import _enqueue_lead, _wave_id
    wave = _wave_id()
    for lead in chosen:
        _enqueue_lead(lead, wave)
        lead.pacing_status = "released"
        lead.released_at = now
        lead.wave_id = wave
    db.commit()
    logger.info("[pacing] refilled %s waitlisted leads in state=%s", len(chosen), state)
    return len(chosen)


def refill_tenant(db: Session, tenant_id: str, dry_run: bool = False) -> int:
    """Work the waitlist across all states for a tenant (cancellation refill)."""
    from app.core import engine_flags
    if not engine_flags.same_day_pacing_enabled():
        return 0
    from app.pacing import events
    states = events.states_with_leads(db, tenant_id, pacing_status="awaiting_slot")
    total = 0
    for state in states:
        try:
            total += process_waitlist(db, tenant_id, state or None, dry_run=dry_run)
        except Exception as exc:  # pragma: no cover
            db.rollback()
            logger.warning("refill_tenant state %s failed: %s", state, exc)
    return total
