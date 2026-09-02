"""
Admin Router

Endpoints:
- POST /admin/campaigns — Create campaign
- GET /admin/campaigns — List campaigns
- GET /admin/campaigns/{id} — Get campaign
- PATCH /admin/campaigns/{id} — Update campaign
- DELETE /admin/campaigns/{id} — Delete campaign
- GET /admin/campaigns/{id}/performance — Campaign performance
- GET /admin/analytics/overview — Tenant analytics overview
- GET /admin/analytics/agents — Agent analytics
- GET /admin/analytics/campaigns — Campaign analytics
- GET /admin/analytics/trends — Daily trends
- GET /admin/analytics/ai — AI performance metrics
- GET /admin/analytics/ai/prompts — Best performing prompts
- GET /admin/analytics/ai/timing — Best outreach timing
- GET /admin/analytics/ai/objections — Objection patterns
"""

from typing import Optional
from uuid import UUID
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user, require_role
from app.models.user import User
from app.admin.services.campaigns import (
    create_campaign,
    update_campaign,
    get_campaign,
    list_campaigns,
    delete_campaign,
    get_campaign_performance,
)
from app.admin.services.analytics import (
    get_tenant_analytics,
    get_agent_analytics,
    get_campaign_analytics,
    get_daily_trends,
)
from app.admin.services.ai_analytics import (
    get_ai_performance_metrics,
    get_best_performing_prompts,
    get_best_outreach_timing,
    get_objection_patterns,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = None
    tone: str = "friendly"
    prompt_template: Optional[str] = None
    objection_prompts: dict = {}
    max_retries: int = 3
    retry_delay_hours: int = 24
    retry_tones: list = ["friendly", "professional", "urgent"]
    booking_enabled: bool = True
    slot_duration_minutes: int = 15
    max_days_ahead: int = 3
    business_hours_start: int = 10
    business_hours_end: int = 21
    target_sources: list = []
    target_states: list = []
    min_lead_score: int = 0
    max_lead_score: int = 100


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    tone: Optional[str] = None
    prompt_template: Optional[str] = None
    objection_prompts: Optional[dict] = None
    max_retries: Optional[int] = None
    retry_delay_hours: Optional[int] = None
    retry_tones: Optional[list] = None
    booking_enabled: Optional[bool] = None
    target_sources: Optional[list] = None
    target_states: Optional[list] = None
    min_lead_score: Optional[int] = None
    max_lead_score: Optional[int] = None


# Campaign endpoints
@router.post("/campaigns")
def create_campaign_endpoint(
    request: CampaignCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Create a new campaign."""
    campaign = create_campaign(db, tenant_id, request.model_dump())
    return {"id": str(campaign.id), "name": campaign.name}


@router.get("/campaigns")
def list_campaigns_endpoint(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List all campaigns."""
    campaigns = list_campaigns(db, tenant_id, status)
    return {
        "campaigns": [
            {
                "id": str(c.id),
                "name": c.name,
                "status": c.status,
                "tone": c.tone,
                "total_leads": c.total_leads,
                "total_contacted": c.total_contacted,
                "total_replied": c.total_replied,
                "total_booked": c.total_booked,
                "total_won": c.total_won,
            }
            for c in campaigns
        ]
    }


@router.get("/campaigns/{campaign_id}")
def get_campaign_endpoint(
    campaign_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get a campaign by ID."""
    campaign = get_campaign(db, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.patch("/campaigns/{campaign_id}")
def update_campaign_endpoint(
    campaign_id: UUID,
    request: CampaignUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Update a campaign."""
    campaign = update_campaign(db, campaign_id, tenant_id, request.model_dump(exclude_unset=True))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"id": str(campaign.id), "name": campaign.name}


@router.delete("/campaigns/{campaign_id}")
def delete_campaign_endpoint(
    campaign_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Delete a campaign."""
    if not delete_campaign(db, campaign_id, tenant_id):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"message": "Campaign deleted"}


@router.get("/campaigns/{campaign_id}/performance")
def campaign_performance_endpoint(
    campaign_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get campaign performance metrics."""
    return get_campaign_performance(db, campaign_id, tenant_id)


# Analytics endpoints
@router.get("/analytics/overview")
def analytics_overview(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get tenant analytics overview."""
    return get_tenant_analytics(db, tenant_id, start_date, end_date)


@router.get("/analytics/agents")
def analytics_agents(
    agent_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agent analytics."""
    return {"agents": get_agent_analytics(db, tenant_id, agent_id, start_date, end_date)}


@router.get("/analytics/campaigns")
def analytics_campaigns(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get campaign analytics."""
    return {"campaigns": get_campaign_analytics(db, tenant_id, start_date, end_date)}


@router.get("/analytics/trends")
def analytics_trends(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get daily trends."""
    return {"trends": get_daily_trends(db, tenant_id, days)}


@router.get("/analytics/ai")
def ai_analytics(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get AI performance metrics."""
    return get_ai_performance_metrics(db, tenant_id, start_date, end_date)


@router.get("/analytics/ai/prompts")
def ai_prompts(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get best performing prompts."""
    return {"prompts": get_best_performing_prompts(db, tenant_id, limit)}


@router.get("/analytics/ai/timing")
def ai_timing(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get best outreach timing."""
    return get_best_outreach_timing(db, tenant_id)


@router.get("/analytics/ai/objections")
def ai_objections(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get objection patterns."""
    return get_objection_patterns(db, tenant_id)


# ===== User management (admin-only): create users, set/reset passwords =====
# Admin (tenant_admin / super_admin) can add system users and (re)set their
# passwords so every login has its own unique password.

from app.core.security import hash_password
from app.models.agent import Agent

_ALLOWED_NEW_ROLES = {"agent", "lead", "head", "manager", "tenant_admin"}


def _assignable_roles(current_user: User) -> set[str]:
    """Roles the current user is allowed to assign when creating/updating a user.

    Only a "dev" can create or promote users to the "dev" role — admins cannot.
    """
    roles = set(_ALLOWED_NEW_ROLES)
    if current_user.role == "dev":
        roles.add("dev")
    return roles
_AGENT_ROLES = {"agent", "lead", "manager"}  # roles that get their own Agent record


class CreateUserRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    role: str = "agent"
    personal_phone: Optional[str] = None   # agent's personal work number (AI never uses it)


class SetPasswordRequest(BaseModel):
    password: str


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None   # active | suspended


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """List all users in the tenant (admin only)."""
    q = db.query(User).filter(User.tenant_id == tenant_id, User.deleted_at.is_(None))
    # Dev accounts are managed only by devs — hide them from non-dev admins so
    # they can't see, edit, or remove a dev (mirrors the hidden "Dev" role option).
    if current_user.role != "dev":
        q = q.filter(User.role != "dev")
    users = q.order_by(User.created_at.desc()).all()
    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "role": u.role,
                "status": u.status,
                "avatar_url": u.avatar_url,
            }
            for u in users
        ],
        "total": len(users),
    }


@router.post("/users", status_code=201)
def create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Create a system user (admin only) with a unique password."""
    role = (request.role or "agent").strip()
    if role not in _assignable_roles(current_user):
        raise HTTPException(status_code=422, detail=f"Invalid role: {role}")
    if len(request.password or "") < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    email = request.email.strip().lower()
    if db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first():
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    prefs = {}
    if request.personal_phone:
        # Admin-assigned personal work number. Stored on the USER, never on the
        # AI sender pool — the AI never uses it for outbound. Agent can't edit it.
        prefs["personal_phone"] = request.personal_phone.strip()

    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(request.password),
        first_name=request.first_name.strip(),
        last_name=request.last_name.strip(),
        role=role,
        status="active",
        preferences=prefs,
    )
    db.add(user)
    db.flush()

    # Operator roles get an Agent record (their own calendar/inbox).
    if role in _AGENT_ROLES:
        db.add(Agent(tenant_id=tenant_id, user_id=user.id, status="active"))
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "email": user.email, "role": user.role}


@router.post("/users/{user_id}/password")
def set_user_password(
    user_id: UUID,
    request: SetPasswordRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Reset a user's password (admin only)."""
    if len(request.password or "") < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    user = db.query(User).filter(
        User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None)
    ).first()
    if not user or (user.role == "dev" and current_user.role != "dev"):
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(request.password)
    db.commit()
    return {"id": str(user.id), "status": "password_updated"}


@router.patch("/users/{user_id}")
def update_user(
    user_id: UUID,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Update a user's role and/or status (admin only)."""
    user = db.query(User).filter(
        User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None)
    ).first()
    if not user or (user.role == "dev" and current_user.role != "dev"):
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.id) == str(current_user.id) and (request.role or request.status):
        raise HTTPException(status_code=409, detail="You cannot change your own role or status")

    if request.role is not None:
        role = request.role.strip()
        if role not in _assignable_roles(current_user):
            raise HTTPException(status_code=422, detail=f"Invalid role: {role}")
        user.role = role
        # Operator roles need an Agent record (calendar/inbox/distribution). Create
        # one if the user is becoming an operator and doesn't have one yet.
        if role in _AGENT_ROLES:
            existing = db.query(Agent).filter(Agent.user_id == user.id).first()
            if not existing:
                db.add(Agent(tenant_id=tenant_id, user_id=user.id, status="active"))

    if request.status is not None:
        st = request.status.strip().lower()
        if st not in ("active", "suspended"):
            raise HTTPException(status_code=422, detail="status must be active or suspended")
        user.status = st
        # Suspended operators stop receiving leads (their Agent record goes inactive).
        agent = db.query(Agent).filter(Agent.user_id == user.id).first()
        if agent:
            agent.status = "active" if st == "active" else "inactive"

    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "role": user.role, "status": user.status}


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Soft-delete a user (admin only). Also deactivates their Agent record so
    they stop receiving leads/booking."""
    if str(user_id) == str(current_user.id):
        raise HTTPException(status_code=409, detail="You cannot remove your own account")
    user = db.query(User).filter(
        User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None)
    ).first()
    if not user or (user.role == "dev" and current_user.role != "dev"):
        raise HTTPException(status_code=404, detail="User not found")
    user.deleted_at = datetime.now(timezone.utc)
    agent = db.query(Agent).filter(Agent.user_id == user.id).first()
    if agent:
        agent.status = "inactive"
    db.commit()
    return None


# --- Per-agent caller ID numbers (Sinch) ---

@router.get("/numbers")
def list_agent_numbers(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """List agents with their assigned caller ID numbers (admin view)."""
    from app.models.agent import Agent
    agents = db.query(Agent).filter(Agent.tenant_id == tenant_id).all()
    items = []
    for a in agents:
        u = db.query(User).filter(User.id == a.user_id).first()
        items.append({
            "agent_id": str(a.id),
            "user_id": str(a.user_id),
            "name": (f"{u.first_name} {u.last_name}".strip() if u else str(a.id)),
            "email": (u.email if u else None),
            "caller_number": a.caller_number,
        })
    return {"items": items, "total": len(items)}


@router.post("/users/{user_id}/caller-number")
def set_caller_number(
    user_id: UUID,
    request: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Assign or change an agent's caller ID number (admin only).

    Accepts E.164 (e.g. +12125551001) or blank to clear. Enforces one number per
    agent within the tenant (no two agents share a caller ID)."""
    import re
    from app.models.agent import Agent

    number = (request.get("caller_number") or "").strip()
    if number:
        if not re.match(r"^\+?[1-9]\d{6,15}$", number.replace(" ", "").replace("-", "")):
            raise HTTPException(status_code=422, detail="Enter a valid phone number in E.164 format, e.g. +12125551001")
        number = number.replace(" ", "").replace("-", "")
        if not number.startswith("+"):
            number = "+" + number

    agent = db.query(Agent).filter(Agent.user_id == user_id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found for this user")

    if number:
        clash = db.query(Agent).filter(
            Agent.tenant_id == tenant_id, Agent.caller_number == number, Agent.id != agent.id
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail="That number is already assigned to another agent")

    agent.caller_number = number or None
    db.commit()
    return {"agent_id": str(agent.id), "user_id": str(user_id), "caller_number": agent.caller_number}
