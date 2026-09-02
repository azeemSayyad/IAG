"""
Role-Based Access Control (RBAC) with granular permissions.

Role Hierarchy:
  super_admin > tenant_admin > manager > agent

Each role has a set of permissions that determine what actions they can perform.
"""

from enum import Enum
from typing import Set
from functools import wraps

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User


class Permission(str, Enum):
    # Tenant management
    TENANT_CREATE = "tenant:create"
    TENANT_READ = "tenant:read"
    TENANT_UPDATE = "tenant:update"
    TENANT_DELETE = "tenant:delete"

    # User management
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_INVITE = "user:invite"

    # Agent management
    AGENT_CREATE = "agent:create"
    AGENT_READ = "agent:read"
    AGENT_UPDATE = "agent:update"
    AGENT_DELETE = "agent:delete"
    AGENT_SCHEDULE = "agent:schedule"

    # Lead management
    LEAD_CREATE = "lead:create"
    LEAD_READ = "lead:read"
    LEAD_UPDATE = "lead:update"
    LEAD_DELETE = "lead:delete"
    LEAD_EXPORT = "lead:export"
    LEAD_IMPORT = "lead:import"

    # Conversation management
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_MANAGE = "conversation:manage"

    # Appointment management
    APPOINTMENT_CREATE = "appointment:create"
    APPOINTMENT_READ = "appointment:read"
    APPOINTMENT_UPDATE = "appointment:update"
    APPOINTMENT_DELETE = "appointment:delete"
    APPOINTMENT_REASSIGN = "appointment:reassign"

    # Campaign management
    CAMPAIGN_CREATE = "campaign:create"
    CAMPAIGN_READ = "campaign:read"
    CAMPAIGN_UPDATE = "campaign:update"
    CAMPAIGN_DELETE = "campaign:delete"

    # Analytics
    ANALYTICS_READ = "analytics:read"
    ANALYTICS_EXPORT = "analytics:export"

    # AI management
    AI_PROMPT_READ = "ai:prompt:read"
    AI_PROMPT_UPDATE = "ai:prompt:update"
    AI_CONFIG_UPDATE = "ai:config:update"

    # Audit
    AUDIT_READ = "audit:read"

    # Compliance
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_MANAGE = "compliance:manage"


# Role permissions mapping
ROLE_PERMISSIONS: dict[str, Set[Permission]] = {
    "super_admin": {
        # All permissions
        Permission.TENANT_CREATE,
        Permission.TENANT_READ,
        Permission.TENANT_UPDATE,
        Permission.TENANT_DELETE,
        Permission.USER_CREATE,
        Permission.USER_READ,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.USER_INVITE,
        Permission.AGENT_CREATE,
        Permission.AGENT_READ,
        Permission.AGENT_UPDATE,
        Permission.AGENT_DELETE,
        Permission.AGENT_SCHEDULE,
        Permission.LEAD_CREATE,
        Permission.LEAD_READ,
        Permission.LEAD_UPDATE,
        Permission.LEAD_DELETE,
        Permission.LEAD_EXPORT,
        Permission.LEAD_IMPORT,
        Permission.CONVERSATION_READ,
        Permission.CONVERSATION_MANAGE,
        Permission.APPOINTMENT_CREATE,
        Permission.APPOINTMENT_READ,
        Permission.APPOINTMENT_UPDATE,
        Permission.APPOINTMENT_DELETE,
        Permission.APPOINTMENT_REASSIGN,
        Permission.CAMPAIGN_CREATE,
        Permission.CAMPAIGN_READ,
        Permission.CAMPAIGN_UPDATE,
        Permission.CAMPAIGN_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.AI_PROMPT_READ,
        Permission.AI_PROMPT_UPDATE,
        Permission.AI_CONFIG_UPDATE,
        Permission.AUDIT_READ,
        Permission.COMPLIANCE_READ,
        Permission.COMPLIANCE_MANAGE,
    },
    "tenant_admin": {
        Permission.USER_CREATE,
        Permission.USER_READ,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.USER_INVITE,
        Permission.AGENT_CREATE,
        Permission.AGENT_READ,
        Permission.AGENT_UPDATE,
        Permission.AGENT_DELETE,
        Permission.AGENT_SCHEDULE,
        Permission.LEAD_CREATE,
        Permission.LEAD_READ,
        Permission.LEAD_UPDATE,
        Permission.LEAD_DELETE,
        Permission.LEAD_EXPORT,
        Permission.LEAD_IMPORT,
        Permission.CONVERSATION_READ,
        Permission.CONVERSATION_MANAGE,
        Permission.APPOINTMENT_CREATE,
        Permission.APPOINTMENT_READ,
        Permission.APPOINTMENT_UPDATE,
        Permission.APPOINTMENT_DELETE,
        Permission.APPOINTMENT_REASSIGN,
        Permission.CAMPAIGN_CREATE,
        Permission.CAMPAIGN_READ,
        Permission.CAMPAIGN_UPDATE,
        Permission.CAMPAIGN_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.AI_PROMPT_READ,
        Permission.AI_PROMPT_UPDATE,
        Permission.AUDIT_READ,
        Permission.COMPLIANCE_READ,
        Permission.COMPLIANCE_MANAGE,
    },
    "manager": {
        Permission.AGENT_READ,
        Permission.AGENT_SCHEDULE,
        Permission.LEAD_CREATE,
        Permission.LEAD_READ,
        Permission.LEAD_UPDATE,
        Permission.LEAD_EXPORT,
        Permission.CONVERSATION_READ,
        Permission.APPOINTMENT_CREATE,
        Permission.APPOINTMENT_READ,
        Permission.APPOINTMENT_UPDATE,
        Permission.APPOINTMENT_REASSIGN,
        Permission.CAMPAIGN_READ,
        Permission.ANALYTICS_READ,
        Permission.COMPLIANCE_READ,
    },
    "agent": {
        Permission.AGENT_READ,
        Permission.LEAD_READ,
        Permission.CONVERSATION_READ,
        Permission.APPOINTMENT_READ,
        Permission.APPOINTMENT_UPDATE,
        Permission.COMPLIANCE_READ,
    },
}

