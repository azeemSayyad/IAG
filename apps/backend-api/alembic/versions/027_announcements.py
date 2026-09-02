"""Admin announcements + per-user acks

Revision ID: 027
Revises: 026
Create Date: 2026-06-19

Blocking announcements an admin pushes to all agents or one agent; each targeted
agent must acknowledge before the UI unblocks (acks tracked per user). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("announcements"):
        op.create_table(
            "announcements",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("target_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("idx_announcements_tenant_active", "announcements", ["tenant_id", "active"])
    if not _has_table("announcement_acks"):
        op.create_table(
            "announcement_acks",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("announcement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("acked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("uq_ann_ack", "announcement_acks", ["announcement_id", "user_id"], unique=True)


def downgrade() -> None:
    if _has_table("announcement_acks"):
        op.drop_table("announcement_acks")
    if _has_table("announcements"):
        op.drop_table("announcements")
