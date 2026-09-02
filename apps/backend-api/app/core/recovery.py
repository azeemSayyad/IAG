"""
Failure Recovery Service (Step 14.3)

Implements:
- Retry logic with exponential backoff
- Dead-letter queue for failed jobs
- Fallback AI responses
- Circuit breaker pattern
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Any, Optional
from functools import wraps

from app.core.redis import redis_service
from app.core.audit import log_ai_action


# Dead-letter queue
DLQ_KEY = "queue:dead_letter"

# Circuit breaker states
CIRCUIT_CLOSED = "closed"      # Normal operation
CIRCUIT_OPEN = "open"          # Failing, reject requests
CIRCUIT_HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """Circuit breaker for external service calls."""

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state_key = f"circuit:{service_name}:state"
        self.failure_key = f"circuit:{service_name}:failures"
        self.last_failure_key = f"circuit:{service_name}:last_failure"

    def get_state(self) -> str:
        """Get current circuit state."""
        state = redis_service.client.get(self.state_key)
        if state:
            return state

        # Check if we should transition from open to half-open
        if redis_service.client.get(self.last_failure_key):
            last_failure = float(redis_service.client.get(self.last_failure_key))
            if datetime.now(timezone.utc).timestamp() - last_failure > self.recovery_timeout:
                self._set_state(CIRCUIT_HALF_OPEN)
                return CIRCUIT_HALF_OPEN

        return CIRCUIT_CLOSED

    def _set_state(self, state: str):
        """Set circuit state."""
        redis_service.client.set(self.state_key, state, ex=300)

    def record_success(self):
        """Record a successful call."""
        redis_service.client.delete(self.failure_key)
        self._set_state(CIRCUIT_CLOSED)

    def record_failure(self):
        """Record a failed call."""
        failures = redis_service.client.incr(self.failure_key)
        redis_service.client.set(self.last_failure_key, datetime.now(timezone.utc).timestamp())

        if failures >= self.failure_threshold:
            self._set_state(CIRCUIT_OPEN)

    def can_execute(self) -> bool:
        """Check if a call can be executed."""
        state = self.get_state()
        if state == CIRCUIT_CLOSED:
            return True
        elif state == CIRCUIT_HALF_OPEN:
            return True  # Allow one test request
        else:
            return False  # Circuit open, reject


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for retry with exponential backoff.
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        await asyncio.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


def add_to_dead_letter_queue(
    job_type: str,
    job_data: Dict,
    error: str,
    retry_count: int = 0,
):
    """
    Add a failed job to the dead-letter queue.
    """
    dlq_entry = {
        "job_type": job_type,
        "job_data": job_data,
        "error": error,
        "retry_count": retry_count,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }

    redis_service.client.rpush(DLQ_KEY, json.dumps(dlq_entry))

    # Audit log
    log_ai_action(
        tenant_id=job_data.get("tenant_id", "system"),
        action="job_added_to_dlq",
        resource_type="job",
        details={"job_type": job_type, "error": error[:200]},
    )


def get_dead_letter_queue(limit: int = 100) -> list:
    """
    Get jobs from the dead-letter queue.
    """
    jobs = redis_service.client.lrange(DLQ_KEY, 0, limit - 1)
    return [json.loads(j) for j in jobs]


def retry_dead_letter_job(index: int) -> bool:
    """
    Retry a job from the dead-letter queue.
    """
    job_data = redis_service.client.lindex(DLQ_KEY, index)
    if not job_data:
        return False

    job = json.loads(job_data)

    # Move back to appropriate queue
    if job["job_type"] == "sms":
        redis_service.client.rpush("queue:outbound_sms", json.dumps(job["job_data"]))
    elif job["job_type"] == "followup":
        redis_service.client.rpush("queue:followups", json.dumps(job["job_data"]))

    # Remove from DLQ
    redis_service.client.lrem(DLQ_KEY, 1, job_data)

    return True


def clear_dead_letter_queue():
    """
    Clear the entire dead-letter queue.
    """
    redis_service.client.delete(DLQ_KEY)


# Fallback AI responses
FALLBACK_RESPONSES = {
    "outreach": "Hi! We'd love to help you explore insurance options. Would you like to learn more?",
    "objection": "I understand your concern. Let me connect you with an agent who can address that.",
    "booking": "I'd be happy to help you schedule an appointment. When works best for you?",
    "general": "Thanks for reaching out! How can I help you today?",
    "error": "I'm having trouble processing that right now. Let me connect you with an agent.",
}


def get_fallback_response(context: str = "general") -> str:
    """
    Get a fallback AI response when the AI service is unavailable.
    """
    return FALLBACK_RESPONSES.get(context, FALLBACK_RESPONSES["general"])


# Health check service
async def check_service_health(service_name: str, health_url: str) -> Dict:
    """
    Check if a service is healthy.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(health_url)
            return {
                "service": service_name,
                "status": "healthy" if response.status_code == 200 else "unhealthy",
                "latency_ms": response.elapsed.total_seconds() * 1000,
            }
    except Exception as e:
        return {
            "service": service_name,
            "status": "unhealthy",
            "error": str(e),
        }


async def check_all_services_health() -> Dict:
    """
    Check health of all services.
    """
    services = {
        "backend-api": "http://localhost:8000/health",
        "redis": "http://localhost:6379",
        "postgres": "http://localhost:5432",
    }

    results = {}
    for service, url in services.items():
        results[service] = await check_service_health(service, url)

    return results
