"""Request/response models for the owner-only expense tracker.

Every money value crossing this boundary is integer CENTS (`*_cents`). The
frontend formats; the API never sees a float.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


Behavior = Literal["fixed_recurring", "usage_based", "one_off"]
Interval = Literal["monthly", "weekly", "yearly", "one_off"]
Source = Literal["manual", "recurring", "timesheet", "derived"]


# ── Categories ───────────────────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: Optional[str] = Field(default=None, max_length=60)
    default_behavior: Behavior = "one_off"
    color: Optional[str] = Field(default=None, max_length=9)
    sort_order: int = 100


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    default_behavior: Optional[Behavior] = None
    color: Optional[str] = Field(default=None, max_length=9)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    default_behavior: str
    color: Optional[str] = None
    sort_order: int
    is_active: bool


# ── Standing commitments (recurring items) ───────────────────────────────────

class ItemCreate(BaseModel):
    category_id: UUID
    name: str = Field(min_length=1, max_length=140)
    vendor: Optional[str] = Field(default=None, max_length=120)
    behavior: Behavior = "fixed_recurring"
    amount_cents: int = Field(default=0, ge=0)
    interval: Interval = "monthly"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None


class ItemUpdate(BaseModel):
    category_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=140)
    vendor: Optional[str] = Field(default=None, max_length=120)
    behavior: Optional[Behavior] = None
    amount_cents: Optional[int] = Field(default=None, ge=0)
    interval: Optional[Interval] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    category_name: Optional[str] = None
    name: str
    vendor: Optional[str] = None
    behavior: str
    amount_cents: int
    interval: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool
    notes: Optional[str] = None
    # Convenience for the UI: has this item already been posted this month, and
    # what does it contribute to the monthly commitment total?
    monthly_cents: int = 0
    posted_this_period: bool = False
    created_at: Optional[datetime] = None


class ItemPostRequest(BaseModel):
    """Materialise this commitment as a real ledger entry for a period."""
    incurred_on: Optional[date] = None            # defaults to today
    amount_cents: Optional[int] = Field(default=None, ge=0)   # defaults to the item's amount
    # Post again even though this item already has an entry in that calendar month.
    force: bool = False


# ── Ledger entries ───────────────────────────────────────────────────────────

class EntryCreate(BaseModel):
    category_id: UUID
    description: str = Field(min_length=1, max_length=255)
    amount_cents: int = Field(ge=0)
    incurred_on: date
    vendor: Optional[str] = Field(default=None, max_length=120)
    item_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = Field(default=None, max_length=16)
    unit_rate_cents: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None


class EntryUpdate(BaseModel):
    category_id: Optional[UUID] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=255)
    amount_cents: Optional[int] = Field(default=None, ge=0)
    incurred_on: Optional[date] = None
    vendor: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = None


class EntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    category_name: Optional[str] = None
    category_color: Optional[str] = None
    item_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    agent_name: Optional[str] = None
    description: str
    vendor: Optional[str] = None
    amount_cents: int
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_rate_cents: Optional[int] = None
    incurred_on: date
    source: str
    notes: Optional[str] = None
    voided_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ── Agent pay (rate history + hours) ─────────────────────────────────────────

class RateSet(BaseModel):
    rate_cents_per_hour: int = Field(ge=0)
    effective_from: date
    note: Optional[str] = Field(default=None, max_length=255)


class RateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    rate_cents_per_hour: int
    effective_from: date
    note: Optional[str] = None


class HoursCreate(BaseModel):
    """Log hours worked. Posts a ledger entry priced with the rate in force on
    `work_date`, snapshotted onto the line. `source` is 'manual' today; agent
    clock-in will post the same shape with source='timesheet'."""

    agent_id: UUID
    work_date: date
    hours: Decimal = Field(gt=0, le=24)
    # Override the agent's on-file rate for this one line (rare — a bonus shift).
    unit_rate_cents: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None


class AgentPayRow(BaseModel):
    agent_id: UUID
    agent_name: str
    current_rate_cents: Optional[int] = None
    rate_effective_from: Optional[date] = None
    hours: Decimal = Decimal(0)
    cost_cents: int = 0


# ── Summary ──────────────────────────────────────────────────────────────────

class CategoryTotal(BaseModel):
    category_id: UUID
    name: str
    color: Optional[str] = None
    amount_cents: int


class TrendPoint(BaseModel):
    label: str
    date: str
    amount_cents: int


class SummaryResponse(BaseModel):
    range: dict
    granularity: str
    total_cents: int
    entry_count: int
    by_category: List[CategoryTotal]
    trend: List[TrendPoint]
    # What the standing commitments add up to per month, regardless of the window.
    monthly_commitment_cents: int
    agent_hours: Decimal
    agent_cost_cents: int
    # Same window, immediately preceding — lets the UI show a period-over-period delta.
    previous_total_cents: int
