"""
Queue Architecture (Step 4.4)

Redis-based job queues for:
- outbound_sms — New outreach messages
- retries — Failed message retries
- reminders — Appointment reminders
- followups — Follow-up sequences

Uses Redis lists as simple queues. Can be upgraded to Celery later.
"""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone

from app.core.redis import redis_service


# Queue names
QUEUE_OUTBOUND = "queue:outbound_sms"
QUEUE_RETRIES = "queue:retries"
QUEUE_REMINDERS = "queue:reminders"
QUEUE_FOLLOWUPS = "queue:followups"


def enqueue_outbound_sms(
    tenant_id: str,
    lead_id: str,
    phone: str,
    message: str,
    priority: int = 5,
    metadata: Dict[str, Any] = None,
) -> str:
    """
    Enqueue an outbound SMS job.

    Args:
        tenant_id: Tenant ID
        lead_id: Lead ID
        phone: Recipient phone number
        message: Message text
        priority: Lower = higher priority (1-10)
        metadata: Additional context

    Returns:
        Job ID
    """
    import uuid
    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "phone": phone,
        "message": message,
        "priority": priority,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 0,
        "max_attempts": 3,
    }

    redis_service.client.rpush(QUEUE_OUTBOUND, json.dumps(job))
    return job_id


def enqueue_retry(
    original_job: Dict[str, Any],
    delay_seconds: int = 300,
) -> str:
    """
    Enqueue a retry job with delay.

    Args:
        original_job: The original job that failed
        delay_seconds: Seconds to wait before retry

    Returns:
        Job ID
    """
    job = original_job.copy()
    job["attempts"] = job.get("attempts", 0) + 1
    job["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
    job["last_error"] = job.get("last_error", "unknown")

    redis_service.client.rpush(QUEUE_RETRIES, json.dumps(job))
    return job["id"]


def enqueue_reminder(
    tenant_id: str,
    lead_id: str,
    appointment_id: str,
    phone: str,
    reminder_type: str,  # "24h", "1h", "15m"
    appointment_time: str,
) -> str:
    """
    Enqueue an appointment reminder.

    Args:
        tenant_id: Tenant ID
        lead_id: Lead ID
        appointment_id: Appointment ID
        phone: Recipient phone
        reminder_type: Type of reminder
        appointment_time: ISO format appointment time

    Returns:
        Job ID
    """
    import uuid
    job_id = str(uuid.uuid4())

    # Calculate when to send
    appt_dt = datetime.fromisoformat(appointment_time)
    if reminder_type == "24h":
        send_at = appt_dt - timedelta(hours=24)
    elif reminder_type == "1h":
        send_at = appt_dt - timedelta(hours=1)
    elif reminder_type == "15m":
        send_at = appt_dt - timedelta(minutes=15)
    else:
        send_at = datetime.now(timezone.utc)

    job = {
        "id": job_id,
        "type": "reminder",
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "appointment_id": appointment_id,
        "phone": phone,
        "reminder_type": reminder_type,
        "appointment_time": appointment_time,
        "send_at": send_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    redis_service.client.rpush(QUEUE_REMINDERS, json.dumps(job))
    return job_id


def enqueue_followup(
    tenant_id: str,
    lead_id: str,
    phone: str,
    first_name: str,
    followup_number: int,
    delay_hours: int = 24,
) -> str:
    """
    Enqueue a follow-up message.

    Args:
        tenant_id: Tenant ID
        lead_id: Lead ID
        phone: Recipient phone
        first_name: Customer first name
        followup_number: Follow-up attempt number
        delay_hours: Hours to wait before sending

    Returns:
        Job ID
    """
    import uuid
    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "type": "followup",
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "phone": phone,
        "first_name": first_name,
        "followup_number": followup_number,
        "send_at": (datetime.now(timezone.utc) + timedelta(hours=delay_hours)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    redis_service.client.rpush(QUEUE_FOLLOWUPS, json.dumps(job))
    return job_id


def dequeue_job(queue_name: str) -> Optional[Dict[str, Any]]:
    """
    Dequeue a job from a queue.

    Returns:
        Job dict or None if queue is empty.
    """
    data = redis_service.client.lpop(queue_name)
    if data:
        return json.loads(data)
    return None


def peek_queue(queue_name: str, count: int = 10) -> List[Dict[str, Any]]:
    """
    Peek at jobs in a queue without removing them.

    Returns:
        List of job dicts.
    """
    jobs = redis_service.client.lrange(queue_name, 0, count - 1)
    return [json.loads(j) for j in jobs]


def get_queue_size(queue_name: str) -> int:
    """Get the number of jobs in a queue."""
    return redis_service.client.llen(queue_name)


def get_all_queue_sizes() -> Dict[str, int]:
    """Get sizes of all queues."""
    return {
        "outbound_sms": get_queue_size(QUEUE_OUTBOUND),
        "retries": get_queue_size(QUEUE_RETRIES),
        "reminders": get_queue_size(QUEUE_REMINDERS),
        "followups": get_queue_size(QUEUE_FOLLOWUPS),
    }
