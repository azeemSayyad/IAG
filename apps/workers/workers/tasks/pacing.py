"""Capacity engine controller task.

Runs every PACING_CYCLE_MINUTES (Celery beat). For each active tenant it runs one
controller cycle: recompute per-state capacity and release just enough top held
leads to keep that day's calendars filling. Entirely inert unless
SAME_DAY_PACING_ENABLED is on (run_cycle short-circuits).
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="workers.tasks.pacing.pacing_tick")
def pacing_tick(self) -> Dict:
    from app.core.config import settings
    if not getattr(settings, "SAME_DAY_PACING_ENABLED", False):
        return {"enabled": False, "tenants": 0}

    from app.core.database import get_db
    from app.models.tenant import Tenant
    from app.pacing import release

    db = next(get_db())
    processed = 0
    total_release = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        for tenant in tenants:
            try:
                rep = release.run_cycle(db, str(tenant.id))
                total_release += rep.get("total_release", 0) or 0
                processed += 1
            except Exception as exc:  # pragma: no cover
                db.rollback()
                logger.warning("pacing_tick tenant %s failed: %s", tenant.id, exc)
    finally:
        db.close()
    return {"enabled": True, "tenants": processed, "total_release": total_release}


@celery_app.task(bind=True, name="workers.tasks.pacing.drip_tick")
def drip_tick(self) -> Dict:
    """Queue-Only Mode drip controller. Runs frequently; for each active tenant it
    releases a running CSV campaign's held leads at the campaign's rate. drip_cycle()
    self-throttles to the per-lead interval. No SAME_DAY_PACING_ENABLED gate here —
    drip_cycle() runs the per-campaign drip regardless (the basic campaign feature)
    and gates only the same-day-pacing ENGINE path internally, so a running campaign
    always paces while the pacing engine stays dormant until its flag is on."""
    from app.core.database import get_db
    from app.models.tenant import Tenant
    from app.pacing import release

    db = next(get_db())
    processed = 0
    total_release = 0
    try:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        for tenant in tenants:
            try:
                rep = release.drip_cycle(db, str(tenant.id))
                total_release += rep.get("released", 0) or 0
                processed += 1
            except Exception as exc:  # pragma: no cover
                db.rollback()
                logger.warning("drip_tick tenant %s failed: %s", tenant.id, exc)
    finally:
        db.close()
    return {"enabled": True, "tenants": processed, "total_release": total_release}
