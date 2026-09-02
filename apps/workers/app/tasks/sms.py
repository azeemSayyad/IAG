"""
SMS Worker Tasks

Processes outbound SMS queue.
"""

import logging
from typing import Dict

from app.celery_app import celery_app
from app.core.database import get_db
from app.core.audit import log_ai_action

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.sms.send_sms",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_sms(self, lead_id: str, message: str, tenant_id: str, campaign_id: str = None) -> Dict:
    """
    Send SMS to a lead.

    Retries up to 3 times with exponential backoff.
    """
    try:
        logger.info(f"Sending SMS to lead {lead_id}")

        # Import here to avoid circular imports
        from app.ai.services.communication_provider import send_sms_to_lead
        from app.models.lead import Lead

        # Get database session
        db = next(get_db())
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return {"success": False, "error": "Lead not found"}

            # Send SMS
            result = send_sms_to_lead(
                phone=lead.phone,
                lead_id=lead_id,
                message=message,
                tenant_id=tenant_id,
            )

            # Log action
            log_ai_action(
                tenant_id=tenant_id,
                action="sms_sent",
                resource_type="lead",
                resource_id=lead_id,
                details={
                    "campaign_id": campaign_id,
                    "success": result.get("success"),
                    "provider": result.get("provider"),
                    "message_sid": result.get("message_sid"),
                },
            )

            return result

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"SMS send failed: {exc}")
        # Retry with exponential backoff
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks.sms.process_sms_queue",
    bind=True,
)
def process_sms_queue(self) -> Dict:
    """
    Process outbound SMS queue.

    Dequeues and sends SMS messages.
    """
    from app.core.queues import queue_manager, QueueType

    processed = 0
    failed = 0

    for _ in range(50):  # Process up to 50 messages per run
        job = queue_manager.dequeue(QueueType.OUTBOUND_SMS, timeout=1)
        if not job:
            break

        try:
            result = send_sms(
                lead_id=job.payload["lead_id"],
                message=job.payload["message"],
                tenant_id=job.payload["tenant_id"],
                campaign_id=job.payload.get("campaign_id"),
            )

            if result.get("success"):
                queue_manager.complete_job(job)
                processed += 1
            else:
                queue_manager.fail_job(job, result.get("error", "Unknown error"))
                failed += 1

        except Exception as exc:
            queue_manager.fail_job(job, str(exc))
            failed += 1

    return {"processed": processed, "failed": failed}
