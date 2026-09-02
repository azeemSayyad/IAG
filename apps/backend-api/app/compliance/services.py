from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit_event
from app.models.agent import Agent
from app.models.compliance import (
    AgentCarrierAppointment,
    AgentStateLicense,
    ComplianceEvent,
    Deal,
    DealApprovalLog,
)
from app.models.user import User
from app.schemas.compliance import normalize_state


APPROVED = "APPROVED"
NOT_APPROVED = "NOT_APPROVED"
FLAGGED = "FLAGGED"

ACTIVE = "ACTIVE"
EXPIRED = "EXPIRED"
PENDING = "PENDING"
REVOKED = "REVOKED"
REJECTED = "REJECTED"

COMPLIANCE_WARNING = "COMPLIANCE_WARNING"
COMPLIANCE_EXPIRED = "COMPLIANCE_EXPIRED"
COMPLIANCE_REVOKED = "COMPLIANCE_REVOKED"
DEAL_NOT_APPROVED = "DEAL_NOT_APPROVED"
POTENTIAL_COMPLIANCE_RISK = "POTENTIAL_COMPLIANCE_RISK"


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str
    appointment: Optional[AgentCarrierAppointment] = None
    license: Optional[AgentStateLicense] = None


def carrier_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def state_key(value: str) -> str:
    return (value or "").strip().upper()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def is_effective(effective_date: date, as_of: date) -> bool:
    return effective_date <= as_of


def is_not_expired(expiration_date: Optional[date], as_of: date) -> bool:
    return expiration_date is None or expiration_date >= as_of


def compute_license_status(effective_date: date, expiration_date: Optional[date]) -> str:
    """Resolve a license's status from its dates (used when an admin approves a
    pending license — an already-expired one must not silently go active)."""
    as_of = today_utc()
    if expiration_date is not None and expiration_date < as_of:
        return EXPIRED
    return ACTIVE


def agent_name(db: Session, agent_id: UUID) -> str:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return "Unknown agent"
    user = db.query(User).filter(User.id == agent.user_id).first()
    if not user:
        return str(agent_id)
    return f"{user.first_name} {user.last_name}".strip()


def agent_identity(db: Session, agent: Agent) -> dict:
    user = db.query(User).filter(User.id == agent.user_id).first()
    name = f"{user.first_name} {user.last_name}".strip() if user else str(agent.id)
    return {
        "id": str(agent.id),
        "user_id": str(agent.user_id),
        "name": name,
        "email": user.email if user else None,
        "role": user.role if user else "agent",
        "status": agent.status,
    }


def create_compliance_event(
    db: Session,
    tenant_id: str,
    event_type: str,
    message: str,
    severity: str = "info",
    agent_id: Optional[UUID] = None,
    carrier: Optional[str] = None,
    state: Optional[str] = None,
    appointment_id: Optional[UUID] = None,
    deal_id: Optional[UUID] = None,
    user_id: Optional[str] = None,
    commit: bool = True,
) -> ComplianceEvent:
    event = ComplianceEvent(
        tenant_id=tenant_id,
        agent_id=agent_id,
        appointment_id=appointment_id,
        deal_id=deal_id,
        event_type=event_type,
        carrier=carrier,
        state=state_key(state) if state else None,
        message=message,
        severity=severity,
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    log_audit_event(
        tenant_id=tenant_id,
        action="compliance_event_created",
        resource_type="compliance_event",
        resource_id=str(event.id),
        user_id=user_id,
        details={
            "event_type": event_type,
            "severity": severity,
            "agent_id": str(agent_id) if agent_id else None,
            "carrier": carrier,
            "state": state,
            "deal_id": str(deal_id) if deal_id else None,
        },
        db=db,
    )
    return event


def _active_license_query(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    state: str,
    as_of: date,
):
    return db.query(AgentStateLicense).filter(
        AgentStateLicense.tenant_id == tenant_id,
        AgentStateLicense.agent_id == agent_id,
        AgentStateLicense.state_code == state_key(state),
        AgentStateLicense.status == ACTIVE,
        AgentStateLicense.effective_date <= as_of,
        or_(AgentStateLicense.expiration_date.is_(None), AgentStateLicense.expiration_date >= as_of),
    )


def _active_appointment_query(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    carrier: str,
    state: str,
    as_of: date,
):
    return db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.agent_id == agent_id,
        AgentCarrierAppointment.carrier_key == carrier_key(carrier),
        AgentCarrierAppointment.state_code == state_key(state),
        AgentCarrierAppointment.status == ACTIVE,
        AgentCarrierAppointment.effective_date <= as_of,
        or_(AgentCarrierAppointment.expiration_date.is_(None), AgentCarrierAppointment.expiration_date >= as_of),
    )


