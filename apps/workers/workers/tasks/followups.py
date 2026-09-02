"""
Follow-up Worker Tasks

Processes follow-up workflows.
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.followups.process_no_reply_leads",
    bind=True,
)
def process_no_reply_leads(self) -> Dict:
    """
    Process leads that haven't replied.

    Sends follow-up messages based on no-reply workflow.
    """
    try:
        logger.info("Processing no-reply leads")

        from app.core.database import get_db
        from app.followup.services.no_reply import process_all_no_reply_leads

        db = next(get_db())
        try:
            result = process_all_no_reply_leads(db)
            logger.info(f"No-reply processing: {result}")
            return result
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"No-reply processing failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.followups.process_missed_appointments",
    bind=True,
)
def process_missed_appointments(self) -> Dict:
    """
    Process missed appointments.

    Sends follow-up messages for no-show appointments.
    """
    try:
        logger.info("Processing missed appointments")

        from app.core.database import get_db
        from app.followup.services.missed_appointment import process_all_missed_appointments

        db = next(get_db())
        try:
            result = process_all_missed_appointments(db)
            logger.info(f"Missed appointment processing: {result}")
            return result
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Missed appointment processing failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.followups.process_nurture_leads",
    bind=True,
)
def process_nurture_leads(self) -> Dict:
    """
    Process nurture campaigns.

    Sends nurture messages to cold leads.
    """
    try:
        logger.info("Processing nurture leads")

        from app.core.database import get_db
        from app.followup.services.nurture import process_nurture_lead

        db = next(get_db())
        try:
            # Get all leads in nurture
            from app.models.lead import Lead
            leads = db.query(Lead).filter(
                Lead.status == "nurture",
                Lead.deleted_at.is_(None),
            ).all()

            processed = 0
            sent = 0

            for lead in leads:
                result = process_nurture_lead(db, lead)
                processed += 1
                if result.get("sent"):
                    sent += 1

            return {"success": True, "processed": processed, "sent": sent}

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Nurture processing failed: {exc}")
        return {"success": False, "error": str(exc)}
