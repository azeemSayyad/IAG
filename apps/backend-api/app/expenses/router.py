"""Company expense tracking — OWNER ONLY.

Gated with require_role("super_admin"), i.e. the owner/CEO (plus "dev", which
passes every gate in this app by design). Managers and tenant_admins deliberately
cannot see payroll.

Shape of the domain (see app/models/expense.py):
  categories  a lookup, seeded per tenant on first read
  items       standing commitments; posting one CREATES a ledger entry
  entries     the ledger — the only thing ever summed, agent hours included
  rates       append-only hourly rate history

Every write logs to audit_logs (resource_type "expense_*"), and entries are
voided rather than deleted, so /expenses/audit can reconstruct the whole history.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import log_audit_event
from app.core.database import get_db
from app.core.date_ranges import resolve_range
from app.core.deps import get_current_active_user, get_tenant_id, require_role
from app.expenses import services
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.expense import AgentRate, ExpenseCategory, ExpenseEntry, ExpenseItem
from app.models.user import User
from app.schemas.expense import (
    AgentPayRow,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    EntryCreate,
    EntryResponse,
    EntryUpdate,
    HoursCreate,
    ItemCreate,
    ItemPostRequest,
    ItemResponse,
    ItemUpdate,
    RateResponse,
    RateSet,
    SummaryResponse,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])

# The owner gate. "dev" also satisfies this (require_role lets dev through every
# check on purpose); no other role can reach ANY endpoint in this file.
_require_owner = require_role("super_admin")

AUDIT_RESOURCES = ("expense_entry", "expense_item", "expense_category", "agent_rate")


# ── helpers ──────────────────────────────────────────────────────────────────

def _audit(db: Session, tenant_id: str, user: User, action: str, resource: str,
           resource_id, details: dict) -> None:
    """Best-effort audit write — a logging failure must never lose the expense."""
    try:
        log_audit_event(
            tenant_id=tenant_id, action=action, resource_type=resource,
            resource_id=str(resource_id) if resource_id else None,
            user_id=str(user.id), details=details, db=db,
        )
    except Exception:
        db.rollback()


def _slugify(name: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in (name or "").lower()).strip("_")
    return (out or "category")[:60]


def _category_map(db: Session, tenant_id: str) -> dict:
    return {c.id: c for c in services.ensure_categories(db, tenant_id)}


def _agent_names(db: Session, tenant_id: str) -> dict:
    """agent_id -> display name (one query, no N+1)."""
    out = {}
    for aid, fn, ln, email in (
        db.query(Agent.id, User.first_name, User.last_name, User.email)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.tenant_id == tenant_id).all()
    ):
        out[aid] = (f"{fn or ''} {ln or ''}".strip() or email or str(aid))
    return out


def _entry_out(e: ExpenseEntry, cats: dict, names: dict) -> EntryResponse:
    cat = cats.get(e.category_id)
    return EntryResponse(
        id=e.id, category_id=e.category_id,
        category_name=cat.name if cat else None,
        category_color=cat.color if cat else None,
        item_id=e.item_id, agent_id=e.agent_id,
        agent_name=names.get(e.agent_id) if e.agent_id else None,
        description=e.description, vendor=e.vendor, amount_cents=int(e.amount_cents or 0),
        quantity=e.quantity, unit=e.unit, unit_rate_cents=e.unit_rate_cents,
        incurred_on=e.incurred_on, source=e.source, notes=e.notes,
        voided_at=e.voided_at, created_at=e.created_at,
    )


def _own_category(db: Session, tenant_id: str, category_id: UUID) -> ExpenseCategory:
    cat = (
        db.query(ExpenseCategory)
        .filter(ExpenseCategory.tenant_id == tenant_id, ExpenseCategory.id == category_id)
        .first()
    )
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


def _own_entry(db: Session, tenant_id: str, entry_id: UUID) -> ExpenseEntry:
    e = (
        db.query(ExpenseEntry)
        .filter(ExpenseEntry.tenant_id == tenant_id, ExpenseEntry.id == entry_id)
        .first()
    )
    if not e:
        raise HTTPException(status_code=404, detail="Expense entry not found")
    return e


def _own_item(db: Session, tenant_id: str, item_id: UUID) -> ExpenseItem:
    it = (
        db.query(ExpenseItem)
        .filter(ExpenseItem.tenant_id == tenant_id, ExpenseItem.id == item_id)
        .first()
    )
    if not it:
        raise HTTPException(status_code=404, detail="Expense item not found")
    return it


def _window(from_: Optional[str], to: Optional[str]) -> tuple[date, date]:
    """Resolve the picker window to inclusive calendar dates, using the same
    Eastern-day helper every other number-bearing page uses."""
    _s, _e, from_d, to_d = resolve_range(from_, to)
    return from_d, to_d


# ── categories ───────────────────────────────────────────────────────────────

@router.get("/categories", response_model=list[CategoryResponse])
def list_categories(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    return services.ensure_categories(db, tenant_id)


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    services.ensure_categories(db, tenant_id)
    slug = _slugify(payload.slug or payload.name)
    if db.query(ExpenseCategory).filter(
        ExpenseCategory.tenant_id == tenant_id, ExpenseCategory.slug == slug
    ).first():
        raise HTTPException(status_code=409, detail="A category with that name already exists")
    cat = ExpenseCategory(
        tenant_id=tenant_id, slug=slug, name=payload.name.strip(),
        default_behavior=payload.default_behavior, color=payload.color,
        sort_order=payload.sort_order,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    _audit(db, tenant_id, user, "create", "expense_category", cat.id, {"name": cat.name})
    return cat


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    cat = _own_category(db, tenant_id, category_id)
    before = {"name": cat.name, "color": cat.color, "is_active": cat.is_active}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cat, field, value)
    db.commit()
    db.refresh(cat)
    _audit(db, tenant_id, user, "update", "expense_category", cat.id,
           {"before": before, "after": {"name": cat.name, "color": cat.color, "is_active": cat.is_active}})
    return cat


# ── standing commitments ─────────────────────────────────────────────────────

@router.get("/items", response_model=list[ItemResponse])
def list_items(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    cats = _category_map(db, tenant_id)
    q = db.query(ExpenseItem).filter(ExpenseItem.tenant_id == tenant_id)
    if not include_inactive:
        q = q.filter(ExpenseItem.is_active.is_(True))
    items = q.order_by(ExpenseItem.name).all()
    today = date.today()
    out = []
    for it in items:
        cat = cats.get(it.category_id)
        out.append(ItemResponse(
            id=it.id, category_id=it.category_id,
            category_name=cat.name if cat else None,
            name=it.name, vendor=it.vendor, behavior=it.behavior,
            amount_cents=int(it.amount_cents or 0), interval=it.interval,
            start_date=it.start_date, end_date=it.end_date, is_active=it.is_active,
            notes=it.notes,
            monthly_cents=services.monthly_cents(it) if services.item_is_live(it, today) else 0,
            posted_this_period=services.posted_in_month(db, tenant_id, it.id, today) is not None,
            created_at=it.created_at,
        ))
    return out


@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    _own_category(db, tenant_id, payload.category_id)
    it = ExpenseItem(
        tenant_id=tenant_id, created_by=user.id,
        **payload.model_dump(),
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    _audit(db, tenant_id, user, "create", "expense_item", it.id,
           {"name": it.name, "amount_cents": int(it.amount_cents or 0), "interval": it.interval})
    cat = _category_map(db, tenant_id).get(it.category_id)
    return ItemResponse(
        id=it.id, category_id=it.category_id, category_name=cat.name if cat else None,
        name=it.name, vendor=it.vendor,
        behavior=it.behavior, amount_cents=int(it.amount_cents or 0), interval=it.interval,
        start_date=it.start_date, end_date=it.end_date, is_active=it.is_active, notes=it.notes,
        monthly_cents=services.monthly_cents(it), posted_this_period=False, created_at=it.created_at,
    )


@router.patch("/items/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: UUID,
    payload: ItemUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    it = _own_item(db, tenant_id, item_id)
    changes = payload.model_dump(exclude_unset=True)
    if "category_id" in changes and changes["category_id"]:
        _own_category(db, tenant_id, changes["category_id"])
    before = {"name": it.name, "amount_cents": int(it.amount_cents or 0),
              "interval": it.interval, "is_active": it.is_active}
    for field, value in changes.items():
        setattr(it, field, value)
    db.commit()
    db.refresh(it)
    _audit(db, tenant_id, user, "update", "expense_item", it.id,
           {"before": before, "after": {"name": it.name, "amount_cents": int(it.amount_cents or 0),
                                        "interval": it.interval, "is_active": it.is_active}})
    cats = _category_map(db, tenant_id)
    cat = cats.get(it.category_id)
    return ItemResponse(
        id=it.id, category_id=it.category_id, category_name=cat.name if cat else None,
        name=it.name, vendor=it.vendor, behavior=it.behavior,
        amount_cents=int(it.amount_cents or 0), interval=it.interval,
        start_date=it.start_date, end_date=it.end_date, is_active=it.is_active, notes=it.notes,
        monthly_cents=services.monthly_cents(it) if services.item_is_live(it, date.today()) else 0,
        posted_this_period=services.posted_in_month(db, tenant_id, it.id, date.today()) is not None,
        created_at=it.created_at,
    )


@router.post("/items/{item_id}/post", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
def post_item(
    item_id: UUID,
    payload: ItemPostRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    """Charge a standing commitment for a period — this is what turns "Railway,
    $20/mo" into money actually spent. Refuses a second post in the same calendar
    month unless `force`, so clicking twice can't double your server bill."""
    it = _own_item(db, tenant_id, item_id)
    on = payload.incurred_on or date.today()
    existing = services.posted_in_month(db, tenant_id, it.id, on)
    if existing and not payload.force:
        raise HTTPException(
            status_code=409,
            detail=f"{it.name} is already posted for {on.strftime('%B %Y')} "
                   f"({existing.incurred_on.isoformat()}). Use force to post it again.",
        )
    amount = payload.amount_cents if payload.amount_cents is not None else int(it.amount_cents or 0)
    entry = ExpenseEntry(
        tenant_id=tenant_id, category_id=it.category_id, item_id=it.id,
        description=it.name, vendor=it.vendor, amount_cents=amount,
        incurred_on=on, source="recurring", created_by=user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    _audit(db, tenant_id, user, "post", "expense_item", it.id,
           {"name": it.name, "entry_id": str(entry.id), "amount_cents": amount,
            "incurred_on": on.isoformat()})
    return _entry_out(entry, _category_map(db, tenant_id), {})


# ── ledger ───────────────────────────────────────────────────────────────────

@router.get("/entries", response_model=list[EntryResponse])
def list_entries(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    category_id: Optional[UUID] = Query(None),
    agent_id: Optional[UUID] = Query(None),
    source: Optional[str] = Query(None),
    include_voided: bool = Query(False),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    from_d, to_d = _window(from_, to)
    q = db.query(ExpenseEntry).filter(
        ExpenseEntry.tenant_id == tenant_id,
        ExpenseEntry.incurred_on >= from_d,
        ExpenseEntry.incurred_on <= to_d,
    )
    if not include_voided:
        q = q.filter(ExpenseEntry.voided_at.is_(None))
    if category_id:
        q = q.filter(ExpenseEntry.category_id == category_id)
    if agent_id:
        q = q.filter(ExpenseEntry.agent_id == agent_id)
    if source:
        q = q.filter(ExpenseEntry.source == source)
    rows = q.order_by(ExpenseEntry.incurred_on.desc(), ExpenseEntry.created_at.desc()).limit(limit).all()
    cats = _category_map(db, tenant_id)
    names = _agent_names(db, tenant_id)
    return [_entry_out(e, cats, names) for e in rows]


@router.post("/entries", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
def create_entry(
    payload: EntryCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    _own_category(db, tenant_id, payload.category_id)
    data = payload.model_dump()
    # An agent_id on a hand-entered line must belong to THIS tenant — the category
    # check above doesn't cover it.
    if data.get("agent_id") and not db.query(Agent).filter(
        Agent.tenant_id == tenant_id, Agent.id == data["agent_id"]
    ).first():
        raise HTTPException(status_code=404, detail="Agent not found")
    entry = ExpenseEntry(tenant_id=tenant_id, source="manual", created_by=user.id, **data)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    _audit(db, tenant_id, user, "create", "expense_entry", entry.id,
           {"description": entry.description, "amount_cents": int(entry.amount_cents or 0),
            "incurred_on": entry.incurred_on.isoformat()})
    return _entry_out(entry, _category_map(db, tenant_id), _agent_names(db, tenant_id))


@router.patch("/entries/{entry_id}", response_model=EntryResponse)
def update_entry(
    entry_id: UUID,
    payload: EntryUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    e = _own_entry(db, tenant_id, entry_id)
    if e.voided_at:
        raise HTTPException(status_code=409, detail="A voided entry cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    if "category_id" in changes and changes["category_id"]:
        _own_category(db, tenant_id, changes["category_id"])
    before = {"description": e.description, "amount_cents": int(e.amount_cents or 0),
              "incurred_on": e.incurred_on.isoformat()}
    for field, value in changes.items():
        setattr(e, field, value)
    db.commit()
    db.refresh(e)
    _audit(db, tenant_id, user, "update", "expense_entry", e.id,
           {"before": before, "after": {"description": e.description,
                                        "amount_cents": int(e.amount_cents or 0),
                                        "incurred_on": e.incurred_on.isoformat()}})
    return _entry_out(e, _category_map(db, tenant_id), _agent_names(db, tenant_id))


@router.delete("/entries/{entry_id}", response_model=EntryResponse)
def void_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    """VOID, not delete — the row stays so the audit trail has no holes, and it
    drops out of every total immediately."""
    from datetime import datetime, timezone as _tz
    e = _own_entry(db, tenant_id, entry_id)
    if not e.voided_at:
        e.voided_at = datetime.now(_tz.utc)
        e.voided_by = user.id
        db.commit()
        db.refresh(e)
        _audit(db, tenant_id, user, "void", "expense_entry", e.id,
               {"description": e.description, "amount_cents": int(e.amount_cents or 0)})
    return _entry_out(e, _category_map(db, tenant_id), _agent_names(db, tenant_id))


# ── agent pay: rates + hours ─────────────────────────────────────────────────

@router.get("/agents", response_model=list[AgentPayRow])
def agent_pay(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    """Every agent with their current rate and the hours/cost already posted in
    the window. Cost comes from the LEDGER, not from re-multiplying — so what you
    see here always equals what Overview totals."""
    from_d, to_d = _window(from_, to)
    names = _agent_names(db, tenant_id)
    rows: dict = {}
    for agent in db.query(Agent).filter(Agent.tenant_id == tenant_id).all():
        rate = services.current_rate(db, tenant_id, agent.id)
        rows[agent.id] = AgentPayRow(
            agent_id=agent.id,
            agent_name=names.get(agent.id, str(agent.id)),
            current_rate_cents=rate.rate_cents_per_hour if rate else None,
            rate_effective_from=rate.effective_from if rate else None,
        )
    posted = (
        db.query(ExpenseEntry)
        .filter(
            ExpenseEntry.tenant_id == tenant_id,
            ExpenseEntry.agent_id.isnot(None),
            ExpenseEntry.voided_at.is_(None),
            ExpenseEntry.incurred_on >= from_d,
            ExpenseEntry.incurred_on <= to_d,
        ).all()
    )
    for e in posted:
        row = rows.get(e.agent_id)
        if not row:
            continue
        row.hours += Decimal(e.quantity or 0)
        row.cost_cents += int(e.amount_cents or 0)
    return sorted(rows.values(), key=lambda r: (-r.cost_cents, r.agent_name))


@router.get("/agents/{agent_id}/rates", response_model=list[RateResponse])
def list_rates(
    agent_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    return (
        db.query(AgentRate)
        .filter(AgentRate.tenant_id == tenant_id, AgentRate.agent_id == agent_id)
        .order_by(AgentRate.effective_from.desc())
        .all()
    )


@router.put("/agents/{agent_id}/rate", response_model=RateResponse, status_code=status.HTTP_201_CREATED)
def set_rate(
    agent_id: UUID,
    payload: RateSet,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    """Set an agent's hourly rate from a date. Append-only: a raise is a NEW row,
    so hours already posted keep the rate that applied when they were worked. Only
    a rate set for the SAME effective_from is overwritten (a correction)."""
    if not db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.id == agent_id).first():
        raise HTTPException(status_code=404, detail="Agent not found")
    existing = (
        db.query(AgentRate)
        .filter(AgentRate.tenant_id == tenant_id, AgentRate.agent_id == agent_id,
                AgentRate.effective_from == payload.effective_from)
        .first()
    )
    if existing:
        before = existing.rate_cents_per_hour
        existing.rate_cents_per_hour = payload.rate_cents_per_hour
        existing.note = payload.note
        db.commit()
        db.refresh(existing)
        _audit(db, tenant_id, user, "update", "agent_rate", existing.id,
               {"agent_id": str(agent_id), "before_cents": before,
                "after_cents": existing.rate_cents_per_hour,
                "effective_from": payload.effective_from.isoformat()})
        return existing
    rate = AgentRate(
        tenant_id=tenant_id, agent_id=agent_id, created_by=user.id,
        rate_cents_per_hour=payload.rate_cents_per_hour,
        effective_from=payload.effective_from, note=payload.note,
    )
    db.add(rate)
    db.commit()
    db.refresh(rate)
    _audit(db, tenant_id, user, "create", "agent_rate", rate.id,
           {"agent_id": str(agent_id), "rate_cents": rate.rate_cents_per_hour,
            "effective_from": rate.effective_from.isoformat()})
    return rate


@router.post("/hours", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
def log_hours(
    payload: HoursCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_owner),
):
    """Log hours worked and price them into the ledger in one step.

    The rate used is the one in force on `work_date` (not today's), and it is
    SNAPSHOT onto the line — a later raise never restates a past month. When
    agents start clocking in, that flow posts this same shape with
    source='timesheet' and nothing downstream changes.
    """
    agent = db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.id == payload.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    rate_cents = payload.unit_rate_cents
    if rate_cents is None:
        rate = services.rate_on(db, tenant_id, agent.id, payload.work_date)
        if not rate:
            raise HTTPException(
                status_code=409,
                detail="This agent has no hourly rate on file for that date — set a rate first.",
            )
        rate_cents = rate.rate_cents_per_hour

    hours = Decimal(payload.hours)
    cat = services.agent_payroll_category(db, tenant_id)
    names = _agent_names(db, tenant_id)
    entry = ExpenseEntry(
        tenant_id=tenant_id, category_id=cat.id, agent_id=agent.id,
        description=f"{names.get(agent.id, 'Agent')} — agent hours",
        amount_cents=services.line_total_cents(hours, rate_cents),
        quantity=hours, unit="hour", unit_rate_cents=rate_cents,
        incurred_on=payload.work_date, source="manual",
        notes=payload.notes, created_by=user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    _audit(db, tenant_id, user, "create", "expense_entry", entry.id,
           {"kind": "agent_hours", "agent_id": str(agent.id), "hours": str(hours),
            "unit_rate_cents": rate_cents, "amount_cents": int(entry.amount_cents or 0),
            "work_date": payload.work_date.isoformat()})
    return _entry_out(entry, _category_map(db, tenant_id), names)


# ── summary ──────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=SummaryResponse)
def summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    """Totals, per-category split and a trend series for the window — plus the
    same window immediately before it, so the UI can show a real delta."""
    from_d, to_d = _window(from_, to)
    cats = _category_map(db, tenant_id)

    def _live(a: date, b: date):
        return (
            db.query(ExpenseEntry)
            .filter(
                ExpenseEntry.tenant_id == tenant_id,
                ExpenseEntry.voided_at.is_(None),
                ExpenseEntry.incurred_on >= a,
                ExpenseEntry.incurred_on <= b,
            ).all()
        )

    rows = _live(from_d, to_d)

    span = (to_d - from_d).days + 1
    prev_to = from_d - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span - 1)
    previous_total = sum(int(e.amount_cents or 0) for e in _live(prev_from, prev_to))

    by_cat: dict = {}
    buckets: dict = {}
    agent_hours = Decimal(0)
    agent_cost = 0
    for e in rows:
        amt = int(e.amount_cents or 0)
        by_cat[e.category_id] = by_cat.get(e.category_id, 0) + amt
        key, label = services.bucket(e.incurred_on, granularity)
        b = buckets.setdefault(key, {"label": label, "date": key, "amount_cents": 0})
        b["amount_cents"] += amt
        if e.agent_id:
            agent_hours += Decimal(e.quantity or 0)
            agent_cost += amt

    monthly_commitment = sum(
        services.monthly_cents(it)
        for it in db.query(ExpenseItem).filter(
            ExpenseItem.tenant_id == tenant_id, ExpenseItem.is_active.is_(True)
        ).all()
        if services.item_is_live(it, to_d)
    )

    return SummaryResponse(
        range={"from": from_d.isoformat(), "to": to_d.isoformat()},
        granularity=granularity,
        total_cents=sum(by_cat.values()),
        entry_count=len(rows),
        by_category=[
            {
                "category_id": cid,
                "name": cats[cid].name if cid in cats else "—",
                "color": cats[cid].color if cid in cats else None,
                "amount_cents": amt,
            }
            for cid, amt in sorted(by_cat.items(), key=lambda kv: -kv[1])
        ],
        trend=[buckets[k] for k in sorted(buckets)],
        monthly_commitment_cents=monthly_commitment,
        agent_hours=agent_hours,
        agent_cost_cents=agent_cost,
        previous_total_cents=previous_total,
    )


# ── audit trail ──────────────────────────────────────────────────────────────

@router.get("/audit")
def expense_audit(
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_owner),
):
    """Every expense change, newest first — reads the app's existing audit_logs
    table, filtered to the expense resource types."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant_id, AuditLog.resource_type.in_(AUDIT_RESOURCES))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    who = {
        u.id: (f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email)
        for u in db.query(User).filter(User.tenant_id == tenant_id).all()
    }
    return {
        "items": [{
            "id": str(r.id),
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": str(r.resource_id) if r.resource_id else None,
            "user_name": who.get(r.user_id, "—"),
            "details": r.details or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    }
