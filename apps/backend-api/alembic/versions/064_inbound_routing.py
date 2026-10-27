"""inbound routing: client owners, hold queue, open hours

Revision ID: 064
Revises: 063
Create Date: 2026-10-23

* voice_client_owners: the agent a client's callbacks go to (by phone) — the
  agent who most recently called / talked to them, or an admin's pick.
* voice_call_attempts.hold_for_user_id: a caller on hold waiting for their own
  agent (NULL = waiting for anyone). Status HOLDING while they wait.
* voice_dialer_settings: max_hold_seconds and the open hours (closed message).
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "voice_client_owners" not in set(inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "voice_client_owners",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("source", sa.String(20), nullable=False, server_default="call"),
            sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "phone_number", name="uq_voice_client_owner_phone"),
        )
        op.create_index("idx_voice_client_owners_user", "voice_client_owners", ["tenant_id", "user_id"])

    if "hold_for_user_id" not in _cols("voice_call_attempts"):
        op.add_column("voice_call_attempts",
                      sa.Column("hold_for_user_id", postgresql.UUID(as_uuid=True), nullable=True))

    cols = _cols("voice_dialer_settings")
    add = {
        "max_hold_seconds": sa.Column("max_hold_seconds", sa.Integer(), nullable=False, server_default="300"),
        "hours_enabled": sa.Column("hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        "open_days": sa.Column("open_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        "open_start_minute": sa.Column("open_start_minute", sa.Integer(), nullable=False, server_default="540"),
        "open_end_minute": sa.Column("open_end_minute", sa.Integer(), nullable=False, server_default="1080"),
        "hours_tz": sa.Column("hours_tz", sa.String(40), nullable=False, server_default="America/New_York"),
    }
    for name, col in add.items():
        if name not in cols:
            op.add_column("voice_dialer_settings", col)


def downgrade() -> None:
    cols = _cols("voice_dialer_settings")
    for name in ("hours_tz", "open_end_minute", "open_start_minute", "open_days", "hours_enabled", "max_hold_seconds"):
        if name in cols:
            op.drop_column("voice_dialer_settings", name)
    if "hold_for_user_id" in _cols("voice_call_attempts"):
        op.drop_column("voice_call_attempts", "hold_for_user_id")
    if "voice_client_owners" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("voice_client_owners")
