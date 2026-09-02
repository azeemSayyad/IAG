"""
Ingestion Worker Tasks

Processes lead import operations.
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.ingestion.process_csv_import",
    max_retries=1,
    soft_time_limit=300,
)
def process_csv_import(
    file_path: str,
    tenant_id: str,
    dedup_mode: str = "skip",
    user_id: str = None,
) -> Dict:
    """
    Process CSV import in background.

    For large files that would timeout in the API.
    """
    try:
        logger.info(f"Processing CSV import for tenant {tenant_id}")

        from app.core.database import get_db
        from app.ingestion.services.csv_import import import_leads_from_csv

        db = next(get_db())
        try:
            result = import_leads_from_csv(
                db=db,
                file_path=file_path,
                tenant_id=tenant_id,
                dedup_mode=dedup_mode,
            )

            logger.info(f"CSV import complete: {result}")
            return result

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"CSV import failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.ingestion.process_webhook_leads",
    max_retries=2,
    default_retry_delay=30,
)
def process_webhook_leads(
    leads: list,
    source: str,
    tenant_id: str,
) -> Dict:
    """
    Process webhook lead imports.
    """
    try:
        logger.info(f"Processing {len(leads)} webhook leads from {source}")

        from app.core.database import get_db
        from app.ingestion.services.webhook import process_webhook_leads as process_leads

        db = next(get_db())
        try:
            result = process_leads(db, leads, source, tenant_id)
            logger.info(f"Webhook processing complete: {result}")
            return result
        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Webhook processing failed: {exc}")
        return {"success": False, "error": str(exc)}
