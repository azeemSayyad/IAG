"""company pay rules: weekly ACA tiers, ancillary product, tier exceptions

Revision ID: 060
Revises: 059
Create Date: 2026-10-04

  * deals.application_id   — groups the person-deals logged in ONE Log Sale
                             submission (NULL on older rows: each is its own
                             application). Commissions are counted per application.
  * deals.ancillary_count  — 0/1 flag like aca/dental/vision.
  * pay_rules              — append-only versions of the tenant's pay rules
                             (tiers, dental / ancillary / vision amounts, which
                             carriers count per member). The earliest row's
                             starts_on is the Monday the rules take over from
                             the per-agent sale rates.
  * pay_exceptions         — one agent's ACA tier locked by an admin.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("deals")}
    if "application_id" not in cols:
        op.add_column("deals", sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index("ix_deals_application_id", "deals", ["application_id"])
    if "ancillary_count" not in cols:
        op.add_column("deals", sa.Column("ancillary_count", sa.Integer(), nullable=False, server_default="0"))
    if not insp.has_table("pay_rules"):
        op.create_table(
            "pay_rules",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("rules", postgresql.JSONB(), nullable=False),
            sa.Column("starts_on", sa.Date(), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_pay_rules_tenant_created", "pay_rules", ["tenant_id", "created_at"])
    if not insp.has_table("pay_exceptions"):
        op.create_table(
            "pay_exceptions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("tier", sa.Integer(), nullable=False),
            sa.Column("ends_on", sa.Date(), nullable=True),
            sa.Column("reason", sa.String(500), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index("idx_pay_exceptions_tenant_agent", "pay_exceptions", ["tenant_id", "agent_id", "created_at"])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("pay_exceptions"):
        op.drop_table("pay_exceptions")
    if insp.has_table("pay_rules"):
        op.drop_table("pay_rules")
    cols = {c["name"] for c in insp.get_columns("deals")}
    if "ancillary_count" in cols:
        op.drop_column("deals", "ancillary_count")
    if "application_id" in cols:
        op.drop_index("ix_deals_application_id", table_name="deals")
        op.drop_column("deals", "application_id")
