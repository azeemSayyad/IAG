"""
Celery Application Configuration (Step 19.2)

Worker Architecture:
- SMS Worker — Processes outbound SMS queue
- AI Worker — Processes AI generation queue
- Booking Worker — Processes booking operations
- Reminder Worker — Processes appointment reminders
- Analytics Worker — Processes analytics jobs
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings


# Create Celery app
celery_app = Celery(
    "launchpad_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,  # 4 minutes soft limit

    # Worker
    worker_prefetch_multiplier=1,  # One task at a time per worker
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
    worker_disable_rate_limits=False,

    # Retry
    task_acks_late=True,  # Acknowledge after completion
    task_reject_on_worker_lost=True,

    # Result backend
    result_expires=3600,  # Results expire after 1 hour

    # Task routes
    task_routes={
        "workers.tasks.sms.*": {"queue": "sms"},
        "workers.tasks.ai.*": {"queue": "ai"},
        "workers.tasks.booking.*": {"queue": "booking"},
        "workers.tasks.reminders.*": {"queue": "reminders"},
        "workers.tasks.analytics.*": {"queue": "analytics"},
        "workers.tasks.followups.*": {"queue": "followups"},
        "workers.tasks.ingestion.*": {"queue": "ingestion"},
        "workers.tasks.system.*": {"queue": "system"},
    },

    # Queue definitions
    task_default_queue="default",
    task_queues={
        "sms": {"exchange": "sms", "routing_key": "sms"},
        "ai": {"exchange": "ai", "routing_key": "ai"},
        "booking": {"exchange": "booking", "routing_key": "booking"},
        "reminders": {"exchange": "reminders", "routing_key": "reminders"},
        "analytics": {"exchange": "analytics", "routing_key": "analytics"},
        "followups": {"exchange": "followups", "routing_key": "followups"},
        "ingestion": {"exchange": "ingestion", "routing_key": "ingestion"},
        "system": {"exchange": "system", "routing_key": "system"},
    },

    # Beat schedule (periodic tasks)
    beat_schedule={
        # Process delayed jobs every minute
        "process-delayed-jobs": {
            "task": "workers.tasks.system.process_delayed_jobs",
            "schedule": 60.0,
        },
        # Process reminders every 5 minutes
        "process-reminders": {
            "task": "workers.tasks.reminders.process_pending_reminders",
            "schedule": 300.0,
        },
        # Process no-reply followups every hour
        "process-no-reply": {
            "task": "workers.tasks.followups.process_no_reply_leads",
            "schedule": 3600.0,
        },
        # Process missed appointments every 30 minutes
        "process-missed-appointments": {
            "task": "workers.tasks.followups.process_missed_appointments",
            "schedule": 1800.0,
        },
        # Process nurture campaigns every 6 hours
        "process-nurture": {
            "task": "workers.tasks.followups.process_nurture_leads",
            "schedule": 21600.0,
        },
        # Generate analytics every hour
        "generate-analytics": {
            "task": "workers.tasks.analytics.generate_hourly_metrics",
            "schedule": 3600.0,
        },
        # Cleanup expired locks every 15 minutes
        "cleanup-locks": {
            "task": "workers.tasks.system.cleanup_expired_locks",
            "schedule": 900.0,
        },
        # Emergency fill cycle every 5 minutes
        "emergency-fill": {
            "task": "workers.tasks.system.run_emergency_fill",
            "schedule": 300.0,
        },
        # Check agent presence timeouts every 30 seconds
        "check-presence-timeouts": {
            "task": "workers.tasks.system.check_presence_timeouts",
            "schedule": 30.0,
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["workers.tasks"])
