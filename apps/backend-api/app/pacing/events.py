"""Funnel / training event extraction for the capacity engine.

Computes, over a rolling window, the real conversion-funnel counts the engine
needs to size releases and (later) train ML models:

    contacted -> replied -> booked -> shown

All queries are read-only and defensive: any failure returns zeros so the
controller falls back to configured default rates rather than breaking.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.appointment import Appointment

logger = logging.getLogger(__name__)

# Lead statuses that imply the customer engaged back at least once.
_REPLIED_STATUSES = ("replied", "qualified", "booked", "completed", "won")
_CONTACTED_STATUSES = ("contacted",) + _REPLIED_STATUSES
# Appointment statuses that count as a real booking (exclude cancelled).
_BOOKED_STATUSES = ("confirmed", "pending", "completed", "no_show", "scheduled")
# Appointment states that count as "the lead showed up".
_SHOWN_STATUSES = ("completed",)


def funnel_counts(
    db: Session,
    tenant_id: str,
    state: Optional[str] = None,
    days: int = 21,
) -> Dict[str, int]:
    """Return {contacted, replied, booked, shown} over the last ``days``.

    When ``state`` is given the counts are restricted to leads in that state.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = {"contacted": 0, "replied": 0, "booked": 0, "shown": 0}

    try:
        lead_q = db.query(Lead).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at.is_(None),
            Lead.created_at >= since,
        )
        if state:
            lead_q = lead_q.filter(Lead.state == state)

        out["contacted"] = lead_q.filter(
            (Lead.contact_count > 0) | (Lead.status.in_(_CONTACTED_STATUSES))
        ).count()
        out["replied"] = lead_q.filter(
            (Lead.last_replied_at.isnot(None)) | (Lead.status.in_(_REPLIED_STATUSES))
        ).count()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("funnel_counts lead query failed: %s", exc)

    try:
        appt_q = (
            db.query(Appointment)
            .join(Lead, Appointment.lead_id == Lead.id)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= since,
            )
        )
        if state:
            appt_q = appt_q.filter(Lead.state == state)

        out["booked"] = appt_q.filter(Appointment.status.in_(_BOOKED_STATUSES)).count()
        out["shown"] = appt_q.filter(
            (Appointment.status.in_(_SHOWN_STATUSES)) | (Appointment.disposition == "won")
        ).count()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("funnel_counts appointment query failed: %s", exc)

    return out


def states_with_leads(
    db: Session, tenant_id: str, pacing_status: Optional[str] = None
) -> Dict[str, int]:
    """Return {state: count} of leads, optionally filtered by pacing_status.

    Used by the controller to know which states have a held pool to work.
    """
    try:
        q = db.query(Lead.state, func.count(Lead.id)).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at.is_(None),
        )
        if pacing_status:
            q = q.filter(Lead.pacing_status == pacing_status)
        q = q.group_by(Lead.state)
        return {(row[0] or ""): int(row[1]) for row in q.all()}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("states_with_leads failed: %s", exc)
        return {}
