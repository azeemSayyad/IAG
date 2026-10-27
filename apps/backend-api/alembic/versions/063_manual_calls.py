"""manual calls: anyone dials any number from the portal (Vonage)

Revision ID: 063
Revises: 062
Create Date: 2026-10-22

* manual_calls: one row per call placed from the Dial drawer — its own log,
  deliberately apart from voice_call_attempts (the auto dialer's rules, stats
  and Called list never see these). recording_id -> deal_recordings.id.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "manual_calls" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "manual_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=False),
        sa.Column("from_number", sa.String(32), nullable=True),
        sa.Column("app_leg_uuid", sa.String(64), nullable=True),
        sa.Column("callee_leg_uuid", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DIALING"),
        sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    op.create_index("idx_manual_calls_tenant_created", "manual_calls", ["tenant_id", "created_at"])
    op.create_index("idx_manual_calls_user_created", "manual_calls", ["user_id", "created_at"])
    op.create_index("idx_manual_calls_app_leg", "manual_calls", ["app_leg_uuid"])


def downgrade() -> None:
    if "manual_calls" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("manual_calls")
