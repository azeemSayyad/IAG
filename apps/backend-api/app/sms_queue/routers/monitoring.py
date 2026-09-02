"""SMS Monitoring — read-only system-health endpoints.

Mounted under /api/v1/sms/monitoring. Manager/admin only.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_role
from app.models.user import User
from app.sms_queue.services import monitoring_service

router = APIRouter(prefix="/sms/monitoring", tags=["sms-monitoring"])

# SMS Monitoring is dev-only. (require_role always lets "dev" through, so this
# restricts the endpoints to the dev role exclusively.)
_require_monitor = require_role("dev")


@router.get("/stats")
def sms_stats(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_monitor),
) -> dict:
    return monitoring_service.get_stats(db, tenant_id)


@router.get("/time-series")
def sms_time_series(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_monitor),
) -> dict:
    return monitoring_service.get_time_series(db, tenant_id)


@router.get("/recent-failures")
def sms_recent_failures(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_monitor),
) -> dict:
    return monitoring_service.get_recent_failures(db, tenant_id, limit)


@router.get("/pulse-events")
def sms_pulse_events(
    minutes: int = Query(10, ge=1, le=60),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_monitor),
) -> dict:
    return monitoring_service.get_pulse_events(db, tenant_id, minutes)
