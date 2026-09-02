"""Booking-horizon control for the capacity engine.

Decides how many days ahead the AI may offer appointment slots:

  * pacing OFF                       -> normal multi-day horizon (unchanged)
  * pacing ON, normal               -> TODAY only (max_days=0)
  * pacing ON, future-day fallback  -> multi-day (only once today is exhausted
    AND the state's waitlist exceeds remaining same-day inventory — Phase 8)
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_HORIZON_DAYS = 14   # current/multi-day behavior
_SAME_DAY = 0                # generate_ny_anchored_slots: max_days=0 -> today only


def waitlist_depth(db: Session, tenant_id: str, state: Optional[str]) -> int:
    """How many interested leads are waiting for a slot in this state."""
    from app.models.lead import Lead
    q = db.query(Lead).filter(
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
        Lead.pacing_status == "awaiting_slot",
    )
    if state:
        q = q.filter(Lead.state == state)
    return q.count()


def future_fallback_active(db: Session, tenant_id: str, state: Optional[str]) -> bool:
    """True only when today is full AND the waitlist exceeds remaining inventory.

    Gated by FUTURE_DAY_FALLBACK_ENABLED. This is the Phase-10 rescue path; until
    both conditions hold the engine stays strictly same-day.
    """
    if not getattr(settings, "FUTURE_DAY_FALLBACK_ENABLED", True):
        return False
    try:
        from app.pacing import capacity
        open_today = capacity.slots_open_today(db, tenant_id, state)
        if open_today > 0:
            return False  # today still has room — stay same-day
        return waitlist_depth(db, tenant_id, state) > open_today  # i.e. > 0
    except Exception as exc:  # pragma: no cover
        logger.warning("future_fallback_active failed: %s", exc)
        return False


def booking_horizon_days(db: Session, lead) -> int:
    """max_days to pass to generate_ny_anchored_slots.

    Same-day pacing is applied ONLY to leads that came through the capacity
    engine — i.e. large bulk imports (>500 rows) that were HELD and released in
    waves (those carry a pacing_status). Leads that blasted normally (<=500-row
    uploads, single/API leads) have no pacing_status and always book over the
    normal multi-day window, even while the engine is enabled for big imports.
    """
    from app.core import engine_flags
    if not engine_flags.same_day_pacing_enabled():
        return _DEFAULT_HORIZON_DAYS
    # Not a paced lead -> normal multi-day booking.
    if not getattr(lead, "pacing_status", None):
        return _DEFAULT_HORIZON_DAYS
    if future_fallback_active(db, str(lead.tenant_id), getattr(lead, "state", None)):
        return _DEFAULT_HORIZON_DAYS
    return _SAME_DAY
