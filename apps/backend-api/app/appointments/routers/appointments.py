from typing import Optional, Any
from uuid import UUID
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_appointment_read, require_appointment_write
from app.core.audit import log_ai_action
from app.models.appointment import Appointment, AppointmentDisposition
from app.models.lead import Lead
from app.models.agent import Agent
from app.models.user import User
from app.models.conversation import Conversation
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentResponse,
    AppointmentDispositionCreate,
    AppointmentDispositionResponse,
    DispositionOption,
)
from app.booking.services.reminders import schedule_reminders
from app.realtime.websocket import emit_to_tenant

router = APIRouter(prefix="/appointments", tags=["appointments"])


DISPOSITION_OPTIONS: dict[str, dict[str, Any]] = {
    "sale": {
        "label": "Sale",
        "description": "Customer picked up and insurance was sold.",
        "outcome_category": "sold",
        "customer_picked_up": True,
        "insurance_sold": True,
        "appointment_status": "completed",
        "lead_status": "completed",
    },
    "appointment_set": {
        "label": "Appointment Set",
        "description": "Customer picked up and another appointment/follow-up was set.",
        "outcome_category": "follow_up",
        "customer_picked_up": True,
        "insurance_sold": False,
        "appointment_status": "completed",
        "lead_status": "nurture",
    },
    "medicare": {
        "label": "Medicare",
        "description": "Customer picked up and should be routed to Medicare handling.",
        "outcome_category": "routed",
        "customer_picked_up": True,
        "insurance_sold": False,
        "appointment_status": "completed",
        "lead_status": "qualified",
    },
    "attempted": {
        "label": "Attempted",
        "description": "Agent attempted the appointment but the customer did not answer.",
        "outcome_category": "no_answer",
        "customer_picked_up": False,
        "insurance_sold": False,
        "appointment_status": "no_show",
        "lead_status": "no_show",
    },
    "couldnt_sell": {
        "label": "Couldn't Sell",
        "description": "Customer picked up but insurance was not sold.",
        "outcome_category": "not_sold",
        "customer_picked_up": True,
        "insurance_sold": False,
        "appointment_status": "completed",
        "lead_status": "nurture",
    },
    "unqualified": {
        "label": "Unqualified",
        "description": "Customer picked up but is not eligible or not a fit.",
        "outcome_category": "unqualified",
        "customer_picked_up": True,
        "insurance_sold": False,
        "appointment_status": "completed",
        "lead_status": "unqualified",
    },
    "wrong_number": {
        "label": "Wrong Number",
        "description": "Phone number is invalid or belongs to the wrong person.",
        "outcome_category": "bad_contact",
        "customer_picked_up": False,
        "insurance_sold": False,
        "appointment_status": "completed",
        "lead_status": "unqualified",
    },
}


def _agent_name(db: Session, agent: Agent | None) -> str | None:
    if not agent:
        return None
    user = db.query(User).filter(User.id == agent.user_id).first()
    return (f"{user.first_name} {user.last_name}".strip() if user else None)


def _customer_name(lead: Lead | None) -> str:
    if not lead:
        return "Unknown customer"
    return f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "Unknown customer"


def _agent_for_user(db: Session, user: User) -> Agent | None:
    return db.query(Agent).filter(Agent.user_id == user.id, Agent.tenant_id == user.tenant_id).first()


def _can_access_agent(current_user: User, agent_id: UUID, db: Session) -> bool:
    if current_user.role in {"super_admin", "tenant_admin", "manager", "lead", "head", "admin"}:
        return True
    agent = _agent_for_user(db, current_user)
    return bool(agent and str(agent.id) == str(agent_id))


def _day_bounds(value: date | None, end: bool = False) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max if end else time.min).replace(tzinfo=timezone.utc)


def _serialize_disposition(row: AppointmentDisposition) -> dict[str, Any]:
    data = AppointmentDispositionResponse.model_validate(row).model_dump()
    if isinstance(data.get("premium_amount"), Decimal):
        data["premium_amount"] = float(data["premium_amount"])
    return data


def _disposition_query(
    db: Session,
    tenant_id: str,
    current_user: User,
    agent_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    disposition_key: Optional[str] = None,
):
    query = db.query(AppointmentDisposition).filter(AppointmentDisposition.tenant_id == tenant_id)
    if current_user.role == "agent":
        current_agent = _agent_for_user(db, current_user)
        if not current_agent:
            return query.filter(False)
        query = query.filter(AppointmentDisposition.agent_id == current_agent.id)
    elif agent_id:
        query = query.filter(AppointmentDisposition.agent_id == agent_id)
    if date_from:
        query = query.filter(AppointmentDisposition.appointment_start_time >= _day_bounds(date_from))
    if date_to:
        query = query.filter(AppointmentDisposition.appointment_start_time <= _day_bounds(date_to, end=True))
    if disposition_key:
        query = query.filter(AppointmentDisposition.disposition_key == disposition_key)
    return query


