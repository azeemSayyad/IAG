"""
Tenant isolation middleware and utilities.

Ensures all database queries are scoped to the current tenant.
"""

from contextvars import ContextVar
from typing import Optional
from uuid import UUID

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# Context variable to hold the current tenant_id for the request
_current_tenant_id: ContextVar[Optional[str]] = ContextVar("current_tenant_id", default=None)


def get_current_tenant_id() -> Optional[str]:
    """Get the current tenant_id from context."""
    return _current_tenant_id.get()


def set_current_tenant_id(tenant_id: str) -> None:
    """Set the current tenant_id in context.

    NOTE: we deliberately do NOT open a separate DB connection here to issue a
    `SET app.current_tenant_id`. That SET ran on a throwaway pooled connection,
    not the one the request's queries use, so it never actually scoped any
    query — it only burned an extra connection per request, which (multiplied
    across concurrent requests + every Celery worker) exhausted the Postgres
    connection limit and caused intermittent 500s. Tenant isolation is enforced
    at the application layer (every query filters by tenant_id).
    """
    _current_tenant_id.set(tenant_id)


class TenantIsolationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that extracts tenant_id from the JWT token and sets it in context.
    This ensures all downstream code can access the tenant_id.
    """

    # Paths that don't require tenant isolation
    PUBLIC_PATHS = {
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/auth/password-reset-request",
        "/api/v1/auth/password-reset-confirm",
        # Public hiree onboarding form (hosted separately, no login)
        "/api/v1/onboarding/submit",
        "/api/v1/onboarding/upload",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip tenant isolation for public paths
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        # Extract tenant_id from the request state (set by auth middleware)
        # The auth dependency will set request.state.tenant_id
        if hasattr(request.state, "tenant_id") and request.state.tenant_id:
            set_current_tenant_id(str(request.state.tenant_id))

        response = await call_next(request)
        return response


class TenantQueryMixin:
    """
    Mixin for SQLAlchemy queries that automatically filters by tenant_id.
    """

    @classmethod
    def tenant_query(cls, db_session, tenant_id: str = None):
        """Create a query filtered by tenant_id."""
        if tenant_id is None:
            tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise ValueError("No tenant_id in context. Ensure TenantIsolationMiddleware is active.")
        return db_session.query(cls).filter(cls.tenant_id == tenant_id)


def validate_tenant_access(resource_tenant_id: UUID, current_tenant_id: str) -> bool:
    """
    Validate that a resource belongs to the current tenant.
    Raises HTTPException if access is denied.
    """
    from fastapi import HTTPException, status

    if str(resource_tenant_id) != current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: resource belongs to a different tenant",
        )
    return True
