"""
AI Retry/Fallback System (Step 36.9)

Handles AI failures gracefully:

Failure Modes:
1. Ollama timeout → Try smaller model
2. Model crash → Try fallback model
3. Malformed output → Retry with stricter prompt
4. All models failed → Safe template response
5. Repeated failures → Queue for later retry

Retry Strategy:
- Max 3 attempts per request
- Exponential backoff: 1s, 2s, 4s
- Circuit breaker: Stop after 5 consecutive failures
- Fallback chain: llama3 → mistral → deepseek → template
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

from app.core.redis import redis_service

logger = logging.getLogger(__name__)


class FailureType(str, Enum):
    """Types of AI failures."""
    TIMEOUT = "timeout"
    MODEL_CRASH = "model_crash"
    MALFORMED_OUTPUT = "malformed_output"
    VALIDATION_FAILED = "validation_failed"
    ALL_MODELS_FAILED = "all_models_failed"
    UNKNOWN = "unknown"


class RetryStrategy:
    """Defines how to retry based on failure type."""

    STRATEGIES = {
        FailureType.TIMEOUT: {
            "action": "try_smaller_model",
            "max_retries": 3,
            "backoff_seconds": [1, 2, 4],
        },
        FailureType.MODEL_CRASH: {
            "action": "try_fallback_model",
            "max_retries": 2,
            "backoff_seconds": [1, 3],
        },
        FailureType.MALFORMED_OUTPUT: {
            "action": "retry_with_stricter_prompt",
            "max_retries": 2,
            "backoff_seconds": [1, 2],
        },
        FailureType.VALIDATION_FAILED: {
            "action": "use_safe_template",
            "max_retries": 0,
            "backoff_seconds": [],
        },
        FailureType.ALL_MODELS_FAILED: {
            "action": "use_safe_template",
            "max_retries": 0,
            "backoff_seconds": [],
        },
    }

    @classmethod
    def get_strategy(cls, failure_type: FailureType) -> Dict:
        """Get retry strategy for a failure type."""
        return cls.STRATEGIES.get(failure_type, {
            "action": "use_safe_template",
            "max_retries": 0,
            "backoff_seconds": [],
        })


class CircuitBreaker:
    """
    Circuit breaker for AI service.

    States:
    - closed: Normal operation, requests pass through
    - open: Too many failures, requests are rejected
    - half_open: Testing if service recovered
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._state = "closed"

    @property
    def state(self) -> str:
        """Get current circuit state."""
        if self._state == "open":
            # Check if recovery timeout has passed
            if self._last_failure_time:
                elapsed = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds()
                if elapsed > self.recovery_timeout:
                    self._state = "half_open"
        return self._state

    def record_success(self) -> None:
        """Record a successful request."""
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failed request."""
        self._failure_count += 1
        self._last_failure_time = datetime.now(timezone.utc)

        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(
                f"Circuit breaker opened after {self._failure_count} failures"
            )

    def can_execute(self) -> bool:
        """Check if requests can be executed."""
        state = self.state
        if state == "closed":
            return True
        elif state == "half_open":
            return True  # Allow one test request
        else:
            return False  # Open — reject request

    def get_status(self) -> Dict:
        """Get circuit breaker status."""
        return {
            "state": self.state,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure": self._last_failure_time.isoformat() if self._last_failure_time else None,
            "recovery_timeout": self.recovery_timeout,
        }


class RetryQueue:
    """
    Redis-based retry queue for failed AI requests.

    Failed requests are queued with delay for later processing.
    """

    QUEUE_KEY = "ai:retry_queue"

    def __init__(self):
        self.redis = redis_service

    def enqueue(
        self,
        request_id: str,
        payload: Dict,
        delay_seconds: int = 60,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> bool:
        """
        Enqueue a failed request for retry.

        Args:
            request_id: Unique request identifier
            payload: Original request payload
            delay_seconds: Seconds to wait before retry
            retry_count: Current retry count
            max_retries: Maximum retries allowed
        """
        if retry_count >= max_retries:
            logger.warning(f"Max retries ({max_retries}) reached for {request_id}")
            return False

        job = {
            "request_id": request_id,
            "payload": payload,
            "retry_count": retry_count + 1,
            "max_retries": max_retries,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "execute_at": (
                datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            ).isoformat(),
        }

        # Store in Redis sorted set with execute_at as score
        score = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).timestamp()
        self.redis.client.zadd(
            self.QUEUE_KEY,
            {f"{request_id}:{retry_count + 1}": score},
        )

        # Store job details
        self.redis.set_cache(
            f"ai:retry:{request_id}:{retry_count + 1}",
            job,
            ttl=delay_seconds + 300,  # TTL = delay + 5 min buffer
        )

        logger.info(f"Enqueued retry for {request_id} (attempt {retry_count + 1})")
        return True

    def dequeue_ready(self, limit: int = 10) -> List[Dict]:
        """
        Dequeue requests that are ready for retry.

        Returns list of ready jobs.
        """
        now = datetime.now(timezone.utc).timestamp()

        # Get jobs with score <= now
        ready = self.redis.client.zrangebyscore(
            self.QUEUE_KEY, 0, now, start=0, num=limit
        )

        jobs = []
        for key in ready:
            # Parse request_id and retry_count
            parts = key.rsplit(":", 1)
            if len(parts) != 2:
                continue

            request_id = parts[0]
            retry_count = int(parts[1])

            # Get job details
            job = self.redis.get_cache(f"ai:retry:{request_id}:{retry_count}")
            if job:
                jobs.append(job)

            # Remove from queue
            self.redis.client.zrem(self.QUEUE_KEY, key)

        return jobs

    def get_queue_size(self) -> int:
        """Get the number of pending retries."""
        return self.redis.client.zcard(self.QUEUE_KEY)

    def clear(self) -> int:
        """Clear all pending retries."""
        size = self.get_queue_size()
        self.redis.client.delete(self.QUEUE_KEY)
        return size


class AIRetryManager:
    """
    Manages AI request retries with circuit breaker and fallback chain.

    Usage:
        retry_mgr = AIRetryManager()
        result = await retry_mgr.execute_with_retry(
            func=generate_response,
            args={"prompt": "..."},
            fallback_func=use_template,
        )
    """

    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self.retry_queue = RetryQueue()

    async def execute_with_retry(
        self,
        func: Callable,
        args: Dict[str, Any],
        fallback_func: Optional[Callable] = None,
        request_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> Dict:
        """
        Execute an AI function with retry and fallback.

        Args:
            func: Async function to execute
            args: Arguments for the function
            fallback_func: Fallback function if all retries fail
            request_id: Unique request ID for tracking
            max_retries: Maximum retry attempts

        Returns:
            Dict with result, retry_count, failure_type, used_fallback
        """
        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            logger.warning("Circuit breaker open, using fallback")
            if fallback_func:
                result = await self._execute_fallback(fallback_func, args)
                return {
                    "result": result,
                    "retry_count": 0,
                    "failure_type": "circuit_breaker_open",
                    "used_fallback": True,
                }
            return {
                "result": None,
                "retry_count": 0,
                "failure_type": "circuit_breaker_open",
                "used_fallback": False,
                "error": "Circuit breaker open and no fallback provided",
            }

        # Try execution with retries
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = await func(**args)
                if result:
                    self.circuit_breaker.record_success()
                    return {
                        "result": result,
                        "retry_count": attempt,
                        "failure_type": None,
                        "used_fallback": False,
                    }

            except asyncio.TimeoutError:
                last_error = FailureType.TIMEOUT
                logger.warning(f"Attempt {attempt + 1} timed out")

            except Exception as e:
                last_error = FailureType.MODEL_CRASH
                logger.warning(f"Attempt {attempt + 1} failed: {e}")

            self.circuit_breaker.record_failure()

            # Backoff before retry
            if attempt < max_retries:
                backoff = RetryStrategy.STRATEGIES.get(
                    last_error, {}
                ).get("backoff_seconds", [1, 2, 4])
                delay = backoff[min(attempt, len(backoff) - 1)]
                await asyncio.sleep(delay)

        # All retries failed — use fallback
        if fallback_func:
            result = await self._execute_fallback(fallback_func, args)
            return {
                "result": result,
                "retry_count": max_retries + 1,
                "failure_type": last_error.value if last_error else "unknown",
                "used_fallback": True,
            }

        return {
            "result": None,
            "retry_count": max_retries + 1,
            "failure_type": last_error.value if last_error else "unknown",
            "used_fallback": False,
            "error": "All retries failed and no fallback provided",
        }

    async def _execute_fallback(
        self,
        fallback_func: Callable,
        args: Dict[str, Any],
    ) -> Any:
        """Execute fallback function."""
        try:
            if asyncio.iscoroutinefunction(fallback_func):
                return await fallback_func(**args)
            else:
                return fallback_func(**args)
        except Exception as e:
            logger.error(f"Fallback function failed: {e}")
            return None

    def get_status(self) -> Dict:
        """Get retry manager status."""
        return {
            "circuit_breaker": self.circuit_breaker.get_status(),
            "retry_queue_size": self.retry_queue.get_queue_size(),
        }
