"""
Queue System (Step 19.1)

Redis-based job queues for async processing.

Queue Types:
- outbound_sms — SMS sending jobs
- retries — Failed job retries
- reminders — Appointment reminders
- ai_generation — AI response generation
- analytics — Analytics processing
- followups — Follow-up sequences
- ingestion — Lead import processing
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.redis import RedisService


class QueueType(str, Enum):
    """Available queue types."""
    OUTBOUND_SMS = "queue:outbound_sms"
    RETRIES = "queue:retries"
    REMINDERS = "queue:reminders"
    AI_GENERATION = "queue:ai_generation"
    ANALYTICS = "queue:analytics"
    FOLLOWUPS = "queue:followups"
    INGESTION = "queue:ingestion"
    DEAD_LETTER = "queue:dead_letter"


class JobPriority(int, Enum):
    """Job priority levels."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class JobStatus(str, Enum):
    """Job status values."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


class Job:
    """Represents a queue job."""

    def __init__(
        self,
        job_type: str,
        payload: Dict[str, Any],
        queue: QueueType = QueueType.OUTBOUND_SMS,
        priority: JobPriority = JobPriority.NORMAL,
        max_retries: int = 3,
        delay_seconds: int = 0,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        self.job_id = str(uuid.uuid4())
        self.job_type = job_type
        self.payload = payload
        self.queue = queue
        self.priority = priority
        self.max_retries = max_retries
        self.delay_seconds = delay_seconds
        self.idempotency_key = idempotency_key or f"{job_type}:{json.dumps(payload, sort_keys=True)}"
        self.metadata = metadata or {}

        self.status = JobStatus.PENDING
        self.attempts = 0
        self.last_error: Optional[str] = None
        self.created_at = datetime.now(timezone.utc)
        self.processed_at: Optional[datetime] = None
        self.next_retry_at: Optional[datetime] = None

    def to_dict(self) -> Dict:
        """Serialize job to dictionary."""
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "payload": self.payload,
            "queue": self.queue.value,
            "priority": self.priority.value,
            "max_retries": self.max_retries,
            "delay_seconds": self.delay_seconds,
            "idempotency_key": self.idempotency_key,
            "metadata": self.metadata,
            "status": self.status.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat(),
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Job":
        """Deserialize job from dictionary."""
        job = cls(
            job_type=data["job_type"],
            payload=data["payload"],
            queue=QueueType(data["queue"]),
            priority=JobPriority(data["priority"]),
            max_retries=data["max_retries"],
            delay_seconds=data.get("delay_seconds", 0),
            idempotency_key=data.get("idempotency_key"),
            metadata=data.get("metadata", {}),
        )
        job.job_id = data["job_id"]
        job.status = JobStatus(data["status"])
        job.attempts = data["attempts"]
        job.last_error = data.get("last_error")
        job.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("processed_at"):
            job.processed_at = datetime.fromisoformat(data["processed_at"])
        if data.get("next_retry_at"):
            job.next_retry_at = datetime.fromisoformat(data["next_retry_at"])
        return job


class QueueManager:
    """Manages Redis-based job queues."""

    def __init__(self):
        self.redis = RedisService()

    @staticmethod
    def _to_text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode()
        return str(value)

    def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        queue: QueueType = QueueType.OUTBOUND_SMS,
        priority: JobPriority = JobPriority.NORMAL,
        max_retries: int = 3,
        delay_seconds: int = 0,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Job]:
        """
        Add a job to a queue.

        Returns Job if enqueued, None if duplicate (idempotency check).
        """
        job = Job(
            job_type=job_type,
            payload=payload,
            queue=queue,
            priority=priority,
            max_retries=max_retries,
            delay_seconds=delay_seconds,
            idempotency_key=idempotency_key,
        )

        # Check idempotency
        if self._is_duplicate(job.idempotency_key):
            return None

        # Store job details
        job_key = f"job:{job.job_id}"
        self.redis.set_cache(job_key, job.to_dict(), ttl=86400 * 7)  # 7 days

        # Mark idempotency key
        self._mark_idempotency(job.idempotency_key, job.job_id)

        # Add to queue
        if delay_seconds > 0:
            # Delayed job — add to sorted set with score = execute_at timestamp
            execute_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            job.next_retry_at = execute_at
            delayed_key = f"{queue.value}:delayed"
            self.redis.client.zadd(
                delayed_key,
                {job.job_id: execute_at.timestamp()}
            )
        else:
            # Immediate job — add to list
            self.redis.client.rpush(queue.value, job.job_id)

        # Track job in set for monitoring
        self.redis.client.sadd(f"{queue.value}:jobs", job.job_id)

        return job

    def dequeue(
        self,
        queue: QueueType,
        timeout: int = 0,
    ) -> Optional[Job]:
        """
        Dequeue a job from a queue.

        Args:
            queue: Queue to dequeue from
            timeout: Blocking timeout in seconds (0 = non-blocking)

        Returns:
            Job or None if queue is empty
        """
        if timeout > 0:
            result = self.redis.client.blpop(queue.value, timeout=timeout)
            if result:
                job_id = self._to_text(result[1])
            else:
                return None
        else:
            job_id = self.redis.client.lpop(queue.value)
            if job_id:
                job_id = self._to_text(job_id)
            else:
                return None

        # Get job details
        job_key = f"job:{job_id}"
        job_data = self.redis.get_cache(job_key)

        if not job_data:
            return None

        job = Job.from_dict(job_data)
        job.status = JobStatus.PROCESSING
        job.attempts += 1
        job.processed_at = datetime.now(timezone.utc)

        # Update job status
        self.redis.set_cache(job_key, job.to_dict(), ttl=86400 * 7)

        return job

    def complete_job(self, job: Job) -> None:
        """Mark a job as completed."""
        job.status = JobStatus.COMPLETED
        job_key = f"job:{job.job_id}"
        self.redis.set_cache(job_key, job.to_dict(), ttl=86400)

        # Remove from active set
        self.redis.client.srem(f"{job.queue.value}:jobs", job.job_id)

        # Increment completed counter
        self.redis.client.incr(f"{job.queue.value}:completed")

    def fail_job(self, job: Job, error: str) -> None:
        """
        Mark a job as failed.

        If retries remaining, schedule retry with exponential backoff.
        Otherwise, move to dead letter queue.
        """
        job.last_error = error
        job.attempts += 1

        if job.attempts < job.max_retries:
            # Schedule retry with exponential backoff
            job.status = JobStatus.RETRYING
            delay = self._calculate_backoff(job.attempts)
            job.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)

            job_key = f"job:{job.job_id}"
            self.redis.set_cache(job_key, job.to_dict(), ttl=86400 * 7)

            # Add to delayed retry queue
            delayed_key = f"{QueueType.RETRIES.value}:delayed"
            self.redis.client.zadd(
                delayed_key,
                {job.job_id: job.next_retry_at.timestamp()}
            )

            # Increment retry counter
            self.redis.client.incr(f"{job.queue.value}:retries")
        else:
            # Move to dead letter queue
            self._move_to_dlq(job)

    def _move_to_dlq(self, job: Job) -> None:
        """Move job to dead letter queue."""
        job.status = JobStatus.DEAD_LETTER
        job_key = f"job:{job.job_id}"
        self.redis.set_cache(job_key, job.to_dict(), ttl=86400 * 30)  # 30 days

        # Add to DLQ
        self.redis.client.rpush(QueueType.DEAD_LETTER.value, job.job_id)

        # Remove from active set
        self.redis.client.srem(f"{job.queue.value}:jobs", job.job_id)

        # Increment DLQ counter
        self.redis.client.incr(f"{job.queue.value}:dead_lettered")

    def _calculate_backoff(self, attempt: int) -> int:
        """
        Calculate exponential backoff delay.

        Formula: base_delay * 2^(attempt-1)
        Caps at 1 hour.
        """
        base_delay = 60  # 1 minute
        max_delay = 3600  # 1 hour
        delay = base_delay * (2 ** (attempt - 1))
        return min(delay, max_delay)

    def _is_duplicate(self, idempotency_key: str) -> bool:
        """Check if job with this idempotency key already exists."""
        key = f"idempotency:{idempotency_key}"
        return self.redis.client.exists(key) > 0

    def _mark_idempotency(self, idempotency_key: str, job_id: str) -> None:
        """Mark idempotency key as used."""
        key = f"idempotency:{idempotency_key}"
        self.redis.set_cache(key, {"job_id": job_id, "created_at": datetime.now(timezone.utc).isoformat()}, ttl=86400)

    def get_queue_size(self, queue: QueueType) -> int:
        """Get number of jobs in queue."""
        return self.redis.client.llen(queue.value)

    def get_delayed_size(self, queue: QueueType) -> int:
        """Get number of delayed jobs."""
        delayed_key = f"{queue.value}:delayed"
        return self.redis.client.zcard(delayed_key)

    def get_dlq_size(self) -> int:
        """Get number of jobs in dead letter queue."""
        return self.redis.client.llen(QueueType.DEAD_LETTER.value)

    def get_all_queue_stats(self) -> Dict:
        """Get statistics for all queues."""
        stats = {}
        for queue in QueueType:
            if queue == QueueType.DEAD_LETTER:
                continue
            stats[queue.value] = {
                "pending": self.get_queue_size(queue),
                "delayed": self.get_delayed_size(queue),
                "completed": int(self.redis.client.get(f"{queue.value}:completed") or 0),
                "retries": int(self.redis.client.get(f"{queue.value}:retries") or 0),
                "dead_lettered": int(self.redis.client.get(f"{queue.value}:dead_lettered") or 0),
            }
        stats["dlq"] = {"size": self.get_dlq_size()}
        return stats

    def process_delayed_jobs(self) -> int:
        """
        Process delayed jobs that are ready to execute.

        Called periodically by worker.

        Returns number of jobs moved to ready queue.
        """
        processed = 0
        now = datetime.now(timezone.utc).timestamp()

        for queue in QueueType:
            if queue == QueueType.DEAD_LETTER:
                continue

            delayed_key = f"{queue.value}:delayed"

            # Get jobs ready to execute
            ready_jobs = self.redis.client.zrangebyscore(
                delayed_key, 0, now, start=0, num=100
            )

            for job_id in ready_jobs:
                job_id = self._to_text(job_id)

                # Move from delayed to ready
                pipe = self.redis.client.pipeline()
                pipe.zrem(delayed_key, job_id)
                pipe.rpush(queue.value, job_id)
                pipe.execute()

                processed += 1

        return processed

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        job_key = f"job:{job_id}"
        job_data = self.redis.get_cache(job_key)
        if job_data:
            return Job.from_dict(job_data)
        return None

    def retry_dlq_job(self, job_id: str) -> bool:
        """Retry a job from the dead letter queue."""
        job = self.get_job(job_id)
        if not job or job.status != JobStatus.DEAD_LETTER:
            return False

        # Reset job state
        job.status = JobStatus.PENDING
        job.attempts = 0
        job.last_error = None
        job.next_retry_at = None

        # Update job
        job_key = f"job:{job_id}"
        self.redis.set_cache(job_key, job.to_dict(), ttl=86400 * 7)

        # Remove from DLQ
        self.redis.client.lrem(QueueType.DEAD_LETTER.value, 0, job_id)

        # Add back to original queue
        self.redis.client.rpush(job.queue.value, job_id)

        return True


# Singleton
queue_manager = QueueManager()