def _escape_pdf_text(value: Any) -> str:
    text_value = str(value or "")
    return text_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_disposition_pdf(title: str, rows: list[AppointmentDisposition]) -> bytes:
    lines = [title, f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", ""]
    for row in rows:
        slot = row.appointment_start_time.strftime("%Y-%m-%d %H:%M")
        sold = "sold" if row.insurance_sold else "not sold"
        picked = "picked up" if row.customer_picked_up else "no pickup"
        lines.append(f"{slot} | {row.agent_name or '-'} | {row.customer_name} | {row.customer_phone}")
        lines.append(f"  {row.disposition_label} | {picked} | {sold}")
        if row.notes:
            lines.append(f"  Notes: {row.notes[:120]}")
    if len(lines) == 3:
        lines.append("No dispositions found.")

    y = 760
    content = ["BT", "/F1 10 Tf"]
    for line in lines[:58]:
        content.append(f"72 {y} Td ({_escape_pdf_text(line)}) Tj")
        content.append(f"-72 -14 Td")
        y -= 14
    content.append("ET")
    stream = "\n".join(content).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(pdf)


@router.get("", response_model=dict)
def list_appointments(
    status_filter: Optional[str] = Query(None, alias="status"),
    agent_id: Optional[UUID] = None,
    date_filter: Optional[date] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_read),
):
    query = db.query(Appointment).filter(Appointment.tenant_id == tenant_id)

    # Privacy: operators (agent / team leader / manager) see only their OWN
    # appointments. Head managers / admins / super admins see all (oversight).
    role = getattr(current_user, "role", None)
    if role in ("agent", "lead", "manager"):
        my_agent = db.query(Agent).filter(Agent.user_id == current_user.id).first()
        query = query.filter(Appointment.agent_id == (my_agent.id if my_agent else None))

    if status_filter:
        query = query.filter(Appointment.status == status_filter)
    if agent_id:
        query = query.filter(Appointment.agent_id == agent_id)
    if date_filter:
        query = query.filter(
            Appointment.start_time >= str(date_filter),
            Appointment.start_time < str(date_filter + timedelta(days=1)),
        )

    total = query.count()
    appointments = query.order_by(Appointment.start_time.desc()).offset((page - 1) * size).limit(size).all()

    items = []
    for a in appointments:
        apt_dict = AppointmentResponse.model_validate(a).model_dump()
        # Enrich with lead name
        lead = db.query(Lead).filter(Lead.id == a.lead_id).first() if a.lead_id else None
        apt_dict["lead_name"] = (lead.first_name + " " + lead.last_name).strip() if lead else None
        apt_dict["lead_phone"] = lead.phone if lead else None
        apt_dict["lead_state"] = lead.state if lead else None
        apt_dict["plan"] = lead.source if lead else None
        # Lets the UI split the Upcoming view: ai_sms_call_now == a "Call now" lead.
        apt_dict["booking_source"] = getattr(a, "booking_source", None)
        # Enrich with agent name
        agent = db.query(Agent).filter(Agent.id == a.agent_id).first() if a.agent_id else None
        if agent:
            agent_user = db.query(User).filter(User.id == agent.user_id).first()
            apt_dict["agent_name"] = (agent_user.first_name + " " + agent_user.last_name).strip() if agent_user else None
        else:
            apt_dict["agent_name"] = None
        items.append(apt_dict)

    return {"items": items, "total": total, "page": page, "size": size}


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    request: AppointmentCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_write),
):
    # Check for overlapping appointments
    overlap = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.agent_id == request.agent_id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time < request.end_time,
            Appointment.end_time > request.start_time,
        )
        .first()
    )
    if overlap:
        raise HTTPException(status_code=409, detail="Time slot already booked for this agent")

    appointment = Appointment(tenant_id=tenant_id, **request.model_dump())
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    schedule_reminders(db, appointment)
    await emit_to_tenant(tenant_id, "appointment_created", {
        "appointment_id": str(appointment.id),
        "lead_id": str(appointment.lead_id),
        "agent_id": str(appointment.agent_id),
        "start_time": appointment.start_time.isoformat(),
        "end_time": appointment.end_time.isoformat(),
        "status": appointment.status,
    })
    return appointment


@router.get("/dispositions/options", response_model=list[DispositionOption])
def get_disposition_options(
    current_user: User = Depends(require_appointment_read),
):
    return [
        {
            "key": key,
            "label": value["label"],
            "description": value["description"],
            "outcome_category": value["outcome_category"],
            "customer_picked_up": value["customer_picked_up"],
            "insurance_sold": value["insurance_sold"],
        }
        for key, value in DISPOSITION_OPTIONS.items()
    ]


