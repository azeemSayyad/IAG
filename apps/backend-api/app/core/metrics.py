"""
Metrics Collection (Step 23.1)

Prometheus metrics for monitoring.

Metrics:
- Request count
- Request duration
- Error rate
- Active connections
- Queue sizes
- AI response time
- Database query time
"""

import time
from typing import Callable
from functools import wraps

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Summary,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Application info
APP_INFO = Info("launchpad", "Launchpad Call Center Application")
APP_INFO.info({
    "version": "1.0.0",
    "environment": "production",
})

# Request metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests in progress",
    ["method", "endpoint"],
)

# Error metrics
ERROR_COUNT = Counter(
    "http_errors_total",
    "Total HTTP errors",
    ["method", "endpoint", "status_code", "error_type"],
)

# Database metrics
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation", "table"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

DB_CONNECTIONS = Gauge(
    "db_connections_active",
    "Active database connections",
)

# Redis metrics
REDIS_OPERATION_DURATION = Histogram(
    "redis_operation_duration_seconds",
    "Redis operation duration in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
)

REDIS_CONNECTIONS = Gauge(
    "redis_connections_active",
    "Active Redis connections",
)

# AI metrics
AI_REQUEST_DURATION = Histogram(
    "ai_request_duration_seconds",
    "AI request duration in seconds",
    ["model", "operation"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

AI_REQUEST_COUNT = Counter(
    "ai_requests_total",
    "Total AI requests",
    ["model", "operation", "status"],
)

AI_TOKENS_USED = Counter(
    "ai_tokens_used_total",
    "Total AI tokens used",
    ["model", "operation"],
)

# Queue metrics
QUEUE_SIZE = Gauge(
    "queue_size",
    "Number of jobs in queue",
    ["queue_name"],
)

QUEUE_PROCESSED = Counter(
    "queue_processed_total",
    "Total jobs processed",
    ["queue_name", "status"],
)

# WebSocket metrics
WS_CONNECTIONS = Gauge(
    "websocket_connections_active",
    "Active WebSocket connections",
    ["tenant_id"],
)

WS_MESSAGES = Counter(
    "websocket_messages_total",
    "Total WebSocket messages",
    ["direction", "event_type"],
)

# Business metrics
LEADS_TOTAL = Gauge(
    "leads_total",
    "Total leads",
    ["tenant_id", "status"],
)

APPOINTMENTS_TOTAL = Gauge(
    "appointments_total",
    "Total appointments",
    ["tenant_id", "status"],
)

SMS_SENT = Counter(
    "sms_sent_total",
    "Total SMS sent",
    ["tenant_id", "status"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip metrics endpoint
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = request.url.path

        # Normalize path (remove IDs)
        normalized_path = self._normalize_path(path)

        # Track in-progress requests
        REQUESTS_IN_PROGRESS.labels(method=method, endpoint=normalized_path).inc()

        # Start timer
        start_time = time.time()

        try:
            response = await call_next(request)

            # Record metrics
            duration = time.time() - start_time
            REQUEST_COUNT.labels(
                method=method,
                endpoint=normalized_path,
                status_code=response.status_code,
            ).inc()
            REQUEST_DURATION.labels(
                method=method,
                endpoint=normalized_path,
            ).observe(duration)

            # Track errors
            if response.status_code >= 400:
                ERROR_COUNT.labels(
                    method=method,
                    endpoint=normalized_path,
                    status_code=response.status_code,
                    error_type=self._get_error_type(response.status_code),
                ).inc()

            return response

        except Exception as e:
            # Record error
            duration = time.time() - start_time
            REQUEST_DURATION.labels(
                method=method,
                endpoint=normalized_path,
            ).observe(duration)
            ERROR_COUNT.labels(
                method=method,
                endpoint=normalized_path,
                status_code=500,
                error_type=type(e).__name__,
            ).inc()
            raise

        finally:
            REQUESTS_IN_PROGRESS.labels(
                method=method,
                endpoint=normalized_path,
            ).dec()

    def _normalize_path(self, path: str) -> str:
        """Normalize path by replacing UUIDs with placeholders."""
        import re
        # Replace UUIDs
        path = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "{id}",
            path,
        )
        # Replace numeric IDs
        path = re.sub(r"/\d+", "/{id}", path)
        return path

    def _get_error_type(self, status_code: int) -> str:
        """Get error type from status code."""
        if status_code < 500:
            return "client_error"
        return "server_error"


def track_db_query(operation: str, table: str, duration: float) -> None:
    """Track database query duration."""
    DB_QUERY_DURATION.labels(operation=operation, table=table).observe(duration)


def track_redis_operation(operation: str, duration: float) -> None:
    """Track Redis operation duration."""
    REDIS_OPERATION_DURATION.labels(operation=operation).observe(duration)


def track_ai_request(
    model: str,
    operation: str,
    duration: float,
    tokens: int = 0,
    status: str = "success",
) -> None:
    """Track AI request."""
    AI_REQUEST_DURATION.labels(model=model, operation=operation).observe(duration)
    AI_REQUEST_COUNT.labels(model=model, operation=operation, status=status).inc()
    if tokens > 0:
        AI_TOKENS_USED.labels(model=model, operation=operation).inc(tokens)


def track_queue_size(queue_name: str, size: int) -> None:
    """Track queue size."""
    QUEUE_SIZE.labels(queue_name=queue_name).set(size)


def track_queue_processed(queue_name: str, status: str) -> None:
    """Track queue job processed."""
    QUEUE_PROCESSED.labels(queue_name=queue_name, status=status).inc()


def track_ws_connection(tenant_id: str, delta: int) -> None:
    """Track WebSocket connection change."""
    WS_CONNECTIONS.labels(tenant_id=tenant_id).inc(delta)


def track_ws_message(direction: str, event_type: str) -> None:
    """Track WebSocket message."""
    WS_MESSAGES.labels(direction=direction, event_type=event_type).inc()


def track_sms_sent(tenant_id: str, status: str) -> None:
    """Track SMS sent."""
    SMS_SENT.labels(tenant_id=tenant_id, status=status).inc()


def get_metrics() -> bytes:
    """Get all metrics in Prometheus format."""
    return generate_latest()