def evaluate_deal(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    carrier: str,
    state: str,
    as_of: Optional[date] = None,
) -> Decision:
    as_of = as_of or today_utc()
    state = state_key(state)

    agent = db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.id == agent_id).first()
    if not agent:
        return Decision(NOT_APPROVED, "Agent does not exist for this tenant")

    license_row = _active_license_query(db, tenant_id, agent_id, state, as_of).first()
    if not license_row:
        return Decision(NOT_APPROVED, f"Agent does not have an active state license for {state}")

    # Approval is based on the agent's active STATE LICENSE only: licensed in the
    # deal's state -> APPROVED, not licensed -> NOT_APPROVED (handled above). The
    # carrier appointment is still looked up and recorded on the decision for
    # reference, but it no longer blocks the deal.
    appointment = _active_appointment_query(db, tenant_id, agent_id, carrier, state, as_of).first()
    note = f"Active {state} license found" + ("" if appointment else f" (no {carrier} appointment on file)")
    return Decision(APPROVED, note, appointment, license_row)


def agent_compliance_profile(db: Session, tenant_id: str, agent_id: UUID) -> dict:
    agent = db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.id == agent_id).first()
    if not agent:
        raise ValueError("Agent not found")
    as_of = today_utc()
    licenses = db.query(AgentStateLicense).filter(
        AgentStateLicense.tenant_id == tenant_id,
        AgentStateLicense.agent_id == agent_id,
    ).order_by(AgentStateLicense.expiration_date.asc().nullslast()).all()
    appointments = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.agent_id == agent_id,
    ).order_by(AgentCarrierAppointment.expiration_date.asc().nullslast()).all()
    events = db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == tenant_id,
        ComplianceEvent.agent_id == agent_id,
    ).order_by(ComplianceEvent.created_at.desc()).limit(25).all()
    logs = db.query(DealApprovalLog).filter(
        DealApprovalLog.tenant_id == tenant_id,
        DealApprovalLog.agent_id == agent_id,
    ).order_by(DealApprovalLog.created_at.desc()).limit(25).all()

    active_licenses = [
        row for row in licenses
        if row.status == ACTIVE and is_effective(row.effective_date, as_of) and is_not_expired(row.expiration_date, as_of)
    ]
    active_appointments = [
        row for row in appointments
        if row.status == ACTIVE and is_effective(row.effective_date, as_of) and is_not_expired(row.expiration_date, as_of)
    ]
    expiring_soon = [
        row for row in appointments
        if row.expiration_date and row.status == ACTIVE and as_of <= row.expiration_date <= as_of + timedelta(days=60)
    ]
    expired = [
        row for row in appointments
        if row.status in {EXPIRED, REVOKED} or (row.expiration_date and row.expiration_date < as_of)
    ]
    total_logs = len(logs)
    approved_logs = sum(1 for log in logs if log.decision == APPROVED)
    return {
        "agent": agent_identity(db, agent),
        "npn": agent.national_producer_number,
        "summary": {
            "active_state_licenses": len(active_licenses),
            "active_carrier_appointments": len(active_appointments),
            "expiring_soon": len(expiring_soon),
            "expired_or_revoked": len(expired),
            "open_events": sum(1 for event in events if not event.resolved),
            "approval_rate": round((approved_logs / total_logs) * 100, 1) if total_logs else 0.0,
            "total_decisions": total_logs,
        },
        "state_licenses": [
            {
                "id": str(row.id),
                "state_code": row.state_code,
                "license_number": row.license_number,
                "effective_date": row.effective_date.isoformat(),
                "expiration_date": row.expiration_date.isoformat() if row.expiration_date else None,
                "status": row.status,
            }
            for row in licenses
        ],
        "carrier_appointments": [
            {
                "id": str(row.id),
                "carrier_name": row.carrier_name,
                "state_code": row.state_code,
                "appointment_number": row.appointment_number,
                "effective_date": row.effective_date.isoformat() if row.effective_date else None,
                "expiration_date": row.expiration_date.isoformat() if row.expiration_date else None,
                "status": row.status,
                "days_until_expiration": (row.expiration_date - as_of).days if row.expiration_date else None,
            }
            for row in appointments
        ],
        "events": [
            {
                "id": str(row.id),
                "event_type": row.event_type,
                "carrier": row.carrier,
                "state": row.state,
                "message": row.message,
                "severity": row.severity,
                "resolved": row.resolved,
                "created_at": row.created_at.isoformat(),
            }
            for row in events
        ],
        "approval_logs": [
            {
                "id": str(row.id),
                "deal_id": str(row.deal_id),
                "carrier": row.carrier,
                "state": row.state,
                "decision": row.decision,
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
            }
            for row in logs
        ],
    }


