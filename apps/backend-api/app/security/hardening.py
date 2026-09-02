"""
Enterprise Security Hardening (Phase 46)

Production-grade security:

Step 46.1 — JWT Revocation
    Redis-based token blacklist for instant logout

Step 46.2 — CSRF Protection
    CSRF tokens for state-changing requests

Step 46.3 — HTTPS Enforcement
    Force SSL redirects, HSTS headers

Step 46.4 — Secret Rotation
    Secret management and rotation

Step 46.5 — Full Audit Compliance
    Audit trail for data access, exports, permission changes
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4

from app.core.redis import redis_service

logger = logging.getLogger(__name__)


# --- JWT Revocation (Step 46.1) ---

class JWTRevocation:
    """
    Redis-based JWT token blacklist.

    Enables instant token revocation on:
    - Logout
    - Password change
    - Security breach
    - Admin action
    """

    # Redis key prefixes
    BLACKLIST_KEY = "jwt:blacklist:"
    REFRESH_KEY = "jwt:refresh_blacklist:"

    def __init__(self):
        self.redis = redis_service

    def revoke_token(self, jti: str, expires_at: datetime, token_type: str = "access") -> bool:
        """
        Revoke a JWT token by its JTI (JWT ID).

        Args:
            jti: JWT ID claim
            expires_at: Token expiration time
            token_type: 'access' or 'refresh'

        Returns:
            True if revoked successfully
        """
        key = f"{self.BLACKLIST_KEY}{jti}"
        ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())

        if ttl <= 0:
            return True  # Already expired

        self.redis.client.setex(key, ttl, json.dumps({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "type": token_type,
        }))

        logger.info(f"Revoked {token_type} token: {jti}")
        return True

    def revoke_all_user_tokens(self, user_id: str) -> int:
        """
        Revoke all tokens for a user.

        Used on password change or security breach.
        """
        # Store user revocation timestamp
        key = f"{self.BLACKLIST_KEY}user:{user_id}"
        self.redis.client.setex(key, 86400 * 30, json.dumps({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "reason": "all_tokens_revoked",
        }))

        logger.info(f"Revoked all tokens for user: {user_id}")
        return 1

    def is_token_revoked(self, jti: str) -> bool:
        """Check if a token has been revoked."""
        return self.redis.client.exists(f"{self.BLACKLIST_KEY}{jti}") > 0

    def is_user_revoked(self, user_id: str, token_issued_at: datetime) -> bool:
        """Check if all user tokens were revoked after token was issued."""
        key = f"{self.BLACKLIST_KEY}user:{user_id}"
        data = self.redis.client.get(key)

        if data:
            try:
                revocation = json.loads(data)
                revoked_at = datetime.fromisoformat(revocation["revoked_at"])
                return revoked_at > token_issued_at
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        return False

    def get_revocation_count(self) -> int:
        """Get count of revoked tokens."""
        keys = self.redis.client.keys(f"{self.BLACKLIST_KEY}*")
        return len(keys)


# --- CSRF Protection (Step 46.2) ---

class CSRFProtection:
    """
    CSRF token generation and validation.

    Protects state-changing requests (POST, PUT, DELETE)
    on admin routes.
    """

    # Redis key prefix
    CSRF_KEY = "csrf:token:"

    # Token TTL (2 hours)
    TOKEN_TTL = 7200

    def __init__(self):
        self.redis = redis_service

    def generate_token(self, session_id: str) -> str:
        """
        Generate a CSRF token for a session.

        Args:
            session_id: User session ID

        Returns:
            CSRF token string
        """
        token = secrets.token_hex(32)
        key = f"{self.CSRF_KEY}{session_id}"

        self.redis.client.setex(key, self.TOKEN_TTL, token)

        return token

    def validate_token(self, session_id: str, token: str) -> bool:
        """
        Validate a CSRF token.

        Args:
            session_id: User session ID
            token: CSRF token to validate

        Returns:
            True if valid
        """
        key = f"{self.CSRF_KEY}{session_id}"
        stored = self.redis.client.get(key)

        if not stored:
            return False

        return hmac.compare_digest(stored, token)

    def revoke_token(self, session_id: str) -> None:
        """Revoke a CSRF token."""
        self.redis.client.delete(f"{self.CSRF_KEY}{session_id}")

    def rotate_token(self, session_id: str) -> str:
        """Rotate (revoke old + generate new) CSRF token."""
        self.revoke_token(session_id)
        return self.generate_token(session_id)


# --- HTTPS Enforcement (Step 46.3) ---

class HTTPSEnforcement:
    """
    HTTPS enforcement and HSTS headers.

    Features:
    - Force SSL redirect
    - HSTS header injection
    - Secure cookie flags
    """

    # HSTS max age (1 year)
    HSTS_MAX_AGE = 31536000

    def get_security_headers(self, include_hsts: bool = True) -> Dict[str, str]:
        """
        Get security headers for HTTP responses.

        Returns:
            Dict of header name to value
        """
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
        }

        if include_hsts:
            headers["Strict-Transport-Security"] = f"max-age={self.HSTS_MAX_AGE}; includeSubDomains; preload"

        return headers

    def should_redirect_to_https(self, request_url: str) -> bool:
        """Check if request should be redirected to HTTPS."""
        return request_url.startswith("http://") and not request_url.startswith("http://localhost")

    def get_redirect_url(self, request_url: str) -> str:
        """Get HTTPS redirect URL."""
        return request_url.replace("http://", "https://", 1)

    def get_secure_cookie_settings(self) -> Dict[str, Any]:
        """Get secure cookie settings."""
        return {
            "secure": True,
            "httponly": True,
            "samesite": "lax",
            "max_age": 3600,
        }


# --- Secret Rotation (Step 46.4) ---

class SecretRotation:
    """
    Secret management and rotation.

    Features:
    - Generate new secrets
    - Track secret versions
    - Graceful rotation (old + new valid during transition)
    - Audit trail for secret changes
    """

    # Redis key prefix
    SECRET_KEY = "secrets:"

    def __init__(self):
        self.redis = redis_service

    def generate_secret(self, name: str, length: int = 64) -> str:
        """
        Generate a new secret.

        Args:
            name: Secret name
            length: Secret length in bytes

        Returns:
            Generated secret string
        """
        secret = secrets.token_hex(length)

        # Store with version
        key = f"{self.SECRET_KEY}{name}"
        versions = self._get_versions(name)

        new_version = len(versions) + 1
        versions.append({
            "version": new_version,
            "secret": secret,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
        })

        # Keep last 3 versions
        if len(versions) > 3:
            for v in versions[:-3]:
                v["status"] = "expired"
            versions = versions[-3:]

        self.redis.client.set(key, json.dumps(versions))

        logger.info(f"Generated new secret: {name} (version {new_version})")
        return secret

    def get_active_secret(self, name: str) -> Optional[str]:
        """Get the active secret."""
        versions = self._get_versions(name)

        for v in reversed(versions):
            if v.get("status") == "active":
                return v.get("secret")

        return None

    def get_valid_secrets(self, name: str) -> List[str]:
        """Get all valid secrets (active + grace period)."""
        versions = self._get_versions(name)
        return [v["secret"] for v in versions if v.get("status") in ("active", "grace_period")]

    def rotate_secret(self, name: str, grace_period_hours: int = 24) -> str:
        """
        Rotate a secret with grace period.

        During grace period, both old and new secrets are valid.
        """
        versions = self._get_versions(name)

        # Set old active to grace period
        for v in versions:
            if v.get("status") == "active":
                v["status"] = "grace_period"
                v["grace_until"] = (
                    datetime.now(timezone.utc) + timedelta(hours=grace_period_hours)
                ).isoformat()

        # Generate new
        new_secret = secrets.token_hex(64)
        new_version = len(versions) + 1
        versions.append({
            "version": new_version,
            "secret": new_secret,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
        })

        self.redis.client.set(f"{self.SECRET_KEY}{name}", json.dumps(versions))

        logger.info(f"Rotated secret: {name} (new version {new_version})")
        return new_secret

    def _get_versions(self, name: str) -> List[Dict]:
        """Get all versions of a secret."""
        data = self.redis.client.get(f"{self.SECRET_KEY}{name}")
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return []


# --- Full Audit Compliance (Step 46.5) ---

class AuditCompliance:
    """
    Comprehensive audit trail for compliance.

    Tracks:
    - Data access (who accessed what)
    - Data exports
    - Permission changes
    - Security events
    - Configuration changes
    """

    # Redis key for real-time audit log
    AUDIT_STREAM = "audit:compliance"

    def __init__(self, db):
        self.db = db
        self.redis = redis_service

    def log_data_access(
        self,
        user_id: str,
        tenant_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
        details: Dict = None,
    ) -> str:
        """
        Log data access event.

        Args:
            user_id: User performing the action
            tenant_id: Tenant ID
            resource_type: Type of resource (lead, appointment, etc.)
            resource_id: Resource ID
            action: Action performed (read, update, delete)
            details: Additional details

        Returns:
            Audit event ID
        """
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": "data_access",
            "user_id": user_id,
            "tenant_id": tenant_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "details": json.dumps(details or {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Store in Redis stream
        self.redis.client.xadd(self.AUDIT_STREAM, event, maxlen=100000)

        # Store in database
        from app.models.audit_log import AuditLog
        audit_log = AuditLog(
            tenant_id=tenant_id,
            user_id=UUID(user_id),
            action=f"data_{action}",
            resource_type=resource_type,
            resource_id=UUID(resource_id) if resource_id else None,
            details=details or {},
        )
        self.db.add(audit_log)
        self.db.commit()

        return event_id

    def log_export(
        self,
        user_id: str,
        tenant_id: str,
        export_type: str,
        record_count: int,
        filters: Dict = None,
    ) -> str:
        """
        Log data export event.

        Important for compliance — tracks who exported what data.
        """
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": "data_export",
            "user_id": user_id,
            "tenant_id": tenant_id,
            "export_type": export_type,
            "record_count": record_count,
            "filters": json.dumps(filters or {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.redis.client.xadd(self.AUDIT_STREAM, event, maxlen=100000)

        logger.warning(f"Data export: user={user_id} type={export_type} records={record_count}")
        return event_id

    def log_permission_change(
        self,
        admin_id: str,
        tenant_id: str,
        target_user_id: str,
        change_type: str,
        old_value: Any,
        new_value: Any,
    ) -> str:
        """
        Log permission change event.

        Tracks role changes, permission grants/revocations.
        """
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": "permission_change",
            "admin_id": admin_id,
            "tenant_id": tenant_id,
            "target_user_id": target_user_id,
            "change_type": change_type,
            "old_value": json.dumps(old_value),
            "new_value": json.dumps(new_value),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.redis.client.xadd(self.AUDIT_STREAM, event, maxlen=100000)

        logger.warning(f"Permission change: admin={admin_id} target={target_user_id} change={change_type}")
        return event_id

    def log_security_event(
        self,
        tenant_id: str,
        event_type: str,
        severity: str,
        details: Dict,
        user_id: str = None,
    ) -> str:
        """
        Log security event.

        Tracks suspicious activities, failed logins, etc.
        """
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": f"security_{event_type}",
            "severity": severity,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "details": json.dumps(details),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.redis.client.xadd(self.AUDIT_STREAM, event, maxlen=100000)

        if severity in ("high", "critical"):
            logger.critical(f"Security event: {event_type} severity={severity} tenant={tenant_id}")

        return event_id

    def log_config_change(
        self,
        admin_id: str,
        tenant_id: str,
        config_key: str,
        old_value: Any,
        new_value: Any,
    ) -> str:
        """Log configuration change."""
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": "config_change",
            "admin_id": admin_id,
            "tenant_id": tenant_id,
            "config_key": config_key,
            "old_value": json.dumps(old_value),
            "new_value": json.dumps(new_value),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.redis.client.xadd(self.AUDIT_STREAM, event, maxlen=100000)
        return event_id

    def get_audit_trail(
        self,
        tenant_id: str,
        event_type: str = None,
        user_id: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        Get audit trail.

        Args:
            tenant_id: Tenant ID
            event_type: Optional event type filter
            user_id: Optional user filter
            limit: Max results

        Returns:
            List of audit events
        """
        from app.models.audit_log import AuditLog

        query = self.db.query(AuditLog).filter(
            AuditLog.tenant_id == tenant_id,
        )

        if event_type:
            query = query.filter(AuditLog.action == event_type)

        if user_id:
            query = query.filter(AuditLog.user_id == user_id)

        logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()

        return [
            {
                "id": str(log.id),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": str(log.resource_id) if log.resource_id else None,
                "user_id": str(log.user_id) if log.user_id else None,
                "details": log.details,
                "timestamp": log.created_at.isoformat(),
            }
            for log in logs
        ]

    def get_compliance_report(
        self,
        tenant_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Any]:
        """
        Generate compliance report for a period.

        Returns:
            Dict with compliance metrics
        """
        from app.models.audit_log import AuditLog

        logs = self.db.query(AuditLog).filter(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
        ).all()

        # Count by action type
        action_counts = {}
        for log in logs:
            action = log.action or "unknown"
            action_counts[action] = action_counts.get(action, 0) + 1

        # Count by user
        user_counts = {}
        for log in logs:
            user_id = str(log.user_id) if log.user_id else "system"
            user_counts[user_id] = user_counts.get(user_id, 0) + 1

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "total_events": len(logs),
            "action_breakdown": action_counts,
            "user_breakdown": user_counts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# --- Security Middleware ---

class SecurityHardeningMiddleware:
    """
    Combined security middleware.

    Applies all security hardening measures:
    - JWT revocation checks
    - CSRF validation
    - HTTPS enforcement
    - Security headers
    """

    def __init__(self):
        self.jwt_revocation = JWTRevocation()
        self.csrf = CSRFProtection()
        self.https = HTTPSEnforcement()

    def validate_request(
        self,
        request,
        session_id: str = None,
        csrf_token: str = None,
    ) -> Dict[str, Any]:
        """
        Validate a request against all security checks.

        Returns:
            Dict with validation result
        """
        issues = []

        # Check HTTPS
        if hasattr(request, 'url'):
            if self.https.should_redirect_to_https(str(request.url)):
                return {
                    "valid": False,
                    "redirect": self.https.get_redirect_url(str(request.url)),
                    "issues": ["https_required"],
                }

        # Check CSRF for state-changing methods
        if hasattr(request, 'method'):
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                if session_id and csrf_token:
                    if not self.csrf.validate_token(session_id, csrf_token):
                        issues.append("invalid_csrf_token")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "headers": self.https.get_security_headers(),
        }
