from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.permissions import (
    Permission,
    require_permission,
    require_any_permission,
    require_all_permissions,
    user_has_permission,
)
from app.models.user import User


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return current_user


def require_role(*roles: str):
    def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        # "dev" is the highest role (developer/super-user): it passes every role
        # gate in the app, so dev sees all pages. To make a route dev-ONLY,
        # gate it with require_role("dev") — only dev satisfies that.
        if current_user.role == "dev":
            return current_user
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return role_checker


def get_tenant_id(current_user: User = Depends(get_current_active_user)) -> str:
    return str(current_user.tenant_id)


# Pre-built permission dependencies
def require_lead_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.LEAD_READ)(current_user)


def require_lead_write(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.LEAD_UPDATE)(current_user)


def require_agent_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.AGENT_READ)(current_user)


def require_appointment_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.APPOINTMENT_READ)(current_user)


def require_appointment_write(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.APPOINTMENT_UPDATE)(current_user)


def require_analytics_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.ANALYTICS_READ)(current_user)


def require_user_management(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.USER_CREATE)(current_user)


def require_audit_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.AUDIT_READ)(current_user)


def require_compliance_read(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.COMPLIANCE_READ)(current_user)


def require_compliance_manage(current_user: User = Depends(get_current_active_user)) -> User:
    return require_permission(Permission.COMPLIANCE_MANAGE)(current_user)