def eligible_agents(db: Session, tenant_id: str, carrier: str, state: str, as_of: Optional[date] = None) -> dict:
    as_of = as_of or today_utc()
    state = state_key(state)
    items = []
    for agent in db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.status == "active").all():
        decision = evaluate_deal(db, tenant_id, agent.id, carrier, state, as_of)
        if decision.decision != APPROVED:
            continue
        ident = agent_identity(db, agent)
        ident.update({
            "carrier": carrier,
            "state": state,
            "appointment_id": str(decision.appointment.id) if decision.appointment else None,
            "license_id": str(decision.license.id) if decision.license else None,
            "reason": decision.reason,
        })
        items.append(ident)
    return {"items": items, "total": len(items), "carrier": carrier, "state": state}


async def revalidate_deal(
    db: Session,
    tenant_id: str,
    deal_id: UUID,
    user_id: Optional[str] = None,
    agent_id: Optional[UUID] = None,
    carrier: Optional[str] = None,
    state: Optional[str] = None,
) -> tuple[Deal, DealApprovalLog]:
    deal = db.query(Deal).filter(Deal.tenant_id == tenant_id, Deal.id == deal_id).first()
    if not deal:
        raise ValueError("Deal not found")
    if agent_id:
        deal.agent_id = agent_id
    if carrier:
        deal.carrier = carrier
        deal.carrier_key = carrier_key(carrier)
    if state:
        deal.state = state_key(state)
    decision = evaluate_deal(db, tenant_id, deal.agent_id, deal.carrier, deal.state)
    deal.status = "approved" if decision.decision == APPROVED else "blocked"
    deal.approval_decision = decision.decision
    deal.approval_reason = decision.reason
    db.flush()

    log = DealApprovalLog(
        tenant_id=tenant_id,
        deal_id=deal.id,
        agent_id=deal.agent_id,
        carrier=deal.carrier,
        state=deal.state,
        decision=decision.decision,
        reason=decision.reason,
    )
    db.add(log)
    if decision.decision != APPROVED:
        create_compliance_event(
            db,
            tenant_id=tenant_id,
            event_type=DEAL_NOT_APPROVED,
            severity="high",
            agent_id=deal.agent_id,
            carrier=deal.carrier,
            state=deal.state,
            deal_id=deal.id,
            message=f"Deal revalidation blocked: {decision.reason}",
            user_id=user_id,
            commit=False,
        )
    db.commit()
    db.refresh(deal)
    db.refresh(log)
    log_audit_event(
        tenant_id=tenant_id,
        action="approval_revalidated",
        resource_type="deal",
        resource_id=str(deal.id),
        user_id=user_id,
        details={
            "agent_id": str(deal.agent_id),
            "carrier": deal.carrier,
            "state": deal.state,
            "decision": decision.decision,
            "reason": decision.reason,
        },
        db=db,
    )
    await emit_compliance_notification(tenant_id, "deal_approved" if decision.decision == APPROVED else "deal_not_approved", {
        "deal_id": str(deal.id),
        "agent_id": str(deal.agent_id),
        "carrier": deal.carrier,
        "state": deal.state,
        "decision": decision.decision,
        "reason": decision.reason,
        "notification_type": "DEAL_APPROVED" if decision.decision == APPROVED else "DEAL_NOT_APPROVED",
        "title": "Deal approved" if decision.decision == APPROVED else "Deal not approved",
        "message": decision.reason,
    })
    return deal, log


