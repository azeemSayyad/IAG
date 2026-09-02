"""
System Worker Tasks

System maintenance and background operations.
"""

import logging
from typing import Dict

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.system.process_delayed_jobs",
    bind=True,
)
def process_delayed_jobs(self) -> Dict:
    """
    Process delayed jobs that are ready to execute.

    Called every minute by Celery Beat.
    """
    try:
        from app.core.queues import queue_manager
        processed = queue_manager.process_delayed_jobs()
        return {"success": True, "processed": processed}

    except Exception as exc:
        logger.error(f"Delayed job processing failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.system.cleanup_expired_locks",
    bind=True,
)
def cleanup_expired_locks(self) -> Dict:
    """
    Clean up expired Redis locks.

    Called every 15 minutes by Celery Beat.
    """
    try:
        from app.core.redis import RedisService
        from app.booking.services.locking import cleanup_expired_locks as cleanup

        redis = RedisService()
        cleaned = cleanup(redis)

        return {"success": True, "cleaned": cleaned}

    except Exception as exc:
        logger.error(f"Lock cleanup failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.system.health_check",
    bind=True,
)
def health_check(self) -> Dict:
    """
    Worker health check.

    Reports worker status and queue sizes.
    """
    try:
        from app.core.queues import queue_manager
        from app.core.redis import RedisService

        queue_stats = queue_manager.get_all_queue_stats()

        redis = RedisService()
        redis_info = redis.client.info("memory")

        return {
            "success": True,
            "queues": queue_stats,
            "memory": {
                "used": redis_info.get("used_memory_human"),
                "peak": redis_info.get("used_memory_peak_human"),
            },
        }

    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.system.run_emergency_fill",
    bind=True,
)
def run_emergency_fill(self) -> Dict:
    """
    Run emergency fill cycle to fill idle agent slots.

    Called every 5 minutes by Celery Beat.
    Detects idle agents, finds warm leads, sends SMS blasts.
    """
    try:
        from app.core.database import SessionLocal
        from app.booking.services.emergency_fill import EmergencyFillEngine

        db = SessionLocal()
        try:
            engine = EmergencyFillEngine(db)

            # Get all active tenants
            from app.models.tenant import Tenant
            tenants = db.query(Tenant).filter(Tenant.status == "active").all()

            results = []
            for tenant in tenants:
                try:
                    cycle_result = engine.run_emergency_fill_cycle(tenant.id)
                    results.append({
                        "tenant_id": str(tenant.id),
                        "result": cycle_result,
                    })
                except Exception as e:
                    logger.error(f"Emergency fill failed for tenant {tenant.id}: {e}")
                    results.append({
                        "tenant_id": str(tenant.id),
                        "error": str(e),
                    })

            return {"success": True, "tenants_processed": len(results), "results": results}
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Emergency fill failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.system.check_presence_timeouts",
    bind=True,
)
def check_presence_timeouts(self) -> Dict:
    """
    Check for agent presence timeouts.

    Called every 30 seconds by Celery Beat.
    Transitions: online->away (30s), away->offline (5min), busy->online (1hr).
    """
    try:
        from app.realtime.presence import PresenceManager

        presence = PresenceManager()

        # Get all active tenants
        from app.core.database import SessionLocal
        from app.models.tenant import Tenant

        db = SessionLocal()
        try:
            tenants = db.query(Tenant).filter(Tenant.status == "active").all()

            results = []
            for tenant in tenants:
                try:
                    changes = presence.check_timeouts(tenant.id)
                    if changes:
                        results.extend(changes)
                except Exception as e:
                    logger.error(f"Presence timeout check failed for tenant {tenant.id}: {e}")

            return {"success": True, "changes": len(results), "details": results}
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Presence timeout check failed: {exc}")
        return {"success": False, "error": str(exc)}
