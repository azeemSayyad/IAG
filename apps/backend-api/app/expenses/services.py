"""Expense-tracker helpers: category seeding, rate lookup, period math.

Kept out of the router so the money rules (which rate applies on which day, what
a weekly item costs per month) live in one place and can be read at a glance.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.models.expense import AgentRate, ExpenseCategory, ExpenseEntry, ExpenseItem


# Seeded on first read so a new tenant opens the page to something usable. These
# are STARTING POINTS — categories are rows, so the owner can rename or add more.
# Colours are a VALIDATED categorical palette, not a taste call: checked with the
# dataviz validator against both the light and the dark chart surface (lightness
# band, chroma floor, colour-vision separation, contrast). sort_order is also the
# palette order, so changing it changes which hues sit next to each other — re-run
# the validator if you reorder or add a colour here.
DEFAULT_CATEGORIES = [
    # (slug, name, default_behavior, color, sort)
    ("infrastructure", "Infrastructure & SaaS", "fixed_recurring", "#2563EB", 10),
    ("staff_payroll", "Staff Payroll", "fixed_recurring", "#EA580C", 20),
    ("agent_payroll", "Agent Payroll", "usage_based", "#059669", 30),
    ("lead_purchase", "Lead Purchases", "one_off", "#C026D3", 40),
    ("telephony", "Telephony & SMS", "one_off", "#0891B2", 50),
    ("licensing", "Licensing & Compliance", "one_off", "#E11D48", 60),
    ("other", "Other", "one_off", "#65A30D", 99),
]

AGENT_PAYROLL_SLUG = "agent_payroll"


def ensure_categories(db: Session, tenant_id: str) -> list[ExpenseCategory]:
    """Return this tenant's categories, seeding the defaults the first time."""
    rows = (
        db.query(ExpenseCategory)
        .filter(ExpenseCategory.tenant_id == tenant_id)
        .order_by(ExpenseCategory.sort_order, ExpenseCategory.name)
        .all()
    )
    if rows:
        return rows
    for slug, name, behavior, color, sort in DEFAULT_CATEGORIES:
        db.add(ExpenseCategory(
            tenant_id=tenant_id, slug=slug, name=name,
            default_behavior=behavior, color=color, sort_order=sort,
        ))
    db.commit()
    return (
        db.query(ExpenseCategory)
        .filter(ExpenseCategory.tenant_id == tenant_id)
        .order_by(ExpenseCategory.sort_order, ExpenseCategory.name)
        .all()
    )


def agent_payroll_category(db: Session, tenant_id: str) -> ExpenseCategory:
    """The category agent hour lines are posted into (seeded if missing)."""
    ensure_categories(db, tenant_id)
    cat = (
        db.query(ExpenseCategory)
        .filter(ExpenseCategory.tenant_id == tenant_id, ExpenseCategory.slug == AGENT_PAYROLL_SLUG)
        .first()
    )
    if not cat:
        cat = ExpenseCategory(
            tenant_id=tenant_id, slug=AGENT_PAYROLL_SLUG, name="Agent Payroll",
            default_behavior="usage_based", color="#059669", sort_order=30,
        )
        db.add(cat)
        db.commit()
        db.refresh(cat)
    return cat


def rate_on(db: Session, tenant_id: str, agent_id, on: date) -> Optional[AgentRate]:
    """The rate in force for `agent_id` on `on` — the latest rate whose
    effective_from is on or before that day. Returns None if never set."""
    return (
        db.query(AgentRate)
        .filter(
            AgentRate.tenant_id == tenant_id,
            AgentRate.agent_id == agent_id,
            AgentRate.effective_from <= on,
        )
        .order_by(AgentRate.effective_from.desc())
        .first()
    )


def current_rate(db: Session, tenant_id: str, agent_id) -> Optional[AgentRate]:
    return rate_on(db, tenant_id, agent_id, date.today())


def line_total_cents(hours: Decimal, rate_cents: int) -> int:
    """hours x rate, rounded to the cent (half-up). Decimal throughout — the whole
    point of storing cents is that this never touches a float."""
    total = (Decimal(hours) * Decimal(rate_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(total)


def monthly_cents(item: ExpenseItem) -> int:
    """What a standing commitment costs per MONTH, so weekly/yearly items can be
    compared with monthly ones in the commitment total. 52/12 weeks per month."""
    amt = int(item.amount_cents or 0)
    if item.interval == "monthly":
        return amt
    if item.interval == "weekly":
        return int(Decimal(amt) * Decimal(52) / Decimal(12))
    if item.interval == "yearly":
        return int(Decimal(amt) / Decimal(12))
    return 0  # one_off commitments carry no monthly run-rate


def item_is_live(item: ExpenseItem, on: date) -> bool:
    """Active and inside its start/end window on the given day."""
    if not item.is_active:
        return False
    if item.start_date and item.start_date > on:
        return False
    if item.end_date and item.end_date < on:
        return False
    return True


def posted_in_month(db: Session, tenant_id: str, item_id, on: date) -> Optional[ExpenseEntry]:
    """The live entry already posted for this item in `on`'s calendar month, if any.
    Guards the recurring 'Post' action against double-charging a month."""
    first = on.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return (
        db.query(ExpenseEntry)
        .filter(
            ExpenseEntry.tenant_id == tenant_id,
            ExpenseEntry.item_id == item_id,
            ExpenseEntry.voided_at.is_(None),
            ExpenseEntry.incurred_on >= first,
            ExpenseEntry.incurred_on < nxt,
        )
        .first()
    )


def bucket(d: date, granularity: str) -> tuple[str, str]:
    """Map a date into a trend bucket -> (iso_key, human_label)."""
    if granularity == "month":
        key = d.replace(day=1)
        return key.isoformat(), key.strftime("%b %Y")
    if granularity == "week":
        key = d - timedelta(days=d.weekday())        # Monday
        return key.isoformat(), key.strftime("%b %-d")
    return d.isoformat(), d.strftime("%b %-d")