async def emit_compliance_notification(tenant_id: str, event: str, payload: dict) -> None:
    from app.realtime.websocket import emit_to_tenant

    await emit_to_tenant(tenant_id, event, payload)
    await emit_to_tenant(tenant_id, "notification", {
        "type": payload.get("notification_type") or event.upper(),
        "title": payload.get("title") or "Compliance update",
        "message": payload.get("message") or payload.get("reason") or "Compliance event",
        "data": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def submit_deal_with_approval(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    carrier: str,
    state: str,
    user_id: Optional[str] = None,
    lead_id: Optional[UUID] = None,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    customer_dob: Optional[str] = None,
    customer_email: Optional[str] = None,
    customer_address: Optional[str] = None,
    customer_city: Optional[str] = None,
    customer_zip: Optional[str] = None,
    customer_gender: Optional[str] = None,
    customer_marital_status: Optional[str] = None,
    customer_tobacco: Optional[str] = None,
    customer_income: Optional[str] = None,
    customer_ssn: Optional[str] = None,
    plan_type: Optional[str] = None,
    premium=None,
    aca_count: int = 1,
    dental_count: int = 0,
    vision_count: int = 0,
    products: Optional[list] = None,
    recording_id: Optional[UUID] = None,
    recording_ids: Optional[list] = None,
) -> tuple[Deal, DealApprovalLog]:
    from decimal import Decimal
    products_json = None
    if products:
        # Multi-product (per-person) mode: compliance is checked for EACH product's
        # carrier; the person's deal is approved only if every product passes. The
        # 0/1 counts below keep My Deals / All Deals / Leaderboard summing policies.
        evaluated, reasons, all_ok = [], [], True
        for p in products:
            p_carrier = (p.get("carrier") or "").strip()
            if p_carrier:
                d = evaluate_deal(db, tenant_id, agent_id, p_carrier, state)
                ok = d.decision == APPROVED
                all_ok = all_ok and ok
                if not ok:
                    reasons.append(f"{p.get('product') or 'Product'} ({p_carrier}): {d.reason}")
                p_decision, p_reason = d.decision, d.reason
            else:
                # Dental / Vision carry no carrier, so there's no carrier-appointment
                # check to run — they pass on their own.
                p_decision, p_reason = APPROVED, "No carrier (ancillary product)"
            _prem = str(p.get("premium") or "").strip()
            evaluated.append({
                "product": p.get("product"), "carrier": p_carrier, "tier": p.get("tier"),
                "plan_name": p.get("plan_name"),
                "premium": float(_prem) if _prem not in ("", "None") else None,
                "effective_date": p.get("effective_date"),
                "decision": p_decision, "reason": p_reason,
            })
        names = {(p.get("product") or "").strip().lower() for p in products}
        aca_count = 1 if "aca" in names else 0
        dental_count = 1 if "dental" in names else 0
        vision_count = 1 if "vision" in names else 0
        # Primary product (ACA first) fills the dashboards' single carrier column;
        # premium is the person's total across their products.
        primary = next((p for p in products if (p.get("product") or "").strip().lower() == "aca"), products[0])
        carrier = (primary.get("carrier") or carrier or "—").strip() or "—"
        plan_type = " + ".join([(p.get("product") or "?") for p in products]) or plan_type
        try:
            premium = sum((Decimal(str(p.get("premium") or 0)) for p in products), Decimal(0))
        except Exception:
            pass
        decision = Decision(
            APPROVED if all_ok else NOT_APPROVED,
            "All products approved." if all_ok else "; ".join(reasons),
        )
        products_json = evaluated
    else:
        decision = evaluate_deal(db, tenant_id, agent_id, carrier, state)
    # Up to 4 call recordings. recording_id stays the PRIMARY (first) for back-compat;
    # recording_ids holds the full set. Accept either a list or the single legacy id.
    _rec_ids = [r for r in (recording_ids or ([recording_id] if recording_id else [])) if r]
    _primary_rec = recording_id or (_rec_ids[0] if _rec_ids else None)
    deal = Deal(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lead_id=lead_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_dob=customer_dob,
        customer_email=customer_email,
        customer_address=customer_address,
        customer_city=customer_city,
        customer_zip=customer_zip,
        customer_gender=customer_gender,
        customer_marital_status=customer_marital_status,
        customer_tobacco=customer_tobacco,
        customer_income=customer_income,
        customer_ssn=customer_ssn,
        carrier=carrier,
        carrier_key=carrier_key(carrier),
        state=state_key(state),
        plan_type=plan_type,
        premium=premium,
        aca_count=max(0, aca_count or 0),
        dental_count=max(0, dental_count or 0),
        vision_count=max(0, vision_count or 0),
        products=products_json,
        recording_id=_primary_rec,
        recording_ids=([str(r) for r in _rec_ids] or None),
        status="approved" if decision.decision == APPROVED else "blocked",
        approval_decision=decision.decision,
        approval_reason=decision.reason,
    )
    db.add(deal)
    db.flush()

    log = DealApprovalLog(
        tenant_id=tenant_id,
        deal_id=deal.id,
        agent_id=agent_id,
        carrier=carrier,
        state=state_key(state),
        decision=decision.decision,
        reason=decision.reason,
    )
    db.add(log)

    if decision.decision != APPROVED:
        create_compliance_event(
            db,
            tenant_id=tenant_id,
            event_type=DEAL_NOT_APPROVED,
            severity="high",
            agent_id=agent_id,
            carrier=carrier,
            state=state,
            deal_id=deal.id,
            message=f"Deal blocked: {decision.reason}",
            user_id=user_id,
            commit=False,
        )

    db.commit()
    db.refresh(deal)
    db.refresh(log)

    log_audit_event(
        tenant_id=tenant_id,
        action="approval_decision",
        resource_type="deal",
        resource_id=str(deal.id),
        user_id=user_id,
        details={
            "agent_id": str(agent_id),
            "carrier": carrier,
            "state": state_key(state),
            "decision": decision.decision,
            "reason": decision.reason,
        },
        db=db,
    )

    event_name = "deal_approved" if decision.decision == APPROVED else "deal_not_approved"
    await emit_compliance_notification(tenant_id, event_name, {
        "deal_id": str(deal.id),
        "agent_id": str(agent_id),
        "carrier": carrier,
        "state": state_key(state),
        "decision": decision.decision,
        "reason": decision.reason,
        "notification_type": "DEAL_NOT_APPROVED" if decision.decision != APPROVED else "DEAL_APPROVED",
        "title": "Deal approved" if decision.decision == APPROVED else "Deal not approved",
        "message": decision.reason,
    })

    return deal, log


def create_or_update_appointment(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    carrier_name: str,
    state_code: str,
    effective_date: Optional[date],
    expiration_date: Optional[date],
    appointment_number: Optional[str] = None,
    status: str = ACTIVE,
    user_id: Optional[str] = None,
) -> AgentCarrierAppointment:
    appointment = AgentCarrierAppointment(
        tenant_id=tenant_id,
        agent_id=agent_id,
        carrier_name=carrier_name,
        carrier_key=carrier_key(carrier_name),
        state_code=state_key(state_code),
        appointment_number=appointment_number,
        # Carrier appointments carry no effective date in the UI; default to today
        # so the NOT NULL column is satisfied without asking for it.
        effective_date=effective_date or date.today(),
        expiration_date=expiration_date,
        status=(status or ACTIVE).upper(),
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    log_audit_event(
        tenant_id=tenant_id,
        action="appointment_created",
        resource_type="agent_carrier_appointment",
        resource_id=str(appointment.id),
        user_id=user_id,
        details={"carrier": carrier_name, "state": state_key(state_code), "agent_id": str(agent_id)},
        db=db,
    )
    return appointment


def scan_appointment_expirations(
    db: Session,
    tenant_id: Optional[str] = None,
    as_of: Optional[date] = None,
) -> dict:
    as_of = as_of or today_utc()
    query = db.query(AgentCarrierAppointment).filter(AgentCarrierAppointment.status == ACTIVE)
    if tenant_id:
        query = query.filter(AgentCarrierAppointment.tenant_id == tenant_id)
    query = query.filter(AgentCarrierAppointment.expiration_date.isnot(None))

    created = 0
    created_events = []
    for appointment in query.all():
        days = (appointment.expiration_date - as_of).days
        if days < 0:
            event_type, severity, emit_name = COMPLIANCE_EXPIRED, "critical", "appointment_expired"
            message = f"{appointment.carrier_name} appointment in {appointment.state_code} expired for {agent_name(db, appointment.agent_id)}"
        elif days <= 30:
            event_type, severity, emit_name = COMPLIANCE_WARNING, "high", "appointment_expiring"
            message = f"{appointment.carrier_name} appointment in {appointment.state_code} expires in {days} days"
        elif days <= 60:
            event_type, severity, emit_name = COMPLIANCE_WARNING, "medium", "appointment_expiring"
            message = f"{appointment.carrier_name} appointment in {appointment.state_code} expires in {days} days"
        else:
            continue

        exists = db.query(ComplianceEvent).filter(
            ComplianceEvent.tenant_id == appointment.tenant_id,
            ComplianceEvent.appointment_id == appointment.id,
            ComplianceEvent.event_type == event_type,
            ComplianceEvent.resolved.is_(False),
        ).first()
        if exists:
            continue
        event = create_compliance_event(
            db,
            tenant_id=str(appointment.tenant_id),
            event_type=event_type,
            severity=severity,
            agent_id=appointment.agent_id,
            carrier=appointment.carrier_name,
            state=appointment.state_code,
            appointment_id=appointment.id,
            message=message,
        )
        created += 1
        created_events.append({
            "event_id": str(event.id),
            "event_type": event.event_type,
            "emit_name": emit_name,
            "agent_id": str(appointment.agent_id),
            "appointment_id": str(appointment.id),
            "carrier": appointment.carrier_name,
            "state": appointment.state_code,
            "message": message,
            "severity": severity,
            "tenant_id": str(appointment.tenant_id),
        })

    return {"success": True, "events_created": created, "events": created_events}


def scan_recent_deal_risk(
    db: Session,
    tenant_id: Optional[str] = None,
    as_of: Optional[datetime] = None,
    days: int = 45,
) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    start = as_of - timedelta(days=days)
    query = db.query(Deal).filter(Deal.created_at >= start, Deal.approval_decision == APPROVED)
    if tenant_id:
        query = query.filter(Deal.tenant_id == tenant_id)

    flagged = 0
    created_events = []
    for deal in query.all():
        decision = evaluate_deal(db, str(deal.tenant_id), deal.agent_id, deal.carrier, deal.state, as_of.date())
        if decision.decision == APPROVED:
            continue
        exists = db.query(ComplianceEvent).filter(
            ComplianceEvent.tenant_id == deal.tenant_id,
            ComplianceEvent.deal_id == deal.id,
            ComplianceEvent.event_type == POTENTIAL_COMPLIANCE_RISK,
            ComplianceEvent.resolved.is_(False),
        ).first()
        if exists:
            continue
        event = create_compliance_event(
            db,
            tenant_id=str(deal.tenant_id),
            event_type=POTENTIAL_COMPLIANCE_RISK,
            severity="critical",
            agent_id=deal.agent_id,
            carrier=deal.carrier,
            state=deal.state,
            deal_id=deal.id,
            message=f"Potential compliance risk: deal approved in last {days} days but current access is invalid. {decision.reason}",
        )
        flagged += 1
        created_events.append({
            "event_id": str(event.id),
            "event_type": event.event_type,
            "emit_name": "compliance_event_created",
            "agent_id": str(deal.agent_id),
            "deal_id": str(deal.id),
            "carrier": deal.carrier,
            "state": deal.state,
            "message": event.message,
            "severity": event.severity,
            "tenant_id": str(deal.tenant_id),
        })

    return {"success": True, "risk_events_created": flagged, "events": created_events}


def scan_lost_appointment_rule(
    db: Session,
    appointment: AgentCarrierAppointment,
    as_of: Optional[datetime] = None,
    days: int = 45,
) -> Optional[ComplianceEvent]:
    if appointment.status not in {REVOKED, EXPIRED}:
        return None
    as_of = as_of or datetime.now(timezone.utc)
    start = as_of - timedelta(days=days)
    recent_count = db.query(Deal).filter(
        Deal.tenant_id == appointment.tenant_id,
        Deal.agent_id == appointment.agent_id,
        Deal.carrier_key == appointment.carrier_key,
        Deal.state == appointment.state_code,
        Deal.created_at >= start,
    ).count()
    if recent_count == 0:
        return None
    exists = db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == appointment.tenant_id,
        ComplianceEvent.appointment_id == appointment.id,
        ComplianceEvent.event_type == COMPLIANCE_REVOKED,
        ComplianceEvent.resolved.is_(False),
    ).first()
    if exists:
        return exists
    name = agent_name(db, appointment.agent_id)
    return create_compliance_event(
        db,
        tenant_id=str(appointment.tenant_id),
        event_type=COMPLIANCE_REVOKED,
        severity="critical",
        agent_id=appointment.agent_id,
        carrier=appointment.carrier_name,
        state=appointment.state_code,
        appointment_id=appointment.id,
        message=(
            f"Carrier Appointment Lost: {name} is no longer appointed for "
            f"{appointment.state_code} / {appointment.carrier_name}. Review {recent_count} recent deals."
        ),
    )


def dashboard_metrics(db: Session, tenant_id: str) -> dict:
    as_of = today_utc()
    expiring = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.status == ACTIVE,
        AgentCarrierAppointment.expiration_date.isnot(None),
        AgentCarrierAppointment.expiration_date >= as_of,
        AgentCarrierAppointment.expiration_date <= as_of + timedelta(days=60),
    ).count()
    expired = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        or_(
            AgentCarrierAppointment.status == EXPIRED,
            and_(AgentCarrierAppointment.expiration_date.isnot(None), AgentCarrierAppointment.expiration_date < as_of),
        ),
    ).count()
    missing_access = db.query(DealApprovalLog).filter(
        DealApprovalLog.tenant_id == tenant_id,
        DealApprovalLog.decision == NOT_APPROVED,
    ).count()
    alerts = db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == tenant_id,
        ComplianceEvent.resolved.is_(False),
    ).count()
    high_risk = db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == tenant_id,
        ComplianceEvent.event_type == POTENTIAL_COMPLIANCE_RISK,
        ComplianceEvent.resolved.is_(False),
    ).count()
    total_decisions = db.query(DealApprovalLog).filter(DealApprovalLog.tenant_id == tenant_id).count()
    approved = db.query(DealApprovalLog).filter(
        DealApprovalLog.tenant_id == tenant_id,
        DealApprovalLog.decision == APPROVED,
    ).count()
    approval_rate = round((approved / total_decisions) * 100, 1) if total_decisions else 0.0
    return {
        "appointments_expiring_60d": expiring,
        "appointments_expired": expired,
        "agents_missing_access": missing_access,
        "compliance_alerts": alerts,
        "high_risk_deals": high_risk,
        "approval_rate": approval_rate,
    }


