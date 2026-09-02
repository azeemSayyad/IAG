"""
Celery Application Configuration (Step 19.2)

Worker Architecture:
- SMS Worker - Processes outbound SMS queue
- AI Worker - Processes AI generation queue
- Booking Worker - Processes booking operations
- Reminder Worker - Processes appointment reminders
- Analytics Worker - Processes analytics jobs
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings


# Create Celery app
celery_app = Celery(
    "launchpad_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "workers.tasks.sms",
        "workers.tasks.ai",
        "workers.tasks.booking",
        "workers.tasks.reminders",
        "workers.tasks.followups",
        "workers.tasks.ingestion",
        "workers.tasks.analytics",
        "workers.tasks.system",
        "workers.tasks.pacing",
    ],
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
        # DISABLED — FIRST-TEMPLATE-ONLY lockdown (fd79760): reminders are blocked
        # at the send chokepoint, so this timer is turned off so it never runs.
        # "process-reminders": {
        #     "task": "workers.tasks.reminders.process_pending_reminders",
        #     "schedule": 300.0,
        # },
        # Drain the outbound SMS queue every 2s -> sub-second-class message latency
        # (was 60s, which made messages wait ~30s on average).
        # expires: if a tick can't run within 15s, drop it — the next tick (2s
        # later) supersedes it, so stale duplicates never pile up under load.
        "process-outbound-sms": {
            "task": "workers.tasks.sms.process_sms_queue",
            "schedule": 2.0,
            "options": {"expires": 15},
        },
        # Poll provider replies every 5s when webhooks are not configured
        # (was 60s, which made inbound/booking wait ~30s on average).
        # expires: drop a poll tick that can't run within 30s — polling is
        # idempotent and a newer tick already covers the same window, so this
        # prevents a backlog of stale polls from forming behind slow AI tasks.
        "poll-provider-replies": {
            "task": "workers.tasks.sms.poll_provider_replies",
            "schedule": 5.0,
            "options": {"expires": 30},
        },
        # Poll the SECOND lead-SMS provider (ENGAGE2) every 5s (no-op until configured).
        "poll-engage2-replies": {
            "task": "workers.tasks.sms.poll_engage2_replies",
            "schedule": 5.0,
            "options": {"expires": 30},
        },
        # Poll the DEDICATED hiree (applicant) account's replies every 5s (separate
        # account from leads; no-op until the hiree provider is configured).
        "poll-applicant-replies": {
            "task": "workers.tasks.sms.poll_applicant_replies",
            "schedule": 5.0,
            "options": {"expires": 30},
        },
        # Auto-sync positive-intent inbound leads into the SMS human queue.
        "ingest-positive-sms-leads": {
            "task": "workers.tasks.sms.ingest_positive_leads",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        # Hand any QUEUED lead to an available idle agent every 10s. Assignment
        # otherwise only fires on event triggers, so a queued lead could sit
        # unassigned while agents are free. expires<schedule so ticks never pile.
        "auto-assign-sms-queue": {
            "task": "workers.tasks.sms.auto_assign_queue",
            "schedule": 10.0,
            "options": {"expires": 8},
        },
        # DISABLED — FIRST-TEMPLATE-ONLY lockdown (fd79760): no-reply / missed-
        # appointment / nurture follow-ups are blocked at the send chokepoint, so
        # these timers are turned off so they never run.
        # "process-no-reply": {
        #     "task": "workers.tasks.followups.process_no_reply_leads",
        #     "schedule": 3600.0,
        # },
        # "process-missed-appointments": {
        #     "task": "workers.tasks.followups.process_missed_appointments",
        #     "schedule": 1800.0,
        # },
        # "process-nurture": {
        #     "task": "workers.tasks.followups.process_nurture_leads",
        #     "schedule": 21600.0,
        # },
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
        # DISABLED — FIRST-TEMPLATE-ONLY lockdown (fd79760): the emergency-fill
        # blast ("Great news!… Reply YES to book!") is blocked at the send
        # chokepoint, so this timer is turned off so it never runs.
        # "emergency-fill": {
        #     "task": "workers.tasks.system.run_emergency_fill",
        #     "schedule": 300.0,
        # },
        # Check agent presence timeouts every 30 seconds
        "check-presence-timeouts": {
            "task": "workers.tasks.system.check_presence_timeouts",
            "schedule": 30.0,
        },
        # Carrier appointment expiration compliance scan nightly
        "scan-compliance-expirations": {
            "task": "workers.tasks.system.scan_compliance_expirations",
            "schedule": crontab(hour=6, minute=0),
        },
        # Recent deal risk scan nightly
        "scan-compliance-risk": {
            "task": "workers.tasks.system.scan_compliance_risk",
            "schedule": crontab(hour=6, minute=15),
        },
        # Appointment Capacity Engine controller — top up lead releases to keep
        # the day's calendars filling. Inert unless SAME_DAY_PACING_ENABLED is on.
        "capacity-engine-tick": {
            "task": "workers.tasks.pacing.pacing_tick",
            "schedule": float((getattr(settings, "PACING_CYCLE_MINUTES", 15) or 15) * 60),
        },
        # Queue-Only Mode drip controller — releases held leads at the admin-set
        # rate while Queue-Only Mode is on. Fires every 5s so drip_cycle() can
        # spread the batch EVENLY within the interval (one lead per
        # interval/leads seconds, e.g. 20/1min -> 1 every 3s) instead of dumping a
        # whole minute's worth at once. drip_cycle() self-throttles to per_lead, so
        # most 5s ticks are cheap no-ops. Inert when not in Queue-Only Mode.
        "queue-only-drip-tick": {
            "task": "workers.tasks.pacing.drip_tick",
            "schedule": 5.0,
            "options": {"expires": 4},
        },
    },
)

# Import task modules explicitly; this keeps local workers deterministic when
# running from a source checkout and avoids namespace collisions with backend
# modules named `app.*`.
celery_app.autodiscover_tasks(["workers.tasks"])
