"""Appointment Capacity Engine — read API for the live dashboard.

Kept in its own router (not admin.py) so the capacity engine stays self-contained.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.user import User
from app.pacing import metrics, release

router = APIRouter(prefix="/pacing", tags=["pacing"])


@router.get("/metrics")
def pacing_metrics(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Live per-state capacity metrics (fill %, in-flight, waitlist, waste)."""
    return metrics.snapshot(db, str(tenant_id))


@router.get("/plan")
def pacing_plan(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """What the controller WOULD release right now per state (no side effects)."""
    from app.pacing import events
    states = set(events.states_with_leads(db, str(tenant_id), "held")) | set(
        events.states_with_leads(db, str(tenant_id), "awaiting_slot")
    )
    return {"states": [release.compute(db, str(tenant_id), s or None) for s in sorted(states)]}
