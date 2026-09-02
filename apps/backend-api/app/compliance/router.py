from datetime import date
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.compliance import services
from app.core.audit import log_audit_event
from app.core.database import get_db
from app.core.date_ranges import resolve_range, APPROVED_STATUSES
from app.core.deps import get_current_active_user, get_tenant_id, require_compliance_manage, require_compliance_read
from app.models.agent import Agent
from app.models.compliance import (
    AgentCarrierAppointment,
    AgentStateLicense,
    ComplianceEvent,
    Deal,
    DealApprovalLog,
    DealRecording,
)
from app.models.user import User
from app.schemas.compliance import (
    ApprovalDecisionResponse,
    CarrierAppointmentCreate,
    CarrierAppointmentResponse,
    CarrierAppointmentUpdate,
    ComplianceAnalyticsResponse,
    ComplianceDashboardResponse,
    ComplianceEventCreate,
    ComplianceEventResponse,
    DealApprovalLogResponse,
    DealRevalidateRequest,
    DealResponse,
    DealSubmitRequest,
    NpnUpdate,
    StateLicenseCreate,
    StateLicenseResponse,
    StateLicenseUpdate,
)
from app.realtime.websocket import emit_to_tenant
from app.notifications.service import create_notification, notify_compliance_admins, mark_resource_notifications_read


router = APIRouter(prefix="/compliance", tags=["compliance"])


def _agent_display_name(db: Session, agent: Agent) -> str:
    """Agent's user name (falls back to email / id) for notification copy."""
    user = db.query(User).filter(User.id == agent.user_id).first()
    if user:
        name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        return name or (user.email or str(agent.id))
    return str(agent.id)


def _ensure_agent(db: Session, tenant_id: str, agent_id: UUID) -> Agent:
    agent = db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _current_agent(db: Session, current_user: User) -> Optional[Agent]:
    return db.query(Agent).filter(Agent.tenant_id == current_user.tenant_id, Agent.user_id == current_user.id).first()


def _agent_scope_filter(query, model, db: Session, current_user: User):
    if current_user.role != "agent":
        return query
    agent = _current_agent(db, current_user)
    if not agent:
        raise HTTPException(status_code=403, detail="No agent profile is linked to this user")
    return query.filter(model.agent_id == agent.id)


def _ensure_agent_scope(db: Session, current_user: User, agent_id: UUID) -> None:
    if current_user.role != "agent":
        return
    agent = _current_agent(db, current_user)
    if not agent or agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can only access their own compliance data")


