"""
Security Router

Endpoints:
- GET /security/suppression — Get suppression list
- POST /security/suppression — Add to suppression list
- DELETE /security/suppression — Remove from suppression list
- POST /security/consent — Record consent
- GET /security/consent/{lead_id} — Check consent
- POST /security/opt-out — Handle opt-out
- GET /security/audit/summary — Audit summary
- GET /security/audit/security — Security events
- GET /security/audit/compliance — Compliance report
- GET /security/rate-limit/status — Rate limit status
"""

from typing import Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user, require_role
from app.models.user import User
from app.security.enhanced_audit import (
    get_audit_summary,
    get_security_events,
    get_compliance_report,
)
from app.security.rate_limiting import get_rate_limit_status

router = APIRouter(prefix="/security", tags=["security"])


class ComplianceReportRequest(BaseModel):
    start_date: datetime
    end_date: datetime


@router.get("/audit/summary")
def audit_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Get audit summary."""
    return get_audit_summary(db, tenant_id, days)


@router.get("/audit/security")
def security_events(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Get security events."""
    events = get_security_events(db, tenant_id, days)
    return {"events": events, "total": len(events)}


@router.post("/audit/compliance")
def compliance_report(
    request: ComplianceReportRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Generate compliance report."""
    return get_compliance_report(db, tenant_id, request.start_date, request.end_date)


@router.get("/rate-limit/status")
def rate_limit_status(
    limit_type: str = Query("api_general"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get rate limit status."""
    identifier = str(current_user.id)
    return get_rate_limit_status(limit_type, identifier)