@router.get("/dispositions", response_model=dict)
def list_dispositions(
    agent_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    disposition_key: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_read),
):
    query = _disposition_query(db, tenant_id, current_user, agent_id, date_from, date_to, disposition_key)
    total = query.count()
    rows = query.order_by(AppointmentDisposition.appointment_start_time.desc()).offset((page - 1) * size).limit(size).all()
    all_rows = query.all()

    by_agent: dict[str, dict[str, Any]] = {}
    by_disposition = {key: 0 for key in DISPOSITION_OPTIONS}
    by_day_agent: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        by_disposition[row.disposition_key] = by_disposition.get(row.disposition_key, 0) + 1
        agent_key = str(row.agent_id)
        agent_bucket = by_agent.setdefault(agent_key, {
            "agent_id": agent_key,
            "agent_name": row.agent_name or "Unknown agent",
            "total": 0,
            "sold": 0,
            "picked_up": 0,
            "counts": {key: 0 for key in DISPOSITION_OPTIONS},
        })
        agent_bucket["total"] += 1
        agent_bucket["sold"] += 1 if row.insurance_sold else 0
        agent_bucket["picked_up"] += 1 if row.customer_picked_up else 0
        agent_bucket["counts"][row.disposition_key] = agent_bucket["counts"].get(row.disposition_key, 0) + 1

        day_key = row.appointment_start_time.date().isoformat()
        day_agent_key = f"{day_key}:{agent_key}"
        day_bucket = by_day_agent.setdefault(day_agent_key, {
            "date": day_key,
            "agent_id": agent_key,
            "agent_name": row.agent_name or "Unknown agent",
            "total": 0,
            "sold": 0,
            "counts": {key: 0 for key in DISPOSITION_OPTIONS},
        })
        day_bucket["total"] += 1
        day_bucket["sold"] += 1 if row.insurance_sold else 0
        day_bucket["counts"][row.disposition_key] = day_bucket["counts"].get(row.disposition_key, 0) + 1

    return {
        "items": [_serialize_disposition(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
        "summary": {
            "total": len(all_rows),
            "sold": sum(1 for row in all_rows if row.insurance_sold),
            "picked_up": sum(1 for row in all_rows if row.customer_picked_up),
            "by_disposition": by_disposition,
            "by_agent": list(by_agent.values()),
            "by_day_agent": sorted(by_day_agent.values(), key=lambda item: (item["date"], item["agent_name"]), reverse=True),
        },
    }


@router.get("/dispositions/export.pdf")
def export_dispositions_pdf(
    agent_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    disposition_key: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_read),
):
    rows = (
        _disposition_query(db, tenant_id, current_user, agent_id, date_from, date_to, disposition_key)
        .order_by(AppointmentDisposition.appointment_start_time.desc())
        .limit(500)
        .all()
    )
    pdf = _build_disposition_pdf("Appointment Disposition Report", rows)
    filename = f"appointment-dispositions-{datetime.now(timezone.utc).date().isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{appointment_id}/disposition", response_model=AppointmentDispositionResponse)
