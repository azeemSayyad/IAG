"""SMS agent breaks — reason + start/end for duration tracking

Revision ID: 012
Revises: 011
Create Date: 2026-06-14

Additive only: new sms_agent_breaks table. Nothing existing is altered.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sms_agent_breaks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_sms_agent_breaks_tenant_user", "sms_agent_breaks", ["tenant_id", "user_id", "started_at"])
    op.create_index("idx_sms_agent_breaks_open", "sms_agent_breaks", ["user_id", "ended_at"])


def downgrade() -> None:
    op.drop_table("sms_agent_breaks")