@router.get("/agents", response_model=dict)
def list_compliance_agents(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    from sqlalchemy import func
    agents = db.query(Agent).filter(Agent.tenant_id == tenant_id).all()
    if current_user.role == "agent":
        own = _current_agent(db, current_user)
        agents = [own] if own else []
    # License counts per agent in this tenant (one grouped query, no N+1).
    license_counts: dict = {}
    for aid, cnt in (
        db.query(AgentStateLicense.agent_id, func.count(AgentStateLicense.id))
        .filter(AgentStateLicense.tenant_id == tenant_id)
        .group_by(AgentStateLicense.agent_id)
        .all()
    ):
        license_counts[aid] = int(cnt or 0)
    items = []
    for agent in agents:
        user = db.query(User).filter(User.id == agent.user_id).first()
        items.append({
            "id": str(agent.id),
            "agent_id": str(agent.id),
            "user_id": str(agent.user_id),
            "name": f"{user.first_name} {user.last_name}".strip() if user else str(agent.id),
            "email": user.email if user else None,
            "status": agent.status,
            "npn": agent.national_producer_number,
            "license_count": license_counts.get(agent.id, 0),
        })
    return {"items": items, "total": len(items)}


@router.get("/agents/{agent_id}/profile", response_model=dict)
def get_agent_compliance_profile(
    agent_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    _ensure_agent_scope(db, current_user, agent_id)
    try:
        return services.agent_compliance_profile(db, tenant_id, agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found") from None


@router.get("/me/profile", response_model=dict)
def get_my_compliance_profile(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """The signed-in agent's own NPN + licenses + carrier appointments. Returns
    an empty shell (HTTP 200) when the user has no linked agent profile."""
    agent = _current_agent(db, current_user)
    if not agent:
        return {"agent_id": None, "npn": None, "licenses": [], "carrier_appointments": []}
    # Reuse the profile service's query logic, remapping state_licenses -> licenses.
    profile = services.agent_compliance_profile(db, tenant_id, agent.id)
    return {
        "agent_id": str(agent.id),
        "npn": agent.national_producer_number,
        "licenses": profile["state_licenses"],
        "carrier_appointments": profile["carrier_appointments"],
    }


@router.post("/me/npn", response_model=dict)
def set_my_npn(
    request: NpnUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Agent self-service: set my National Producer Number ONCE. A non-empty NPN
    cannot be overwritten here (an admin uses PATCH /agents/{id}/npn for that)."""
    agent = _current_agent(db, current_user)
    if not agent:
        raise HTTPException(status_code=403, detail="No agent profile")
    if isinstance(agent.national_producer_number, str) and agent.national_producer_number.strip():
        raise HTTPException(status_code=409, detail="NPN already set")
    agent.national_producer_number = request.npn
    db.commit()
    log_audit_event(
        tenant_id=tenant_id,
        action="agent_npn_set",
        resource_type="agent",
        resource_id=str(agent.id),
        user_id=str(current_user.id),
        details={"agent_id": str(agent.id)},
        db=db,
    )
    return {"ok": True, "npn": agent.national_producer_number}


@router.patch("/agents/{agent_id}/npn", response_model=dict)
def set_agent_npn(
    agent_id: UUID,
    request: NpnUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    """Admin-only: set/overwrite an agent's National Producer Number."""
    agent = _ensure_agent(db, tenant_id, agent_id)
    agent.national_producer_number = request.npn
    db.commit()
    log_audit_event(
        tenant_id=tenant_id,
        action="agent_npn_set",
        resource_type="agent",
        resource_id=str(agent.id),
        user_id=str(current_user.id),
        details={"agent_id": str(agent.id)},
        db=db,
    )
    return {"ok": True, "npn": agent.national_producer_number}


@router.get("/eligibility", response_model=dict)
def get_eligible_agents(
    carrier: str = Query(..., min_length=1),
    state: str = Query(..., min_length=2, max_length=2),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    result = services.eligible_agents(db, tenant_id, carrier, state.upper())
    if current_user.role == "agent":
        own = _current_agent(db, current_user)
        result["items"] = [item for item in result["items"] if own and item["id"] == str(own.id)]
        result["total"] = len(result["items"])
    return result


@router.get("/state-licenses", response_model=dict)
def list_state_licenses(
    agent_id: Optional[UUID] = None,
    state: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    query = db.query(AgentStateLicense).filter(AgentStateLicense.tenant_id == tenant_id)
    query = _agent_scope_filter(query, AgentStateLicense, db, current_user)
    if agent_id:
        query = query.filter(AgentStateLicense.agent_id == agent_id)
    if state:
        query = query.filter(AgentStateLicense.state_code == state.upper())
    if status_filter:
        query = query.filter(AgentStateLicense.status == status_filter.upper())
    total = query.count()
    items = query.order_by(AgentStateLicense.expiration_date.asc().nullslast()).offset((page - 1) * size).limit(size).all()
    return {"items": [StateLicenseResponse.model_validate(item).model_dump(mode="json") for item in items], "total": total, "page": page, "size": size}


@router.post("/state-licenses", response_model=StateLicenseResponse, status_code=status.HTTP_201_CREATED)
async def create_state_license(
    request: StateLicenseCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    # Self-service: an agent may add THEIR OWN licenses. Anyone with
    # COMPLIANCE_MANAGE (admin / tenant_admin) can add for any agent.
    # Agent-submitted licenses are NOT usable until an admin approves them: they
    # are created as PENDING (blocked from eligibility) and the admins are
    # notified to review. Admin-added licenses go live immediately (ACTIVE).
    from app.core.permissions import user_has_permission, Permission
    can_manage = user_has_permission(current_user, Permission.COMPLIANCE_MANAGE)
    own = _current_agent(db, current_user)
    is_own = bool(own and str(own.id) == str(request.agent_id))
    if not can_manage and not is_own:
        raise HTTPException(
            status_code=403,
            detail="You can only add your own licenses. Ask an admin to add licenses for other agents.",
        )

    agent = _ensure_agent(db, tenant_id, request.agent_id)
    # No duplicate states per agent: an agent may hold only one license row per
    # state (regardless of the license number entered). A previously REJECTED row
    # does NOT block a fresh submission — clear it so the agent can resubmit.
    existing = db.query(AgentStateLicense).filter(
        AgentStateLicense.tenant_id == tenant_id,
        AgentStateLicense.agent_id == request.agent_id,
        AgentStateLicense.state_code == request.state_code,
    ).first()
    if existing:
        if (existing.status or "").upper() == services.REJECTED:
            db.delete(existing)
            db.flush()
        else:
            raise HTTPException(status_code=409, detail=f"This agent already has a {request.state_code} license.")

    data = request.model_dump()
    # Effective date isn't asked for in the UI anymore; default to today (NOT NULL column).
    if not data.get("effective_date"):
        data["effective_date"] = date.today()
    self_added = not can_manage
    if self_added:
        # Agent submitted their own license → must be approved before use.
        data["status"] = services.PENDING
    row = AgentStateLicense(tenant_id=tenant_id, **data)
    db.add(row)
    db.commit()
    db.refresh(row)
    log_audit_event(
        tenant_id=tenant_id,
        action="state_license_created",
        resource_type="agent_state_license",
        resource_id=str(row.id),
        user_id=str(current_user.id),
        details={"agent_id": str(row.agent_id), "state": row.state_code, "self_added": self_added, "status": row.status},
        db=db,
    )
    if self_added:
        # In-app notification to every compliance admin to review the submission.
        # NEVER let a notification failure roll back the (already-committed)
        # license — e.g. before the notifications table migration is applied.
        try:
            agent_name = _agent_display_name(db, agent)
            notify_compliance_admins(
                db,
                tenant_id,
                type="compliance",
                title="License pending review",
                body=f"{agent_name} submitted a {row.state_code} license for approval.",
                link="settings.html#team",
                resource_type="agent_state_license",
                resource_id=str(row.id),
                meta={"agent_id": str(row.agent_id), "state": row.state_code},
                exclude_user_id=str(current_user.id),
            )
        except Exception:
            db.rollback()
    await emit_to_tenant(tenant_id, "state_license_created", StateLicenseResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.patch("/state-licenses/{license_id}/approve", response_model=StateLicenseResponse)
async def approve_state_license(
    license_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    """Admin approves a PENDING license → it becomes ACTIVE and the agent is
    notified in-app. (Status is recomputed from the dates so an expired one
    doesn't silently go active.)"""
    row = db.query(AgentStateLicense).filter(
        AgentStateLicense.tenant_id == tenant_id, AgentStateLicense.id == license_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="State license not found")
    row.status = services.compute_license_status(row.effective_date, row.expiration_date)
    db.commit()
    db.refresh(row)
    log_audit_event(
        tenant_id, "state_license_approved", "agent_state_license", str(row.id), str(current_user.id),
        {"agent_id": str(row.agent_id), "state": row.state_code, "status": row.status}, db=db,
    )
    # Resolve the admins' "pending review" notifications for this license so their
    # list/badge updates. Done BEFORE creating the agent's notification below so
    # that new one stays unread.
    mark_resource_notifications_read(db, tenant_id, "agent_state_license", license_id)
    agent = db.query(Agent).filter(Agent.id == row.agent_id).first()
    if agent:
        try:
            create_notification(
                db, tenant_id, str(agent.user_id),
                type="compliance",
                title="License approved",
                body=f"Your {row.state_code} license has been approved and is now active.",
                link="settings.html#npn-licenses",
                resource_type="agent_state_license",
                resource_id=str(row.id),
                meta={"state": row.state_code, "agent_id": str(row.agent_id)},
            )
        except Exception:
            db.rollback()
    await emit_to_tenant(tenant_id, "state_license_updated", StateLicenseResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.patch("/state-licenses/{license_id}/reject", response_model=StateLicenseResponse)
async def reject_state_license(
    license_id: UUID,
    reason: str = Body("", embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    """Admin rejects a pending license → status REJECTED (not usable) and the
    agent is notified in-app with the reason."""
    row = db.query(AgentStateLicense).filter(
        AgentStateLicense.tenant_id == tenant_id, AgentStateLicense.id == license_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="State license not found")
    row.status = services.REJECTED
    db.commit()
    db.refresh(row)
    reason_txt = (reason or "").strip()
    log_audit_event(
        tenant_id, "state_license_rejected", "agent_state_license", str(row.id), str(current_user.id),
        {"agent_id": str(row.agent_id), "state": row.state_code, "reason": reason_txt}, db=db,
    )
    mark_resource_notifications_read(db, tenant_id, "agent_state_license", license_id)
    agent = db.query(Agent).filter(Agent.id == row.agent_id).first()
    if agent:
        body = f"Your {row.state_code} license was rejected."
        if reason_txt:
            body += f" Reason: {reason_txt}"
        try:
            create_notification(
                db, tenant_id, str(agent.user_id),
                type="compliance",
                title="License rejected",
                body=body,
                link="settings.html#npn-licenses",
                resource_type="agent_state_license",
                resource_id=str(row.id),
                meta={"state": row.state_code, "reason": reason_txt, "agent_id": str(row.agent_id)},
            )
        except Exception:
            db.rollback()
    await emit_to_tenant(tenant_id, "state_license_updated", StateLicenseResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.patch("/state-licenses/{license_id}", response_model=StateLicenseResponse)
async def update_state_license(
    license_id: UUID,
    request: StateLicenseUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    row = db.query(AgentStateLicense).filter(AgentStateLicense.tenant_id == tenant_id, AgentStateLicense.id == license_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="State license not found")
    for key, value in request.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    log_audit_event(tenant_id, "state_license_updated", "agent_state_license", str(row.id), str(current_user.id), {"state": row.state_code}, db=db)
    await emit_to_tenant(tenant_id, "state_license_updated", StateLicenseResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.delete("/state-licenses/{license_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_state_license(
    license_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    row = db.query(AgentStateLicense).filter(AgentStateLicense.tenant_id == tenant_id, AgentStateLicense.id == license_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="State license not found")
    db.delete(row)
    db.commit()
    log_audit_event(tenant_id, "state_license_deleted", "agent_state_license", str(license_id), str(current_user.id), db=db)


@router.get("/carrier-appointments", response_model=dict)
def list_carrier_appointments(
    agent_id: Optional[UUID] = None,
    carrier: Optional[str] = None,
    state: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    query = db.query(AgentCarrierAppointment).filter(AgentCarrierAppointment.tenant_id == tenant_id)
    query = _agent_scope_filter(query, AgentCarrierAppointment, db, current_user)
    if agent_id:
        query = query.filter(AgentCarrierAppointment.agent_id == agent_id)
    if carrier:
        query = query.filter(AgentCarrierAppointment.carrier_key == services.carrier_key(carrier))
    if state:
        query = query.filter(AgentCarrierAppointment.state_code == state.upper())
    if status_filter:
        query = query.filter(AgentCarrierAppointment.status == status_filter.upper())
    total = query.count()
    items = query.order_by(AgentCarrierAppointment.expiration_date.asc().nullslast()).offset((page - 1) * size).limit(size).all()
    return {"items": [CarrierAppointmentResponse.model_validate(item).model_dump(mode="json") for item in items], "total": total, "page": page, "size": size}


@router.post("/carrier-appointments", response_model=CarrierAppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_carrier_appointment(
    request: CarrierAppointmentCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    _ensure_agent(db, tenant_id, request.agent_id)
    existing = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.agent_id == request.agent_id,
        AgentCarrierAppointment.carrier_key == services.carrier_key(request.carrier_name),
        AgentCarrierAppointment.state_code == request.state_code,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Carrier appointment already exists for this agent/state")
    row = services.create_or_update_appointment(
        db,
        tenant_id,
        request.agent_id,
        request.carrier_name,
        request.state_code,
        request.effective_date,
        request.expiration_date,
        request.appointment_number,
        request.status,
        user_id=str(current_user.id),
    )
    await emit_to_tenant(tenant_id, "carrier_appointment_created", CarrierAppointmentResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.patch("/carrier-appointments/{appointment_id}", response_model=CarrierAppointmentResponse)
async def update_carrier_appointment(
    appointment_id: UUID,
    request: CarrierAppointmentUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    row = db.query(AgentCarrierAppointment).filter(AgentCarrierAppointment.tenant_id == tenant_id, AgentCarrierAppointment.id == appointment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Carrier appointment not found")
    old_status = row.status
    updates = request.model_dump(exclude_unset=True)
    if "carrier_name" in updates and updates["carrier_name"]:
        updates["carrier_key"] = services.carrier_key(updates["carrier_name"])
    for key, value in updates.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    if old_status != row.status and row.status in {services.REVOKED, services.EXPIRED}:
        event = services.scan_lost_appointment_rule(db, row)
        if event:
            await services.emit_compliance_notification(tenant_id, "compliance_event_created", {
                "event_id": str(event.id),
                "notification_type": event.event_type,
                "title": "Carrier Appointment Lost",
                "message": event.message,
                "carrier": event.carrier,
                "state": event.state,
                "agent_id": str(event.agent_id),
            })
    log_audit_event(tenant_id, "appointment_updated", "agent_carrier_appointment", str(row.id), str(current_user.id), {"status": row.status}, db=db)
    await emit_to_tenant(tenant_id, "carrier_appointment_updated", CarrierAppointmentResponse.model_validate(row).model_dump(mode="json"))
    return row


@router.delete("/carrier-appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_carrier_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    row = db.query(AgentCarrierAppointment).filter(AgentCarrierAppointment.tenant_id == tenant_id, AgentCarrierAppointment.id == appointment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Carrier appointment not found")
    # Detach any compliance events tied to this appointment first. The FK
    # compliance_events.appointment_id -> agent_carrier_appointments.id is
    # ON DELETE NO ACTION, so leaving a referencing event in place makes the
    # delete raise an IntegrityError (surfaced to the UI as a 500). The event
    # history is kept for the record; only its link to this appointment is cleared.
    db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == tenant_id,
        ComplianceEvent.appointment_id == appointment_id,
    ).update({ComplianceEvent.appointment_id: None}, synchronize_session=False)
    db.delete(row)
    db.commit()
    log_audit_event(tenant_id, "appointment_deleted", "agent_carrier_appointment", str(appointment_id), str(current_user.id), db=db)


@router.post("/carrier-appointments/import-csv")
async def import_carrier_appointments_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    content = (await file.read()).decode("utf-8-sig")
    result = services.import_appointments_csv(db, tenant_id, content, user_id=str(current_user.id))
    await emit_to_tenant(tenant_id, "carrier_appointments_imported", result)
    return result


@router.post("/compliance-events", response_model=ComplianceEventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    request: ComplianceEventCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    event = services.create_compliance_event(db, tenant_id, user_id=str(current_user.id), **request.model_dump())
    await services.emit_compliance_notification(tenant_id, "compliance_event_created", {
        "event_id": str(event.id),
        "notification_type": event.event_type,
        "title": "Compliance event",
        "message": event.message,
        "severity": event.severity,
    })
    return event


@router.get("/compliance-events", response_model=dict)
def list_events(
    resolved: Optional[bool] = None,
    event_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    query = db.query(ComplianceEvent).filter(ComplianceEvent.tenant_id == tenant_id)
    query = _agent_scope_filter(query, ComplianceEvent, db, current_user)
    if resolved is not None:
        query = query.filter(ComplianceEvent.resolved == resolved)
    if event_type:
        query = query.filter(ComplianceEvent.event_type == event_type)
    total = query.count()
    items = query.order_by(ComplianceEvent.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [ComplianceEventResponse.model_validate(item).model_dump(mode="json") for item in items], "total": total, "page": page, "size": size}


@router.patch("/compliance-events/{event_id}/resolve", response_model=ComplianceEventResponse)
async def resolve_event(
    event_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    event = db.query(ComplianceEvent).filter(ComplianceEvent.tenant_id == tenant_id, ComplianceEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Compliance event not found")
    event.resolved = True
    db.commit()
    db.refresh(event)
    log_audit_event(tenant_id, "compliance_event_resolved", "compliance_event", str(event.id), str(current_user.id), db=db)
    await emit_to_tenant(tenant_id, "compliance_event_resolved", ComplianceEventResponse.model_validate(event).model_dump(mode="json"))
    return event


@router.post("/deals/submit", response_model=ApprovalDecisionResponse, status_code=status.HTTP_201_CREATED)
async def submit_deal(
    request: DealSubmitRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    _ensure_agent_scope(db, current_user, request.agent_id)
    deal, approval_log = await services.submit_deal_with_approval(
        db=db,
        tenant_id=tenant_id,
        agent_id=request.agent_id,
        carrier=request.carrier,
        state=request.state,
        user_id=str(current_user.id),
        lead_id=request.lead_id,
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        customer_dob=request.customer_dob,
        customer_email=request.customer_email,
        customer_address=request.customer_address,
        customer_city=request.customer_city,
        customer_zip=request.customer_zip,
        customer_gender=request.customer_gender,
        customer_marital_status=request.customer_marital_status,
        customer_tobacco=request.customer_tobacco,
        customer_income=request.customer_income,
        customer_ssn=request.customer_ssn,
        plan_type=request.plan_type,
        premium=request.premium,
        aca_count=request.aca_count,
        dental_count=request.dental_count,
        vision_count=request.vision_count,
        products=[p.model_dump() for p in request.products] if request.products else None,
        recording_id=request.recording_id,
        recording_ids=request.recording_ids,
    )

    # Capacity engine: logging a deal frees this agent for the next lead. Mark the
    # credited agent AVAILABLE and kick a drip so more CSV leads release to them
    # (runs in its own session off the event loop; best-effort; a no-op unless the
    # capacity engine is enabled). Never affects the committed deal, and the send
    # path / first-template lockdown are unchanged.
    try:
        from fastapi.concurrency import run_in_threadpool
        from app.pacing.release import free_agent_after_deal
        await run_in_threadpool(free_agent_after_deal, tenant_id, request.agent_id)
    except Exception:
        pass

    return {
        "decision": approval_log.decision,
        "reason": approval_log.reason,
        "deal": deal,
        "approval_log": approval_log,
    }


# No size cap on call recordings: a long call saved as WAV is uncompressed
# (~10 MB/min) and routinely exceeds any small limit, which was blocking agents
# from logging real sales. Any audio size is accepted (only an empty file is
# rejected). Agents are authenticated, so this is not a public-upload vector.
_AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".webm", ".aac", ".mp4", ".flac", ".amr", ".3gp")


@router.post("/deals/recording", status_code=status.HTTP_201_CREATED)
async def upload_deal_recording(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Store the sales-call recording the Add Deal form requires before it
    unlocks. Uses S3 when configured, otherwise stores the bytes inline so the
    upload always works. Returns the recording id to attach on /deals/submit."""
    raw = await file.read()
    size = len(raw)
    if size == 0:
        raise HTTPException(status_code=400, detail="The audio file is empty.")
    fname = file.filename or "recording"
    ctype = (file.content_type or "").lower()
    # Accept ALL audio file types — and the video containers calls are often saved
    # as (mp4/webm/mov/…), plus a missing/generic MIME — so a real recording is
    # never rejected. Only an empty file (handled above) is refused.
    is_audio = (
        ctype.startswith("audio/")
        or ctype.startswith("video/")
        or ctype in ("application/octet-stream", "")
        or fname.lower().endswith(_AUDIO_EXTS)
    )
    if not is_audio:
        raise HTTPException(status_code=415, detail="Please upload an audio (or video) recording of the call.")

    rec = DealRecording(
        tenant_id=tenant_id,
        filename=fname[:255],
        content_type=(ctype or "application/octet-stream")[:100],
        byte_size=size,
        storage="db",
    )
    # best-effort link to the uploading agent
    try:
        agent = db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.user_id == current_user.id).first()
        if agent:
            rec.agent_id = agent.id
    except Exception:
        pass
    # S3 when configured; otherwise fall back to inline DB bytes so the form is
    # never stuck locked on a deploy without AWS creds.
    stored_to_s3 = False
    try:
        from app.calls.s3_storage import s3_storage
        if s3_storage.configured():
            ext = (fname.rsplit(".", 1)[-1] if "." in fname else "mp3").lower()[:8] or "mp3"
            key = f"deal-recordings/{tenant_id}/{uuid4()}.{ext}"
            out = s3_storage.upload_bytes(raw, key, content_type=rec.content_type)
            rec.storage, rec.s3_bucket, rec.s3_key, rec.data = "s3", out["bucket"], out["key"], None
            stored_to_s3 = True
    except Exception:
        stored_to_s3 = False
    if not stored_to_s3:
        rec.storage, rec.data = "db", raw

    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {
        "id": str(rec.id),
        "filename": rec.filename,
        "byte_size": rec.byte_size,
        "content_type": rec.content_type,
        "storage": rec.storage,
    }


@router.get("/deals/recording/{recording_id}")
def get_deal_recording(
    recording_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Play back / download a stored deal recording (tenant-scoped)."""
    rec = (
        db.query(DealRecording)
        .filter(DealRecording.id == recording_id, DealRecording.tenant_id == tenant_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    if rec.storage == "s3" and rec.s3_key:
        from app.calls.s3_storage import s3_storage
        url = s3_storage.signed_url(rec.s3_key)
        if not url:
            raise HTTPException(status_code=404, detail="Recording is unavailable")
        return RedirectResponse(url)
    return Response(
        content=bytes(rec.data or b""),
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{rec.filename or "recording"}"'},
    )


@router.patch("/deals/{deal_id}/revalidate", response_model=ApprovalDecisionResponse)
async def revalidate_deal(
    deal_id: UUID,
    request: DealRevalidateRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    existing = db.query(Deal).filter(Deal.tenant_id == tenant_id, Deal.id == deal_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Deal not found")
    _ensure_agent_scope(db, current_user, existing.agent_id)
    if request.agent_id:
        _ensure_agent_scope(db, current_user, request.agent_id)
    deal, approval_log = await services.revalidate_deal(
        db=db,
        tenant_id=tenant_id,
        deal_id=deal_id,
        user_id=str(current_user.id),
        agent_id=request.agent_id,
        carrier=request.carrier,
        state=request.state,
    )
    return {
        "decision": approval_log.decision,
        "reason": approval_log.reason,
        "deal": deal,
        "approval_log": approval_log,
    }


# Statuses an admin can set on a deal from the All Deals page.
ALLOWED_DEAL_STATUSES = ("submitted", "approved", "paid", "won", "blocked")


@router.patch("/deals/{deal_id}/status")
def update_deal_status(
    deal_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin-only: change a deal's status (All Deals page)."""
    if current_user.role not in ("admin", "tenant_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    new_status = str(body.get("status", "")).strip().lower()
    if new_status not in ALLOWED_DEAL_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    deal = db.query(Deal).filter(Deal.tenant_id == tenant_id, Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    deal.status = new_status
    # Keep the approval decision in sync with the admin's status choice so EVERY
    # view agrees. All Deals shows `status`; the agent profile and the agent's own
    # My Deals read `approval_decision`. Without this, an admin "Approve" flipped
    # All Deals to Approved but left the agent profile (and the agent) on NOT_APPROVED.
    deal.approval_decision = services.APPROVED if new_status in APPROVED_STATUSES else services.NOT_APPROVED
    deal.approval_reason = f"Status set to '{new_status}' by admin"
    db.commit()
    return {"id": str(deal.id), "status": deal.status, "approval_decision": deal.approval_decision}


@router.patch("/deals/{deal_id}")
def update_deal(
    deal_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin-only: edit a logged deal's fields from the All Deals page. Updates only
    the editable fields present in the body; approval decision/status are left as-is
    (use the status dropdown or /revalidate to change those)."""
    from decimal import Decimal
    if current_user.role not in ("admin", "tenant_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    deal = db.query(Deal).filter(Deal.tenant_id == tenant_id, Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    for f in ("customer_name", "customer_phone", "customer_dob", "customer_email",
              "customer_address", "customer_city", "customer_zip", "customer_gender",
              "plan_type"):
        if f in body:
            v = body[f]
            setattr(deal, f, str(v).strip() if v not in (None, "") else None)
    if body.get("state"):
        deal.state = services.state_key(str(body["state"]))
    if "carrier" in body:
        c = str(body.get("carrier") or "").strip()
        if c:
            deal.carrier = c
            deal.carrier_key = services.carrier_key(c)
    if "premium" in body:
        p = body["premium"]
        try:
            deal.premium = Decimal(str(p)) if p not in (None, "") else None
        except Exception:
            pass
    db.commit()
    return {
        "id": str(deal.id),
        "customer_name": deal.customer_name, "customer_phone": deal.customer_phone,
        "customer_dob": deal.customer_dob, "customer_email": deal.customer_email,
        "customer_address": deal.customer_address, "customer_city": deal.customer_city,
        "customer_zip": deal.customer_zip, "customer_gender": deal.customer_gender,
        "state": deal.state, "carrier": deal.carrier, "plan_type": deal.plan_type,
        "premium": float(deal.premium) if deal.premium is not None else None,
        "status": deal.status,
    }


@router.get("/deals", response_model=dict)
def list_deals(
    decision: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    query = db.query(Deal).filter(Deal.tenant_id == tenant_id)
    query = _agent_scope_filter(query, Deal, db, current_user)
    if decision:
        query = query.filter(Deal.approval_decision == decision.upper())
    total = query.count()
    items = query.order_by(Deal.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [DealResponse.model_validate(item).model_dump(mode="json") for item in items], "total": total, "page": page, "size": size}


@router.get("/deals/my-today")
def my_deals_today(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    """Deals for the SIGNED-IN agent only, for the selected Eastern date range
    (defaults to today), with policy-count totals. Powers the agent's My Deals
    page."""
    empty_totals = {"total_deals": 0, "total_aca": 0, "total_dental": 0,
                    "total_vision": 0, "total_dental_vision": 0}
    agent = _current_agent(db, current_user)
    start, end, from_label, to_label = _eastern_range(from_, to)
    if not agent:
        return {"date": to_label, "from": from_label, "to": to_label,
                "totals": empty_totals, "deals": []}

    deals = (
        db.query(Deal)
        .filter(Deal.tenant_id == tenant_id, Deal.agent_id == agent.id,
                Deal.created_at >= start, Deal.created_at < end)
        .order_by(Deal.created_at.desc())
        .all()
    )
    # The LIST shows ALL statuses (this page reviews/approves pending deals); the CARD
    # totals below count APPROVED-only so they match every other page portal-wide.
    approved = [d for d in deals if (d.status or "").lower() in APPROVED_STATUSES]
    # Totals = COUNT of approved deal ROWS; per-coverage = rows carrying that coverage.
    t_aca = sum(1 for d in approved if (d.aca_count or 0) > 0)
    t_dental = sum(1 for d in approved if (d.dental_count or 0) > 0)
    t_vision = sum(1 for d in approved if (d.vision_count or 0) > 0)
    t_dv = sum(1 for d in approved if (d.dental_count or 0) > 0 or (d.vision_count or 0) > 0)
    items = [{
        "id": str(d.id),
        "customer_name": d.customer_name,
        "customer_phone": d.customer_phone,
        "state": d.state,
        "carrier": d.carrier,
        "plan_type": d.plan_type,
        "premium": float(d.premium) if d.premium is not None else None,
        "aca_count": d.aca_count or 0,
        "dental_count": d.dental_count or 0,
        "vision_count": d.vision_count or 0,
        "total": (d.aca_count or 0) + (d.dental_count or 0) + (d.vision_count or 0),
        "status": d.status,
        "approval_decision": d.approval_decision,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d in deals]
    return {
        "date": to_label,        # kept for backward compat (single-day label)
        "from": from_label,
        "to": to_label,
        "totals": {
            "total_deals": len(approved),
            "total_aca": t_aca,
            "total_dental": t_dental,
            "total_vision": t_vision,
            "total_dental_vision": t_dv,
        },
        "deals": items,
    }


def _eastern_range(from_iso: Optional[str], to_iso: Optional[str]):
    """Thin wrapper over core.date_ranges.resolve_range — the single shared helper.
    Returns ISO-string labels for back-compat with callers."""
    start, end, d_from, d_to = resolve_range(from_iso, to_iso)
    return start, end, d_from.isoformat(), d_to.isoformat()


@router.get("/deals/today-all")
def all_deals_today(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    """Deals across ALL agents (admin-only) for the selected Eastern date range
    (defaults to today), with org-wide policy totals and the agent who logged
    each deal. Powers the admin All Deals page."""
    if current_user.role not in ("admin", "tenant_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admins only")

    start, end, from_label, to_label = _eastern_range(from_, to)

    deals = (
        db.query(Deal)
        .filter(Deal.tenant_id == tenant_id, Deal.created_at >= start, Deal.created_at < end)
        .order_by(Deal.created_at.desc())
        .all()
    )
    # Resolve agent_id -> display name (one query, no N+1).
    agent_ids = {d.agent_id for d in deals if d.agent_id}
    name_map = {}
    if agent_ids:
        for aid, fn, ln in (
            db.query(Agent.id, User.first_name, User.last_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.id.in_(agent_ids)).all()
        ):
            name_map[aid] = (f"{fn or ''} {ln or ''}".strip() or "Unknown agent")

    # Resolve recording_id -> filename (one query, no N+1) so the admin All Deals
    # table can offer a per-deal download/play of the call recording.
    rec_ids = {d.recording_id for d in deals if d.recording_id}
    rec_map = {}
    if rec_ids:
        for rid, fn in (
            db.query(DealRecording.id, DealRecording.filename)
            .filter(DealRecording.id.in_(rec_ids)).all()
        ):
            rec_map[rid] = fn

    # The LIST (items below, capped 500) shows ALL statuses so admins can review/approve
    # pending deals; the CARD totals count APPROVED-only so they match every other page.
    approved = [d for d in deals if (d.status or "").lower() in APPROVED_STATUSES]
    # Totals = COUNT of approved deal ROWS; per-coverage = rows carrying that coverage.
    t_aca = sum(1 for d in approved if (d.aca_count or 0) > 0)
    t_dental = sum(1 for d in approved if (d.dental_count or 0) > 0)
    t_vision = sum(1 for d in approved if (d.vision_count or 0) > 0)
    t_dv = sum(1 for d in approved if (d.dental_count or 0) > 0 or (d.vision_count or 0) > 0)
    items = [{
        "id": str(d.id),
        "agent_name": name_map.get(d.agent_id, "—"),
        "customer_name": d.customer_name,
        "customer_phone": d.customer_phone,
        # Detail fields so the admin Edit modal can show/edit them (not shown in the table).
        "customer_dob": d.customer_dob,
        "customer_email": d.customer_email,
        "customer_address": d.customer_address,
        "customer_city": d.customer_city,
        "customer_zip": d.customer_zip,
        "customer_gender": d.customer_gender,
        "state": d.state,
        "carrier": d.carrier,
        "plan_type": d.plan_type,
        "premium": float(d.premium) if d.premium is not None else None,
        "aca_count": d.aca_count or 0,
        "dental_count": d.dental_count or 0,
        "vision_count": d.vision_count or 0,
        "total": (d.aca_count or 0) + (d.dental_count or 0) + (d.vision_count or 0),
        "status": d.status,
        "approval_decision": d.approval_decision,
        "recording_id": str(d.recording_id) if d.recording_id else None,
        "recording_filename": rec_map.get(d.recording_id),
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d in deals[:500]]   # table capped at 500; totals below span ALL of the range
    return {
        "date": to_label,        # kept for backward compat (single-day label)
        "from": from_label,
        "to": to_label,
        "totals": {
            "total_deals": len(approved),
            "total_aca": t_aca,
            "total_dental": t_dental,
            "total_vision": t_vision,
            "total_dental_vision": t_dv,
            "agent_count": len(agent_ids),
            "deal_count": len(approved),
        },
        "deals": items,
    }


@router.get("/deals/leaderboard")
def deals_leaderboard(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    """Agent leaderboard for the selected Eastern date range (defaults to today),
    ranked by total deals = COUNT of deal ROWS (one row = one deal), so the per-agent
    numbers sum to the hero total. The ACA/Dental/Vision columns remain policy-count
    sums. Global — visible to admins AND agents; the highest total is on top."""
    from sqlalchemy import func

    start, end, from_label, to_label = _eastern_range(from_, to)

    # Per-agent numbers are ALL deal-ROW counts (one row = one deal), one query:
    # total = COUNT(*), each coverage = COUNT(*) of rows carrying it. One unit across
    # the whole leaderboard, so every coverage column is <= total and the per-agent
    # totals sum to the hero.
    sums = {}
    for aid, a, d, v, dv, n in (
        db.query(
            Deal.agent_id,
            func.count(Deal.id).filter(Deal.aca_count > 0),
            func.count(Deal.id).filter(Deal.dental_count > 0),
            func.count(Deal.id).filter(Deal.vision_count > 0),
            func.count(Deal.id).filter((Deal.dental_count > 0) | (Deal.vision_count > 0)),
            func.count(Deal.id),
        )
        # Leaderboard counts APPROVED deals only (approved / paid / won) — never
        # denied/blocked or not-yet-approved (submitted).
        .filter(
            Deal.tenant_id == tenant_id,
            Deal.created_at >= start,
            Deal.created_at < end,
            func.lower(Deal.status).in_(APPROVED_STATUSES),
        )
        .group_by(Deal.agent_id)
        .all()
    ):
        sums[aid] = {"aca": int(a or 0), "dental": int(d or 0), "vision": int(v or 0),
                     "dv": int(dv or 0), "deals": int(n or 0)}

    # Every active agent + name, plus any agent who sold today but isn't active.
    name_map = {}
    for aid, fn, ln in (
        db.query(Agent.id, User.first_name, User.last_name)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.tenant_id == tenant_id, Agent.status == "active")
        .all()
    ):
        name_map[aid] = (f"{fn or ''} {ln or ''}".strip() or "Unknown agent")
    missing = [aid for aid in sums if aid not in name_map]
    if missing:
        for aid, fn, ln in (
            db.query(Agent.id, User.first_name, User.last_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.id.in_(missing)).all()
        ):
            name_map[aid] = (f"{fn or ''} {ln or ''}".strip() or "Unknown agent")

    board = []
    for aid in (set(name_map) | set(sums)):
        s = sums.get(aid, {"aca": 0, "dental": 0, "vision": 0, "dv": 0, "deals": 0})
        board.append({
            "agent_id": str(aid),
            "agent_name": name_map.get(aid, "Unknown agent"),
            "total_aca": s["aca"],
            "total_dental": s["dental"],
            "total_vision": s["vision"],
            "total_dental_vision": s["dv"],
            # Per-agent TOTAL = COUNT(*) deal rows, so Σ per-agent == the hero exactly.
            # Coverage columns stay flag-counts; they overlap and do NOT sum to TOTAL
            # (the honest coverage identity). Board re-sorts by this; podium = table.
            "total_deals": s["deals"],
        })
    board.sort(key=lambda x: (-x["total_deals"], x["agent_name"].lower()))

    # Hero summary card = deal-ROW counts (one row = one deal), computed
    # independently of the policy-sum agent RANKINGS above (which are unchanged).
    h = (
        db.query(
            func.count(Deal.id),
            func.count(Deal.id).filter(Deal.aca_count > 0),
            func.count(Deal.id).filter(Deal.dental_count > 0),
            func.count(Deal.id).filter(Deal.vision_count > 0),
            func.count(Deal.id).filter((Deal.dental_count > 0) | (Deal.vision_count > 0)),
        )
        .filter(
            Deal.tenant_id == tenant_id,
            Deal.created_at >= start,
            Deal.created_at < end,
            func.lower(Deal.status).in_(APPROVED_STATUSES),
        )
        .first()
    )

    me = _current_agent(db, current_user)
    return {
        "date": to_label,        # kept for backward compat (single-day label)
        "from": from_label,
        "to": to_label,
        "me_agent_id": str(me.id) if me else None,
        "totals": {
            "total_deals": int(h[0] or 0),
            "total_aca": int(h[1] or 0),
            "total_dental": int(h[2] or 0),
            "total_vision": int(h[3] or 0),
            "total_dental_vision": int(h[4] or 0),
            "agent_count": len([b for b in board if b["total_deals"] > 0]),
        },
        "leaderboard": board,
    }


@router.get("/approval-logs", response_model=dict)
def list_approval_logs(
    decision: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    query = db.query(DealApprovalLog).filter(DealApprovalLog.tenant_id == tenant_id)
    query = _agent_scope_filter(query, DealApprovalLog, db, current_user)
    if decision:
        query = query.filter(DealApprovalLog.decision == decision.upper())
    total = query.count()
    items = query.order_by(DealApprovalLog.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": [DealApprovalLogResponse.model_validate(item).model_dump(mode="json") for item in items], "total": total, "page": page, "size": size}


@router.get("/dashboard", response_model=ComplianceDashboardResponse)
def compliance_dashboard(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    return services.dashboard_metrics(db, tenant_id)


@router.get("/analytics", response_model=ComplianceAnalyticsResponse)
def compliance_analytics(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_read),
):
    return services.analytics(db, tenant_id)


@router.post("/scan/expirations")
async def scan_expirations(
    as_of: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    result = services.scan_appointment_expirations(db, tenant_id=tenant_id, as_of=as_of)
    for event in result.get("events", []):
        await services.emit_compliance_notification(tenant_id, event["emit_name"], {
            **event,
            "notification_type": event["event_type"],
            "title": "Appointment expired" if event["emit_name"] == "appointment_expired" else "Appointment expiring",
        })
    if result["events_created"]:
        await emit_to_tenant(tenant_id, "compliance_scan_completed", result)
    return result


@router.post("/scan/risk")
async def scan_risk(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_compliance_manage),
):
    result = services.scan_recent_deal_risk(db, tenant_id=tenant_id)
    for event in result.get("events", []):
        await services.emit_compliance_notification(tenant_id, "compliance_event_created", {
            **event,
            "notification_type": event["event_type"],
            "title": "Potential compliance risk",
        })
    if result["risk_events_created"]:
        await emit_to_tenant(tenant_id, "compliance_scan_completed", result)
    return result
