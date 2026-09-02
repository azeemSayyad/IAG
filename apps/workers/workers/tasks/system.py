"""
System Worker Tasks

System maintenance and background operations.
"""

import logging
import asyncio
from typing import Dict

from workers.celery_app import celery_app

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

            # Capacity engine: refill cancelled/no-show slots from the waitlist
            # before the emergency-fill blast (interested leads come first).
            pacing_refill = 0
            try:
                from app.core.config import settings as _s
                if getattr(_s, "SAME_DAY_PACING_ENABLED", False):
                    from app.pacing.waitlist import refill_tenant
                    for tenant in tenants:
                        try:
                            pacing_refill += refill_tenant(db, str(tenant.id))
                        except Exception:
                            db.rollback()
            except Exception:
                pass

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


@celery_app.task(
    name="workers.tasks.system.scan_compliance_expirations",
    bind=True,
)
def scan_compliance_expirations(self) -> Dict:
    """Scan carrier appointments for 60-day, 30-day, and expired compliance events."""
    try:
        from app.compliance import services
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            result = services.scan_appointment_expirations(db)
            for event in result.get("events", []):
                asyncio.run(services.emit_compliance_notification(event["tenant_id"], event["emit_name"], {
                    **event,
                    "notification_type": event["event_type"],
                    "title": "Appointment expired" if event["emit_name"] == "appointment_expired" else "Appointment expiring",
                }))
            return result
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"Compliance expiration scan failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.system.scan_compliance_risk",
    bind=True,
)
def scan_compliance_risk(self) -> Dict:
    """Scan recent approved deals against current carrier appointment access."""
    try:
        from app.compliance import services
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            result = services.scan_recent_deal_risk(db)
            for event in result.get("events", []):
                asyncio.run(services.emit_compliance_notification(event["tenant_id"], "compliance_event_created", {
                    **event,
                    "notification_type": event["event_type"],
                    "title": "Potential compliance risk",
                }))
            return result
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"Compliance risk scan failed: {exc}")
        return {"success": False, "error": str(exc)}
