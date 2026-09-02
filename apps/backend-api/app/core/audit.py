"""
Audit logging service.

Tracks all important actions in the system for compliance and debugging.
"""

from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.core.database import SessionLocal


def log_audit_event(
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    db: Session = None,
) -> AuditLog:
    """
    Log an audit event.

    Args:
        tenant_id: The tenant this action belongs to
        action: The action performed (e.g., "login", "create", "update", "delete")
        resource_type: The type of resource (e.g., "user", "lead", "appointment")
        resource_id: The ID of the specific resource
        user_id: The user who performed the action
        details: Additional context about the action
        ip_address: Client IP address
        user_agent: Client user agent string
        db: Database session (creates new one if not provided)
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        audit_log = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(audit_log)
        db.commit()
        return audit_log
    finally:
        if should_close:
            db.close()


# Convenience functions for common audit events
def log_login(tenant_id: str, user_id: str, ip_address: str = None, user_agent: str = None):
    """Log a user login event."""
    return log_audit_event(
        tenant_id=tenant_id,
        action="login",
        resource_type="user",
        resource_id=user_id,
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_logout(tenant_id: str, user_id: str, ip_address: str = None):
    """Log a user logout event."""
    return log_audit_event(
        tenant_id=tenant_id,
        action="logout",
        resource_type="user",
        resource_id=user_id,
        user_id=user_id,
        ip_address=ip_address,
    )


def log_create(tenant_id: str, user_id: str, resource_type: str, resource_id: str, details: dict = None):
    """Log a resource creation event."""
    return log_audit_event(
        tenant_id=tenant_id,
        action="create",
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        details=details,
    )


def log_update(tenant_id: str, user_id: str, resource_type: str, resource_id: str, details: dict = None):
    """Log a resource update event."""
    return log_audit_event(
        tenant_id=tenant_id,
        action="update",
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        details=details,
    )


def log_delete(tenant_id: str, user_id: str, resource_type: str, resource_id: str, details: dict = None):
    """Log a resource deletion event."""
    return log_audit_event(
        tenant_id=tenant_id,
        action="delete",
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        details=details,
    )


def log_ai_action(tenant_id: str, action: str, resource_type: str, resource_id: str, details: dict = None):
    """Log an AI system action."""
    return log_audit_event(
        tenant_id=tenant_id,
        action=f"ai_{action}",
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
    )
