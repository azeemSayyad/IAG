"""
Follow-Up Router

Endpoints:
- POST /followup/no-reply/process — Process no-reply follow-ups
- POST /followup/missed/process — Process missed appointment follow-ups
- POST /followup/nurture/process — Process nurture campaigns
- GET /followup/no-reply/status/{lead_id} — Get no-reply status
- GET /followup/missed/status/{appointment_id} — Get missed appointment status
- GET /followup/nurture/status/{lead_id} — Get nurture status
- POST /followup/nurture/move — Move lead to nurture
- POST /followup/nurture/re-engage — Re-engage a nurtured lead
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.followup.services.no_reply import (
    process_all_no_reply_leads,
    get_no_reply_status,
)
from app.followup.services.missed_appointment import (
    process_all_missed_appointments,
    get_missed_appointment_status,
    mark_as_no_show,
)
from app.followup.services.nurture import (
    process_all_nurture_leads,
    get_nurture_status,
    move_to_nurture,
    re_engage_lead,
)

router = APIRouter(prefix="/followup", tags=["followup"])


class MoveToNurtureRequest(BaseModel):
    lead_id: UUID


class ReEngageRequest(BaseModel):
    lead_id: UUID
    reason: str = None


class MarkNoShowRequest(BaseModel):
    appointment_id: UUID


@router.post("/no-reply/process")
def process_no_reply(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process all no-reply follow-ups."""
    result = process_all_no_reply_leads(db, tenant_id)
    return result


@router.post("/missed/process")
def process_missed(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process all missed appointment follow-ups."""
    result = process_all_missed_appointments(db, tenant_id)
    return result


@router.post("/nurture/process")
def process_nurture(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Process all nurture campaigns."""
    result = process_all_nurture_leads(db, tenant_id)
    return result


@router.get("/no-reply/status/{lead_id}")
def no_reply_status(
    lead_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Get no-reply follow-up status for a lead."""
    return get_no_reply_status(str(lead_id))


@router.get("/missed/status/{appointment_id}")
def missed_status(
    appointment_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Get missed appointment follow-up status."""
    return get_missed_appointment_status(str(appointment_id))


@router.get("/nurture/status/{lead_id}")
def nurture_status(
    lead_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Get nurture campaign status for a lead."""
    return get_nurture_status(str(lead_id))


@router.post("/nurture/move")
def move_to_nurture_endpoint(
    request: MoveToNurtureRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Move a lead to nurture campaign."""
    result = move_to_nurture(db, request.lead_id, tenant_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/nurture/re-engage")
def re_engage_endpoint(
    request: ReEngageRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Re-engage a nurtured lead."""
    result = re_engage_lead(db, request.lead_id, tenant_id, request.reason)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/missed/mark-no-show")
def mark_no_show_endpoint(
    request: MarkNoShowRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Mark an appointment as no-show."""
    result = mark_as_no_show(db, request.appointment_id, tenant_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