def analytics(db: Session, tenant_id: str) -> dict:
    logs = db.query(DealApprovalLog).filter(DealApprovalLog.tenant_id == tenant_id).all()
    total = len(logs)
    approved = sum(1 for log in logs if log.decision == APPROVED)
    not_approved = sum(1 for log in logs if log.decision == NOT_APPROVED)
    flagged = sum(1 for log in logs if log.decision == FLAGGED)

    def grouped_rate(values: Iterable[DealApprovalLog], attr: str) -> dict:
        buckets: dict[str, dict[str, int | float]] = {}
        for log in values:
            key = getattr(log, attr) or "unknown"
            bucket = buckets.setdefault(key, {"total": 0, "approved": 0, "approval_rate": 0.0})
            bucket["total"] += 1
            if log.decision == APPROVED:
                bucket["approved"] += 1
        for bucket in buckets.values():
            bucket["approval_rate"] = round((bucket["approved"] / bucket["total"]) * 100, 1) if bucket["total"] else 0.0
        return buckets

    expired = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.expiration_date.isnot(None),
        AgentCarrierAppointment.expiration_date < today_utc(),
    ).count()
    violations = db.query(ComplianceEvent).filter(ComplianceEvent.tenant_id == tenant_id).count()
    risk = db.query(ComplianceEvent).filter(
        ComplianceEvent.tenant_id == tenant_id,
        ComplianceEvent.event_type == POTENTIAL_COMPLIANCE_RISK,
    ).count()
    return {
        "total_decisions": total,
        "approved": approved,
        "not_approved": not_approved,
        "flagged": flagged,
        "approval_rate": round((approved / total) * 100, 1) if total else 0.0,
        "carrier_approval": grouped_rate(logs, "carrier"),
        "state_approval": grouped_rate(logs, "state"),
        "compliance_violations": violations,
        "expired_appointments": expired,
        "high_risk_deals": risk,
    }


