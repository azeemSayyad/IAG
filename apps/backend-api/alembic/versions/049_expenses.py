"""Company expense tracking (owner-only)

Revision ID: 049
Revises: 048
Create Date: 2026-09-04

Creates the four expense tables:
  expense_categories  seeded per tenant at runtime (a lookup, not an enum)
  expense_items       standing commitments (Railway $20/mo, a salary)
  expense_entries     the ledger — every dated charge, including agent hour lines
  agent_rates         append-only hourly rate history per agent

Money is integer cents. Entries are soft-voided, never deleted. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
# Explicit import: `sa.dialects.postgresql` is NOT reachable through the top-level
# `sqlalchemy` namespace unless the dialect submodule has been imported, so the
# attribute form raises AttributeError and takes the whole migration down with it.
from sqlalchemy.dialects import postgresql


revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("expense_categories"):
        op.create_table(
            "expense_categories",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("slug", sa.String(60), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("default_behavior", sa.String(20), nullable=False, server_default="one_off"),
            sa.Column("color", sa.String(9), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_expense_categories_tenant_slug"),
        )
        op.create_index("ix_expense_categories_tenant_id", "expense_categories", ["tenant_id"])

    if not _has_table("expense_items"):
        op.create_table(
            "expense_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expense_categories.id"), nullable=False),
            sa.Column("name", sa.String(140), nullable=False),
            sa.Column("vendor", sa.String(120), nullable=True),
            sa.Column("behavior", sa.String(20), nullable=False, server_default="fixed_recurring"),
            sa.Column("amount_cents", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("interval", sa.String(12), nullable=False, server_default="monthly"),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_expense_items_tenant_id", "expense_items", ["tenant_id"])
        op.create_index("ix_expense_items_category_id", "expense_items", ["category_id"])
        op.create_index("idx_expense_items_tenant_active", "expense_items", ["tenant_id", "is_active"])

    if not _has_table("expense_entries"):
        op.create_table(
            "expense_entries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expense_categories.id"), nullable=False),
            sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expense_items.id"), nullable=True),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
            sa.Column("description", sa.String(255), nullable=False),
            sa.Column("vendor", sa.String(120), nullable=True),
            sa.Column("amount_cents", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("quantity", sa.Numeric(10, 2), nullable=True),
            sa.Column("unit", sa.String(16), nullable=True),
            sa.Column("unit_rate_cents", sa.Integer(), nullable=True),
            sa.Column("incurred_on", sa.Date(), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("voided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_expense_entries_tenant_id", "expense_entries", ["tenant_id"])
        op.create_index("ix_expense_entries_category_id", "expense_entries", ["category_id"])
        op.create_index("ix_expense_entries_item_id", "expense_entries", ["item_id"])
        op.create_index("ix_expense_entries_agent_id", "expense_entries", ["agent_id"])
        op.create_index("ix_expense_entries_incurred_on", "expense_entries", ["incurred_on"])
        op.create_index("idx_expense_entries_tenant_date", "expense_entries", ["tenant_id", "incurred_on"])
        op.create_index("idx_expense_entries_tenant_agent", "expense_entries", ["tenant_id", "agent_id"])

    if not _has_table("agent_rates"):
        op.create_table(
            "agent_rates",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("rate_cents_per_hour", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("note", sa.String(255), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("agent_id", "effective_from", name="uq_agent_rates_agent_from"),
        )
        op.create_index("ix_agent_rates_tenant_id", "agent_rates", ["tenant_id"])
        op.create_index("ix_agent_rates_agent_id", "agent_rates", ["agent_id"])
        op.create_index("idx_agent_rates_tenant_agent", "agent_rates", ["tenant_id", "agent_id"])


def downgrade() -> None:
    for t in ("agent_rates", "expense_entries", "expense_items", "expense_categories"):
        if _has_table(t):
            op.drop_table(t)