# The frontend recognizes 'lead' (Team Leader) and 'head' (Head Manager) for
# display/routing, but the RBAC table above did not — so those logins would get
# an empty permission set (403 everywhere). Grant them functional permissions:
# a Team Leader operates at manager level; a Head Manager gets a bit more.
ROLE_PERMISSIONS["lead"] = set(ROLE_PERMISSIONS["manager"])
ROLE_PERMISSIONS["head"] = set(ROLE_PERMISSIONS["manager"]) | {
    Permission.AGENT_CREATE,
    Permission.AGENT_UPDATE,
}

# "dev" is the developer/super-user role: full access to every page and action.
# It gets ALL permissions (even ones added later) so no permission gate blocks it.
ROLE_PERMISSIONS["dev"] = set(Permission)


def get_permissions_for_role(role: str) -> Set[Permission]:
    """Get the set of permissions for a given role."""
    return ROLE_PERMISSIONS.get(role, set())


def user_has_permission(user: User, permission: Permission) -> bool:
    """Check if a user has a specific permission."""
    user_permissions = get_permissions_for_role(user.role)
    return permission in user_permissions


def require_role(role: str):
    """Dependency factory that checks if the current user has a specific role."""
    def role_checker(current_user: User) -> User:
        # "dev" (developer/super-user) satisfies every role gate.
        if current_user.role != role and current_user.role != "dev":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {role}",
            )
        return current_user
    return role_checker


def require_permission(permission: Permission):
    """Dependency factory that checks if the current user has a specific permission."""
    def permission_checker(current_user: User) -> User:
        if not user_has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission.value}",
            )
        return current_user
    return permission_checker


def require_any_permission(*permissions: Permission):
    """Check if user has ANY of the specified permissions."""
    def permission_checker(current_user: User) -> User:
        user_permissions = get_permissions_for_role(current_user.role)
        if not any(p in user_permissions for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions: {[p.value for p in permissions]}",
            )
        return current_user
    return permission_checker


def require_all_permissions(*permissions: Permission):
    """Check if user has ALL of the specified permissions."""
    def permission_checker(current_user: User) -> User:
        user_permissions = get_permissions_for_role(current_user.role)
        missing = [p.value for p in permissions if p not in user_permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions: {missing}",
            )
        return current_user
    return permission_checker
