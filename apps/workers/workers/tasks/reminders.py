"""
Reminder Worker Tasks

Processes appointment reminders.
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.reminders.process_pending_reminders",
    bind=True,
)
def process_pending_reminders(self) -> Dict:
    """
    Process pending appointment reminders.

    Sends reminders for appointments at 24h, 1h, and 15m before.
    """
    try:
        logger.info("Processing pending reminders")

        from app.core.database import get_db
        from app.booking.services.reminders import process_pending_reminders as process_reminders

        db = next(get_db())
        try:
            result = process_reminders(db)
            logger.info(f"Reminders processed: {result}")
            return result
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Reminder processing failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.reminders.send_reminder",
    max_retries=2,
    default_retry_delay=60,
)
def send_reminder(
    appointment_id: str,
    reminder_type: str,
    tenant_id: str,
) -> Dict:
    """
    Send a specific reminder.
    """
    try:
        logger.info(f"Sending {reminder_type} reminder for appointment {appointment_id}")

        from app.core.database import get_db
        from app.booking.services.reminders import send_reminder as send_reminder_impl
        from app.models.appointment import Appointment

        db = next(get_db())
        try:
            appointment = db.query(Appointment).filter(
                Appointment.id == appointment_id
            ).first()

            if not appointment:
                return {"success": False, "error": "Appointment not found"}

            result = send_reminder_impl(db, appointment, reminder_type)
            return result

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Reminder send failed: {exc}")
        return {"success": False, "error": str(exc)}