async def submit_appointment_disposition(
    appointment_id: UUID,
    request: AppointmentDispositionCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_write),
):
    option = DISPOSITION_OPTIONS.get(request.disposition_key)
    if not option:
        raise HTTPException(status_code=422, detail=f"Invalid disposition: {request.disposition_key}")

    appointment = (
        db.query(Appointment)
        .filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant_id)
        .first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if not _can_access_agent(current_user, appointment.agent_id, db):
        raise HTTPException(status_code=403, detail="You cannot disposition another agent's appointment")

    lead = db.query(Lead).filter(Lead.id == appointment.lead_id, Lead.tenant_id == tenant_id).first()
    agent = db.query(Agent).filter(Agent.id == appointment.agent_id, Agent.tenant_id == tenant_id).first()
    customer_name = _customer_name(lead)
    customer_phone = lead.phone if lead else ""
    agent_name = _agent_name(db, agent)

    disposition = (
        db.query(AppointmentDisposition)
        .filter(AppointmentDisposition.appointment_id == appointment.id)
        .first()
    )
    if not disposition:
        disposition = AppointmentDisposition(
            tenant_id=appointment.tenant_id,
            appointment_id=appointment.id,
            lead_id=appointment.lead_id,
            agent_id=appointment.agent_id,
        )
        db.add(disposition)

    disposition.submitted_by_user_id = current_user.id
    disposition.disposition_key = request.disposition_key
    disposition.disposition_label = option["label"]
    disposition.outcome_category = option["outcome_category"]
    disposition.customer_picked_up = option["customer_picked_up"] if request.customer_picked_up is None else request.customer_picked_up
    disposition.insurance_sold = option["insurance_sold"] if request.insurance_sold is None else request.insurance_sold
    disposition.customer_name = customer_name
    disposition.customer_phone = customer_phone
    disposition.appointment_start_time = appointment.start_time
    disposition.appointment_end_time = appointment.end_time
    disposition.agent_name = agent_name
    disposition.notes = request.notes
    disposition.call_duration_seconds = request.call_duration_seconds
    disposition.sale_carrier = request.sale_carrier
    disposition.sale_product = request.sale_product
    disposition.premium_amount = request.premium_amount
    disposition.policy_number = request.policy_number
    disposition.extra = request.extra or {}
    disposition.updated_at = datetime.now(timezone.utc)

    appointment.status = option["appointment_status"]
    appointment.disposition = request.disposition_key
    appointment.notes = request.notes
    appointment.call_duration_seconds = request.call_duration_seconds
    appointment.updated_at = datetime.now(timezone.utc)

    if lead:
        lead.status = option["lead_status"]
        lead.lifecycle_stage = option["lead_status"]
        lead.updated_at = datetime.now(timezone.utc)

    conversation = db.query(Conversation).filter(Conversation.lead_id == appointment.lead_id).first()
    if conversation:
        conversation.status = "closed" if request.disposition_key not in {"appointment_set"} else "active"
        conversation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(disposition)
    db.refresh(appointment)

    log_ai_action(
        tenant_id=tenant_id,
        action="appointment_disposition_saved",
        resource_type="appointment",
        resource_id=str(appointment.id),
        details={
            "disposition": request.disposition_key,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "agent_id": str(appointment.agent_id),
            "insurance_sold": disposition.insurance_sold,
        },
    )
    await emit_to_tenant(tenant_id, "appointment_disposition_saved", {
        "appointment_id": str(appointment.id),
        "disposition_id": str(disposition.id),
        "agent_id": str(appointment.agent_id),
        "lead_id": str(appointment.lead_id),
        "disposition_key": disposition.disposition_key,
        "disposition_label": disposition.disposition_label,
        "status": appointment.status,
        "insurance_sold": disposition.insurance_sold,
    })

    return disposition


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_read),
):
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id, Appointment.tenant_id == tenant_id
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: UUID,
    request: AppointmentUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_write),
):
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id, Appointment.tenant_id == tenant_id
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(appointment, field, value)

    db.commit()
    db.refresh(appointment)
    await emit_to_tenant(tenant_id, "appointment_updated", {
        "appointment_id": str(appointment.id),
        "lead_id": str(appointment.lead_id),
        "agent_id": str(appointment.agent_id),
        "start_time": appointment.start_time.isoformat(),
        "end_time": appointment.end_time.isoformat(),
        "status": appointment.status,
        "disposition": appointment.disposition,
    })
    return appointment


from pydantic import BaseModel


class AppointmentNotifyRequest(BaseModel):
    kind: str = "reminder"          # "reminder" | "reschedule"
    message: Optional[str] = None   # optional custom override


@router.post("/{appointment_id}/notify")
def notify_appointment(
    appointment_id: UUID,
    request: AppointmentNotifyRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_appointment_write),
):
    """Send the customer a manual reminder or reschedule notice for an appointment.

    Additive agent-facing action: it REUSES the existing SMS boundary
    (`send_sms_to_lead`) and does not alter the CSV->booking pipeline.
    """
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id, Appointment.tenant_id == tenant_id
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if not lead or not lead.phone:
        raise HTTPException(status_code=404, detail="Lead or phone number not found")

    # Customer-facing time shown in the LEAD's own timezone.
    from app.core.timezones import lead_zone
    start = appointment.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    local = start.astimezone(lead_zone(getattr(lead, "timezone", None)))
    when = local.strftime("%a %b %d at %I:%M %p %Z").replace(" 0", " ").strip()

    first = lead.first_name or "there"
    if request.message:
        message = request.message
    elif request.kind == "reschedule":
        message = (
            f"Hi {first}, your appointment has been rescheduled to {when}. "
            "Reply here if that time doesn't work for you."
        )
    else:
        message = f"Hi {first}, a quick reminder about your appointment on {when}. See you then!"

    from app.ai.services.communication_provider import send_sms_to_lead
    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=str(appointment.tenant_id),
        lead_id=str(lead.id),
    )
    log_ai_action(
        tenant_id=str(appointment.tenant_id),
        action=f"appointment_{request.kind}_notify",
        resource_type="appointment",
        resource_id=str(appointment.id),
        details={"success": bool(result.get("success")), "kind": request.kind},
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Failed to send message")
    return {"success": True, "kind": request.kind, "message": message}
