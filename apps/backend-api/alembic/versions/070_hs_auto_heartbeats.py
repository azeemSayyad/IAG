"""HealthSherpa auto mode heartbeat

Revision ID: 070
Revises: 069
Create Date: 2026-11-14

* hs_auto_heartbeats: the HealthSherpa agent's auto-mode tab as last seen, so
  the campaign page can show admins whether auto mode is really running.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "hs_auto_heartbeats" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "hs_auto_heartbeats",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("auto_on", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ext_version", sa.String(20), nullable=True),
        sa.Column("tab_hidden", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.String(16), nullable=True),
    )
    op.create_index("ix_hs_auto_heartbeats_tenant_id", "hs_auto_heartbeats", ["tenant_id"])


def downgrade() -> None:
    if "hs_auto_heartbeats" in inspect(op.get_bind()).get_table_names():
        op.drop_table("hs_auto_heartbeats")
