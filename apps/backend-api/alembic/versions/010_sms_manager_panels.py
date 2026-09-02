"""SMS manager panels — pass_count + sms_settings

Revision ID: 010
Revises: 009
Create Date: 2026-06-14

Additive only: adds sms_leads.pass_count (for the Rejected pool) and a new
sms_settings table (polling toggle). Nothing existing is altered.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sms_leads",
        sa.Column("pass_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "sms_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("polling_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("sms_settings")
    op.drop_column("sms_leads", "pass_count")
