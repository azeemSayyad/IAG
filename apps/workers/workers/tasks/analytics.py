"""
Analytics Worker Tasks

Processes analytics and reporting.
"""

import logging
from typing import Dict

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.tasks.analytics.generate_hourly_metrics",
    bind=True,
)
def generate_hourly_metrics(self) -> Dict:
    """
    Generate hourly metrics for ClickHouse.

    Aggregates data from PostgreSQL and stores in ClickHouse.
    """
    try:
        logger.info("Generating hourly metrics")

        from app.core.database import get_db
        from datetime import datetime, timezone
        import json

        db = next(get_db())
        try:
            # Get current hour's data
            now = datetime.now(timezone.utc)
            hour_start = now.replace(minute=0, second=0, microsecond=0)

            # Aggregate metrics
            from app.models.lead import Lead
            from app.models.appointment import Appointment
            from app.models.message import Message

            new_leads = db.query(Lead).filter(
                Lead.created_at >= hour_start,
                Lead.created_at < now,
            ).count()

            new_appointments = db.query(Appointment).filter(
                Appointment.created_at >= hour_start,
                Appointment.created_at < now,
            ).count()

            messages_sent = db.query(Message).filter(
                Message.created_at >= hour_start,
                Message.created_at < now,
                Message.sender == "ai",
            ).count()

            messages_received = db.query(Message).filter(
                Message.created_at >= hour_start,
                Message.created_at < now,
                Message.sender == "customer",
            ).count()

            metrics = {
                "hour": hour_start.isoformat(),
                "new_leads": new_leads,
                "new_appointments": new_appointments,
                "messages_sent": messages_sent,
                "messages_received": messages_received,
            }

            from app.core.redis import redis_service
            redis_service.client.xadd(
                "stream:analytics_events",
                {
                    "event_type": "hourly_metrics",
                    "tenant_id": "system",
                    "data": json.dumps(metrics),
                    "timestamp": now.isoformat(),
                    "date": now.date().isoformat(),
                },
                maxlen=50000,
            )

            return {
                "success": True,
                "hour": hour_start.isoformat(),
                "metrics": metrics,
            }

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Metrics generation failed: {exc}")
        return {"success": False, "error": str(exc)}


@celery_app.task(
    name="workers.tasks.analytics.generate_daily_report",
    bind=True,
)
def generate_daily_report(self) -> Dict:
    """
    Generate daily analytics report.
    """
    try:
        logger.info("Generating daily report")

        from app.core.database import get_db
        from app.admin.services.analytics import get_tenant_analytics

        db = next(get_db())
        try:
            from app.models.tenant import Tenant
            tenants = db.query(Tenant).filter(Tenant.status == "active").all()

            reports = {}
            for tenant in tenants:
                analytics = get_tenant_analytics(db, str(tenant.id))
                reports[str(tenant.id)] = analytics

            return {"success": True, "reports": len(reports)}

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"Daily report failed: {exc}")
        return {"success": False, "error": str(exc)}
