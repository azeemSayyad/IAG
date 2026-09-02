"""
Booking Worker Tasks

Processes booking-related operations.
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.booking.process_overflow_queue",
    bind=True,
)
def process_overflow_queue(self) -> Dict:
    """
    Process overflow queue when slots become available.
    """
    try:
        logger.info("Processing overflow queue")

        from app.core.database import get_db
        from app.booking.services.overflow import process_overflow_queue as process_overflow

        db = next(get_db())
        try:
            # Process for each tenant
            from app.models.tenant import Tenant
            tenants = db.query(Tenant).filter(Tenant.status == "active").all()

            total_processed = 0
            total_booked = 0

            for tenant in tenants:
                result = process_overflow(db, str(tenant.id))
                total_processed += result.get("processed", 0)
                total_booked += result.get("booked", 0)

            return {
                "success": True,
                "processed": total_processed,
                "booked": total_booked,
            }

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Overflow processing failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.booking.cleanup_expired_locks",
    bind=True,
)
def cleanup_expired_locks(self) -> Dict:
    """
    Clean up expired slot locks.
    """
    try:
        logger.info("Cleaning up expired locks")

        from app.core.redis import RedisService
        from app.booking.services.locking import cleanup_expired_locks as cleanup_locks

        redis = RedisService()
        result = cleanup_locks(redis)

        return {"success": True, "cleaned": result}

    except Exception as exc:
        logger.error(f"Lock cleanup failed: {exc}")
        return {"success": False, "error": str(exc)}
