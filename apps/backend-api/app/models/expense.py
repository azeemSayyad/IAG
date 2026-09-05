"""Company expense tracking (owner/CEO only).

Four tables, deliberately split so the numbers stay auditable:

  ExpenseCategory  Seeded per tenant on first read. A LOOKUP, not an enum, so a
                   new spend type never needs a migration.
  ExpenseItem      The COMMITMENT — "Railway, $20/mo, active since Jan". It does
                   not itself cost anything; it generates entries.
  ExpenseEntry     The TRANSACTION — one dated charge. This is the ledger, and the
                   ONLY thing that is ever summed. Agent hour lines live here too
                   (source='manual' today, 'timesheet' once agents clock in), with
                   the hourly rate SNAPSHOT so history can't move under you.
  AgentRate        Hourly rate history per agent, keyed by effective_from. Rates
                   are never overwritten — a raise must not restate last month.

Money is stored as integer CENTS everywhere. Never floats.
Entries are never hard-deleted: voided_at keeps the audit trail whole.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# How a cost behaves, independent of what category it is in.
BEHAVIOR_FIXED = "fixed_recurring"   # same amount every period (servers, salaries)
BEHAVIOR_USAGE = "usage_based"       # quantity x rate (agent hours, per-lead)
BEHAVIOR_ONE_OFF = "one_off"         # a single charge
BEHAVIORS = (BEHAVIOR_FIXED, BEHAVIOR_USAGE, BEHAVIOR_ONE_OFF)

# Where an entry came from. 'manual' and 'timesheet' both mean agent hours — the
# second one is reserved for when agents clock in themselves; nothing else changes.
SOURCES = ("manual", "recurring", "timesheet", "derived")

INTERVALS = ("monthly", "weekly", "yearly", "one_off")


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    slug = Column(String(60), nullable=False)
    name = Column(String(80), nullable=False)
    # The behaviour NEW items in this category default to (still overridable per item).
    default_behavior = Column(String(20), nullable=False, default=BEHAVIOR_ONE_OFF)
    color = Column(String(9), nullable=True)          # chart colour, e.g. "#3B82F6"
    sort_order = Column(Integer, nullable=False, default=100)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_expense_categories_tenant_slug"),
    )


class ExpenseItem(Base):
    """A standing commitment. Posting it creates an ExpenseEntry for that period."""

    __tablename__ = "expense_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("expense_categories.id"), nullable=False, index=True)
    name = Column(String(140), nullable=False)
    vendor = Column(String(120), nullable=True)
    behavior = Column(String(20), nullable=False, default=BEHAVIOR_FIXED)
    # Expected amount per interval. The posted entry can differ (the month Railway
    # bills $34 instead of $20) — that is exactly why entries are separate.
    amount_cents = Column(BigInteger, nullable=False, default=0)
    interval = Column(String(12), nullable=False, default="monthly")
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    category = relationship("ExpenseCategory")

    __table_args__ = (
        Index("idx_expense_items_tenant_active", "tenant_id", "is_active"),
    )


class ExpenseEntry(Base):
    """One dated charge. The ledger — the only table that is ever summed."""

    __tablename__ = "expense_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("expense_categories.id"), nullable=False, index=True)
    # Set when this entry was posted from a standing commitment.
    item_id = Column(UUID(as_uuid=True), ForeignKey("expense_items.id"), nullable=True, index=True)
    # Set on agent-hour lines so payroll can be pivoted per agent.
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)

    description = Column(String(255), nullable=False)
    vendor = Column(String(120), nullable=True)
    amount_cents = Column(BigInteger, nullable=False, default=0)

    # Usage-based detail (agent hours): amount_cents = quantity * unit_rate_cents,
    # with the rate SNAPSHOT here so a later raise cannot restate this line.
    quantity = Column(Numeric(10, 2), nullable=True)
    unit = Column(String(16), nullable=True)            # "hour"
    unit_rate_cents = Column(Integer, nullable=True)

    incurred_on = Column(Date, nullable=False, index=True)
    source = Column(String(16), nullable=False, default="manual")
    notes = Column(Text, nullable=True)

    # Soft delete — an expense is never removed, only voided, so the trail is whole.
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    category = relationship("ExpenseCategory")
    item = relationship("ExpenseItem")

    __table_args__ = (
        Index("idx_expense_entries_tenant_date", "tenant_id", "incurred_on"),
        Index("idx_expense_entries_tenant_agent", "tenant_id", "agent_id"),
    )


class AgentRate(Base):
    """Hourly pay rate for an agent, effective from a date. Append-only: a new rate
    is a NEW row, so posted hours keep the rate that applied on the day worked."""

    __tablename__ = "agent_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    rate_cents_per_hour = Column(Integer, nullable=False, default=0)
    effective_from = Column(Date, nullable=False)
    note = Column(String(255), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = relationship("Agent")

    __table_args__ = (
        UniqueConstraint("agent_id", "effective_from", name="uq_agent_rates_agent_from"),
        Index("idx_agent_rates_tenant_agent", "tenant_id", "agent_id"),
    )
