"""
Enhanced Audit Logging (Step 12.4)

Tracks:
- Admin actions
- AI actions
- Booking edits
- Security events
- Data access

Provides:
- Detailed context for each event
- Compliance reporting
- Forensic analysis support
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.core.audit import log_audit_event


# Audit event categories
class AuditCategory:
    AUTH = "auth"
    LEAD = "lead"
    APPOINTMENT = "appointment"
    CAMPAIGN = "campaign"
    AGENT = "agent"
    AI = "ai"
    SECURITY = "security"
    ADMIN = "admin"
    DATA = "data"


# Specific audit events
AUDIT_EVENTS = {
    # Auth events
    "login_success": {"category": AuditCategory.AUTH, "severity": "info"},
    "login_failed": {"category": AuditCategory.AUTH, "severity": "warning"},
    "login_locked": {"category": AuditCategory.AUTH, "severity": "warning"},
    "logout": {"category": AuditCategory.AUTH, "severity": "info"},
    "password_changed": {"category": AuditCategory.AUTH, "severity": "info"},
    "password_reset_requested": {"category": AuditCategory.AUTH, "severity": "info"},
    "password_reset_completed": {"category": AuditCategory.AUTH, "severity": "info"},

    # Lead events
    "lead_created": {"category": AuditCategory.LEAD, "severity": "info"},
    "lead_updated": {"category": AuditCategory.LEAD, "severity": "info"},
    "lead_deleted": {"category": AuditCategory.LEAD, "severity": "warning"},
    "lead_imported": {"category": AuditCategory.LEAD, "severity": "info"},
    "lead_exported": {"category": AuditCategory.DATA, "severity": "info"},

    # Appointment events
    "appointment_created": {"category": AuditCategory.APPOINTMENT, "severity": "info"},
    "appointment_updated": {"category": AuditCategory.APPOINTMENT, "severity": "info"},
    "appointment_cancelled": {"category": AuditCategory.APPOINTMENT, "severity": "warning"},
    "appointment_rescheduled": {"category": AuditCategory.APPOINTMENT, "severity": "info"},
    "appointment_completed": {"category": AuditCategory.APPOINTMENT, "severity": "info"},
    "disposition_set": {"category": AuditCategory.APPOINTMENT, "severity": "info"},

    # Campaign events
    "campaign_created": {"category": AuditCategory.CAMPAIGN, "severity": "info"},
    "campaign_updated": {"category": AuditCategory.CAMPAIGN, "severity": "info"},
    "campaign_deleted": {"category": AuditCategory.CAMPAIGN, "severity": "warning"},
    "campaign_paused": {"category": AuditCategory.CAMPAIGN, "severity": "info"},

    # Agent events
    "agent_created": {"category": AuditCategory.AGENT, "severity": "info"},
    "agent_updated": {"category": AuditCategory.AGENT, "severity": "info"},
    "agent_deactivated": {"category": AuditCategory.AGENT, "severity": "warning"},

    # AI events
    "ai_outreach_sent": {"category": AuditCategory.AI, "severity": "info"},
    "ai_response_generated": {"category": AuditCategory.AI, "severity": "info"},
    "ai_intent_detected": {"category": AuditCategory.AI, "severity": "debug"},
    "ai_objection_handled": {"category": AuditCategory.AI, "severity": "info"},
    "ai_workflow_triggered": {"category": AuditCategory.AI, "severity": "info"},

    # Security events
    "rate_limit_exceeded": {"category": AuditCategory.SECURITY, "severity": "warning"},
    "suspicious_activity": {"category": AuditCategory.SECURITY, "severity": "warning"},
    "unauthorized_access": {"category": AuditCategory.SECURITY, "severity": "critical"},
    "data_breach_attempt": {"category": AuditCategory.SECURITY, "severity": "critical"},

    # Admin events
    "user_created": {"category": AuditCategory.ADMIN, "severity": "info"},
    "user_updated": {"category": AuditCategory.ADMIN, "severity": "info"},
    "user_deactivated": {"category": AuditCategory.ADMIN, "severity": "warning"},
    "settings_changed": {"category": AuditCategory.ADMIN, "severity": "info"},
    "billing_updated": {"category": AuditCategory.ADMIN, "severity": "info"},

    # Data events
    "data_exported": {"category": AuditCategory.DATA, "severity": "info"},
    "data_imported": {"category": AuditCategory.DATA, "severity": "info"},
    "pii_accessed": {"category": AuditCategory.DATA, "severity": "info"},
}


def log_enhanced_audit(
    db: Session,
    tenant_id: str,
    event: str,
    user_id: str = None,
    resource_type: str = None,
    resource_id: str = None,
    details: Dict = None,
    ip_address: str = None,
    user_agent: str = None,
) -> AuditLog:
    """
    Log an enhanced audit event with full context.
    """
    event_config = AUDIT_EVENTS.get(event, {
        "category": "unknown",
        "severity": "info",
    })

    enhanced_details = {
        **(details or {}),
        "event": event,
        "category": event_config["category"],
        "severity": event_config["severity"],
    }

    return log_audit_event(
        tenant_id=tenant_id,
        action=event,
        resource_type=resource_type or event_config["category"],
        resource_id=resource_id,
        user_id=user_id,
        details=enhanced_details,
        ip_address=ip_address,
        user_agent=user_agent,
        db=db,
    )


def get_audit_summary(
    db: Session,
    tenant_id: str,
    days: int = 30,
) -> Dict:
    """
    Get a summary of audit events.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= cutoff,
        )
        .all()
    )

    # Count by category
    category_counts = {}
    severity_counts = {"info": 0, "warning": 0, "critical": 0, "debug": 0}

    for log in logs:
        details = log.details or {}
        category = details.get("category", "unknown")
        severity = details.get("severity", "info")

        category_counts[category] = category_counts.get(category, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    return {
        "period_days": days,
        "total_events": len(logs),
        "by_category": category_counts,
        "by_severity": severity_counts,
    }


def get_security_events(
    db: Session,
    tenant_id: str,
    days: int = 7,
) -> List[Dict]:
    """
    Get security-related audit events.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= cutoff,
        )
        .all()
    )

    security_events = []
    for log in logs:
        details = log.details or {}
        if details.get("category") == AuditCategory.SECURITY:
            security_events.append({
                "id": str(log.id),
                "event": log.action,
                "severity": details.get("severity", "unknown"),
                "details": details,
                "user_id": str(log.user_id) if log.user_id else None,
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat(),
            })

    return security_events


def get_compliance_report(
    db: Session,
    tenant_id: str,
    start_date: datetime,
    end_date: datetime,
) -> Dict:
    """
    Generate a compliance report for a date range.
    """
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= start_date,
            AuditLog.created_at < end_date,
        )
        .all()
    )

    # Count events
    total_events = len(logs)
    ai_events = sum(1 for l in logs if (l.details or {}).get("category") == AuditCategory.AI)
    security_events = sum(1 for l in logs if (l.details or {}).get("category") == AuditCategory.SECURITY)
    data_events = sum(1 for l in logs if (l.details or {}).get("category") == AuditCategory.DATA)

    # Count critical events
    critical_events = sum(1 for l in logs if (l.details or {}).get("severity") == "critical")

    return {
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "summary": {
            "total_events": total_events,
            "ai_events": ai_events,
            "security_events": security_events,
            "data_events": data_events,
            "critical_events": critical_events,
        },
        "compliance_status": "compliant" if critical_events == 0 else "attention_required",
    }
