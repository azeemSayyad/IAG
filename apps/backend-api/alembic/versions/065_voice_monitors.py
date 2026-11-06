"""call monitoring: a manager listening in / whispering on a live dialer call

Revision ID: 065
Revises: 064
Create Date: 2026-11-06

* voice_monitors: one row per listen-in session (who, which call, listen or
  whisper, when). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "voice_monitors" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "voice_monitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("voice_call_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False, server_default="listen"),
        sa.Column("leg_uuid", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="RINGING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_voice_monitors_attempt", "voice_monitors", ["attempt_id"])
    op.create_index("idx_voice_monitors_user", "voice_monitors", ["user_id", "created_at"])


def downgrade() -> None:
    if "voice_monitors" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("voice_monitors")
