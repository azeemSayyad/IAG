"""per-sale agent pay rates (ACA / Dental / Vision)

Revision ID: 056
Revises: 055
Create Date: 2026-09-25

Adds agent_sale_rates: append-only per-agent, per-product pay for each APPROVED
sale, in force from effective_at. Earnings are derived from deals at read time,
each deal priced by the rate in force when it was logged, so a rate change never
restates past income. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("agent_sale_rates"):
        return
    op.create_table(
        "agent_sale_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("aca_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dental_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vision_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_sale_rates_tenant_id", "agent_sale_rates", ["tenant_id"])
    op.create_index("ix_agent_sale_rates_agent_id", "agent_sale_rates", ["agent_id"])
    op.create_index("idx_agent_sale_rates_tenant_agent", "agent_sale_rates",
                    ["tenant_id", "agent_id", "effective_at"])


def downgrade() -> None:
    if _has_table("agent_sale_rates"):
        op.drop_table("agent_sale_rates")
