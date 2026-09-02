"""
Distributed Tracing (Step 23.3)

OpenTelemetry integration for distributed tracing.

Features:
- Automatic instrumentation of FastAPI
- Redis tracing
- Database query tracing
- HTTP client tracing
- Custom span creation
"""

import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.trace import StatusCode, Status

from app.core.config import settings


# Global tracer
_tracer: Optional[trace.Tracer] = None


def init_tracing(
    service_name: str = "launchpad-backend",
    service_version: str = "1.0.0",
    otlp_endpoint: Optional[str] = None,
    enable_console: bool = False,
) -> None:
    """
    Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service
        service_version: Version of the service
        otlp_endpoint: OTLP collector endpoint (e.g., "localhost:4317")
        enable_console: Whether to also export to console (for development)
    """
    global _tracer

    # Create resource
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": settings.APP_ENV,
    })

    # Create provider
    provider = TracerProvider(resource=resource)

    # Configure exporters
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    if enable_console or settings.APP_ENV == "development":
        console_exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(console_exporter))

    # Set global provider
    trace.set_tracer_provider(provider)

    # Get tracer
    _tracer = trace.get_tracer(service_name, service_version)


def get_tracer() -> trace.Tracer:
    """Get the global tracer."""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("launchpad-backend")
    return _tracer


def instrument_fastapi(app) -> None:
    """Instrument FastAPI application."""
    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine) -> None:
    """Instrument SQLAlchemy engine."""
    SQLAlchemyInstrumentor().instrument(engine=engine)


def instrument_redis() -> None:
    """Instrument Redis client."""
    RedisInstrumentor().instrument()


def instrument_httpx() -> None:
    """Instrument HTTPX client."""
    HTTPXClientInstrumentor().instrument()


def instrument_celery() -> None:
    """Instrument Celery."""
    CeleryInstrumentor().instrument()


def create_span(
    name: str,
    attributes: Optional[dict] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> trace.Span:
    """
    Create a new span.

    Args:
        name: Span name
        attributes: Span attributes
        kind: Span kind (INTERNAL, SERVER, CLIENT, PRODUCER, CONSUMER)

    Returns:
        Span object
    """
    tracer = get_tracer()
    span = tracer.start_span(name, kind=kind)

    if attributes:
        for key, value in attributes.items():
            span.set_attribute(key, value)

    return span


def trace_function(
    name: Optional[str] = None,
    attributes: Optional[dict] = None,
):
    """
    Decorator to trace a function.

    Usage:
        @trace_function("my_function", {"key": "value"})
        def my_function():
            pass
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            span_name = name or f"{func.__module__}.{func.__qualname__}"
            with trace.get_tracer(__name__).start_as_current_span(span_name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        def sync_wrapper(*args, **kwargs):
            span_name = name or f"{func.__module__}.{func.__qualname__}"
            with trace.get_tracer(__name__).start_as_current_span(span_name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                try:
                    result = func(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def add_span_event(name: str, attributes: Optional[dict] = None) -> None:
    """Add an event to the current span."""
    span = trace.get_current_span()
    if span:
        span.add_event(name, attributes)


def set_span_attribute(key: str, value) -> None:
    """Set an attribute on the current span."""
    span = trace.get_current_span()
    if span:
        span.set_attribute(key, value)


def set_span_error(error: Exception) -> None:
    """Record an error on the current span."""
    span = trace.get_current_span()
    if span:
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.record_exception(error)


def set_span_ok() -> None:
    """Set current span status to OK."""
    span = trace.get_current_span()
    if span:
        span.set_status(StatusCode.OK)


class TracingContext:
    """Context manager for tracing operations."""

    def __init__(
        self,
        name: str,
        attributes: Optional[dict] = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
    ):
        self.name = name
        self.attributes = attributes
        self.kind = kind
        self.span = None

    def __enter__(self):
        self.span = create_span(self.name, self.attributes, self.kind)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.span:
            if exc_type:
                self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
                self.span.record_exception(exc_val)
            else:
                self.span.set_status(StatusCode.OK)
            self.span.end()
        return False


# Convenience context manager
def trace_operation(name: str, attributes: Optional[dict] = None):
    """Create a tracing context manager."""
    return TracingContext(name, attributes)
