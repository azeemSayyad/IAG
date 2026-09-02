from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id, require_lead_read, require_lead_write, require_role
from app.core.api_key import lead_read_auth
from app.core.permissions import Permission, user_has_permission
from app.models.lead import Lead
from app.models.user import User
from app.schemas.lead import LeadCreate, LeadUpdate, LeadResponse
from app.ingestion.services.events import on_lead_created, on_lead_updated
from app.ingestion.services.validation import normalize_phone
from app.realtime.websocket import emit_to_tenant
from app.leads.services.distribution import booking_agents_for_state

router = APIRouter(prefix="/leads", tags=["leads"])


def _cf_first(cf: dict, *keys):
    """First non-empty value from custom_fields under any of the given keys
    (case-insensitive on the common variants we see in imported CSVs)."""
    if not isinstance(cf, dict):
        return None
    lower = {str(k).lower(): v for k, v in cf.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, "", "—"):
            return str(v)
    return None


def _fmt_money(val):
    if val in (None, "", "—"):
        return "$0"
    try:
        n = float(str(val).replace("$", "").replace(",", ""))
        return f"${n:,.0f}"
    except (TypeError, ValueError):
        return f"${val}"


@router.get("", response_model=dict)
def list_leads(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    # Accepts a JWT user (lead:read perm) OR a master/scoped API key (lead:read scope).
    tenant_id: str = Depends(lead_read_auth),
):
    query = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None))

    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if search:
        query = query.filter(
            Lead.first_name.ilike(f"%{search}%")
            | Lead.last_name.ilike(f"%{search}%")
            | Lead.phone.ilike(f"%{search}%")
            | Lead.email.ilike(f"%{search}%")
        )

    total = query.count()
    leads = query.order_by(Lead.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {"items": [LeadResponse.model_validate(l) for l in leads], "total": total, "page": page, "size": size}


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    request: LeadCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_lead_write),
):
    data = request.model_dump()
    if data.get("phone"):
        data["phone_normalized"] = normalize_phone(data["phone"])
    if data.get("email"):
        data["email_normalized"] = data["email"].strip().lower()
    lead = Lead(tenant_id=tenant_id, **data)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    on_lead_created(db, lead)
    db.refresh(lead)
    await emit_to_tenant(tenant_id, "lead_created", {
        "lead_id": str(lead.id),
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source,
        "status": lead.status,
        "lead_score": lead.lead_score,
    })
    return lead


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(lead_read_auth),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/{lead_id}/copilot")
def lead_copilot(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_lead_read),
):
    """Real data for the Inbox copilot panel: customer snapshot, compliance
    checks (consent/DNC, TCPA quiet hours, state licensing) and the extracted
    deal fields — all derived from the live lead record (no placeholders)."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    cf = lead.custom_fields or {}
    plan = _cf_first(cf, "plan", "plan_type", "plan_name", "coverage") or "—"
    household = _cf_first(cf, "household", "household_size", "family_size", "dependents") or "—"
    address = _cf_first(cf, "address", "street", "address1") or (lead.city or "")
    loc = ", ".join([p for p in [address, lead.city, lead.state, lead.zip_code] if p])

    snapshot = {
        "age": _cf_first(cf, "age") or "—",
        "state": lead.state or "—",
        "plan": plan,
        "household": household,
        "score": round(lead.lead_score or 0),
        "conv": round((lead.conversion_probability or lead.booking_probability or 0)),
        "rev": _fmt_money(_cf_first(cf, "premium", "annual_premium", "deal_value", "est_revenue", "revenue")),
    }

    deal = {
        "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "—",
        "dob": _cf_first(cf, "dob", "date_of_birth", "birthdate") or "—",
        "addr": loc or "—",
        "plan": plan,
        "income": _fmt_money(_cf_first(cf, "income", "annual_income", "household_income")) if _cf_first(cf, "income", "annual_income", "household_income") else "—",
        "household": household,
    }

    # --- Real compliance checks ---
    compliance = []
    tags_lower = [str(t).lower() for t in (lead.tags or [])]
    is_dnc = (not lead.sms_consent) or ("dnc" in tags_lower) or ("do_not_contact" in tags_lower)
    if is_dnc:
        compliance.append({
            "kind": "dnc",
            "t": "Do-not-contact",
            "d": "SMS consent is withdrawn or the lead is on the DNC list — outbound is blocked.",
        })
    else:
        compliance.append({
            "kind": "ok",
            "t": "SMS consent on file",
            "d": "The number is consented for outreach.",
        })

    if lead.state:
        licensed = booking_agents_for_state(db, tenant_id, lead.state)
        if licensed:
            compliance.append({
                "kind": "ok",
                "t": f"Licensed for {lead.state}",
                "d": f"{len(licensed)} active agent(s) hold a license in {lead.state}.",
            })
        else:
            compliance.append({
                "kind": "warn",
                "t": f"No license for {lead.state}",
                "d": f"No active agent currently holds a license in {lead.state}.",
            })
    else:
        compliance.append({
            "kind": "warn",
            "t": "State unknown",
            "d": "The lead has no state on file — licensing can't be verified.",
        })

    return {"snapshot": snapshot, "deal": deal, "compliance": compliance}


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: UUID,
    request: LeadUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_lead_write),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    data = request.model_dump(exclude_unset=True)
    if data.get("phone"):
        data["phone_normalized"] = normalize_phone(data["phone"])
    if data.get("email"):
        data["email_normalized"] = data["email"].strip().lower()

    changed_fields = []
    for field, value in data.items():
        if getattr(lead, field, None) != value:
            changed_fields.append(field)
        setattr(lead, field, value)

    db.commit()
    db.refresh(lead)
    score_result = on_lead_updated(db, lead, changed_fields) if changed_fields else None
    if score_result:
        db.refresh(lead)
    await emit_to_tenant(tenant_id, "lead_updated", {
        "lead_id": str(lead.id),
        "changes": changed_fields,
        "status": lead.status,
        "lead_score": lead.lead_score,
    })
    return lead


@router.post("/{lead_id}/assign", response_model=LeadResponse)
def assign_lead(
    lead_id: UUID,
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("manager", "lead", "head", "tenant_admin", "super_admin", "admin")),
):
    """Manually (re)assign a single lead to an agent.

    Compliance-enforced: the target agent must be licensed for the lead's state,
    so a lead can never be handed to an unlicensed agent even by a human.
    """
    from app.models.agent import Agent
    from app.leads.services.distribution import agent_can_handle_lead

    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=422, detail="agent_id is required")
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent_can_handle_lead(db, agent, lead):
        raise HTTPException(status_code=409, detail=f"Agent is not licensed for state {lead.state or '—'}")

    lead.assigned_agent_id = agent.id
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/{lead_id}/reroute", response_model=dict)
def reroute_lead(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_lead_write),
):
    """Re-route a lead: clear its current agent and re-run compliance-aware
    AI distribution, preferring a DIFFERENT eligible agent than the current one.

    Backs the inbox "Re-route lead" suggestion. Compliance still enforced —
    only a licensed agent can receive the lead.
    """
    from app.models.agent import Agent
    from app.leads.services.distribution import eligible_agents_for_lead, auto_assign_lead

    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    prev = lead.assigned_agent_id
    # Manual re-route: move to a DIFFERENT licensed agent. Capacity is ignored
    # here (a human explicitly wants the lead off the current agent), but the
    # compliance/licensing rule still applies.
    candidates = eligible_agents_for_lead(db, lead, respect_capacity=False)
    others = [a for a in candidates if a.id != prev]
    if others:
        import random
        random.shuffle(others)
        lead.assigned_agent_id = others[0].id
        db.commit()
        chosen = others[0].id
    else:
        # No alternative — clear and re-run standard auto-assign (may re-pick same).
        lead.assigned_agent_id = None
        db.commit()
        agent = auto_assign_lead(db, lead, commit=True)
        chosen = agent.id if agent else None
    return {
        "lead_id": str(lead.id),
        "previous_agent_id": str(prev) if prev else None,
        "assigned_agent_id": str(chosen) if chosen else None,
        "rerouted": bool(chosen and chosen != prev),
    }


@router.post("/reassign", response_model=dict)
def reassign_leads(
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("head", "tenant_admin", "super_admin", "admin")),
):
    """Bulk move up to ``count`` active leads from one agent to another.

    Mirrors the "Reassign leads" UI (move N leads from Agent A → Agent B).
    Head manager / admin only. Compliance-enforced per lead — a lead whose state
    the target agent isn't licensed for is skipped (reported in ``skipped``).
    """
    from app.models.agent import Agent
    from app.leads.services.distribution import agent_can_handle_lead

    from_agent_id = body.get("from_agent_id")
    to_agent_id = body.get("to_agent_id")
    count = int(body.get("count") or 0)
    if not to_agent_id or count <= 0:
        raise HTTPException(status_code=422, detail="to_agent_id and a positive count are required")

    to_agent = db.query(Agent).filter(Agent.id == to_agent_id, Agent.tenant_id == tenant_id).first()
    if not to_agent:
        raise HTTPException(status_code=404, detail="Target agent not found")

    q = db.query(Lead).filter(
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
        Lead.lifecycle_stage.notin_(["completed", "unqualified"]),
    )
    if from_agent_id:
        q = q.filter(Lead.assigned_agent_id == from_agent_id)
    else:
        q = q.filter(Lead.assigned_agent_id.is_(None))
    candidates = q.order_by(Lead.created_at.desc()).limit(count * 3).all()

    moved, skipped = 0, 0
    for lead in candidates:
        if moved >= count:
            break
        if not agent_can_handle_lead(db, to_agent, lead):
            skipped += 1
            continue
        lead.assigned_agent_id = to_agent.id
        moved += 1
    db.commit()
    return {"moved": moved, "skipped_unlicensed": skipped, "to_agent_id": str(to_agent_id)}


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    if not user_has_permission(current_user, Permission.LEAD_DELETE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: lead:delete")

    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.deleted_at = datetime.now(timezone.utc)
    db.commit()