def import_appointments_csv(db: Session, tenant_id: str, csv_text: str, user_id: Optional[str] = None) -> dict:
    reader = csv.DictReader(StringIO(csv_text))
    required = {"agent_email", "carrier", "state", "effective_date", "expiration_date"}
    missing_columns = required.difference(set(reader.fieldnames or []))
    if missing_columns:
        return {"created": 0, "failed": 1, "errors": [f"Missing columns: {', '.join(sorted(missing_columns))}"]}

    created = 0
    errors = []
    seen = set()
    for row_num, row in enumerate(reader, start=2):
        try:
            email = (row.get("agent_email") or "").strip().lower()
            carrier = (row.get("carrier") or "").strip()
            state = normalize_state(row.get("state") or "")
            key = (email, carrier_key(carrier), state)
            if key in seen:
                raise ValueError("Duplicate row in file")
            seen.add(key)
            user = db.query(User).filter(User.tenant_id == tenant_id, func.lower(User.email) == email).first()
            if not user or not user.agent:
                raise ValueError("Agent not found")
            existing = db.query(AgentCarrierAppointment).filter(
                AgentCarrierAppointment.tenant_id == tenant_id,
                AgentCarrierAppointment.agent_id == user.agent.id,
                AgentCarrierAppointment.carrier_key == carrier_key(carrier),
                AgentCarrierAppointment.state_code == state,
            ).first()
            if existing:
                raise ValueError("Duplicate appointment already exists")
            effective = date.fromisoformat(row["effective_date"])
            expiration = date.fromisoformat(row["expiration_date"]) if row.get("expiration_date") else None
            create_or_update_appointment(
                db,
                tenant_id,
                user.agent.id,
                carrier,
                state,
                effective,
                expiration,
                appointment_number=row.get("appointment_number") or None,
                status=row.get("status") or ACTIVE,
                user_id=user_id,
            )
            created += 1
        except Exception as exc:
            errors.append({"row": row_num, "error": str(exc)})
    return {"created": created, "failed": len(errors), "errors": errors}
