"""SMS agent actions — per-agent passed vs kept tally

Revision ID: 014
Revises: 013
Create Date: 2026-06-15

Additive only: new sms_agent_actions table. Nothing existing is altered.
Renumbered from 013 -> 014 to sit after 013_user_avatar (both originally
claimed revision 013, which collided). Idempotent: skips if the table exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("sms_agent_actions"):
        return
    op.create_table(
        "sms_agent_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sms_lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sms_leads.id"), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_sms_agent_actions_tenant_user", "sms_agent_actions", ["tenant_id", "user_id", "created_at"])
    op.create_index("idx_sms_agent_actions_tenant_action", "sms_agent_actions", ["tenant_id", "action", "created_at"])


def downgrade() -> None:
    if _has_table("sms_agent_actions"):
        op.drop_table("sms_agent_actions")
