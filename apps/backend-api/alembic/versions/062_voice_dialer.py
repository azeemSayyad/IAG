"""voice dialer: YES-reply auto dialer with screen pop

Revision ID: 062
Revises: 061
Create Date: 2026-10-19

* sms_leads: yes_reply / yes_at / dial_from / next_dial_at / dial_attempts —
  a lead that replied YES waits as status READY_TO_CALL (out of the pool) and
  is locked as DIALING while a call is in flight.
* sms_queue_agents: voice_leg_uuid / voice_line_at — the agent's open headset line;
  voice_ready — the agent's "Ready for calls" switch (off = pool only, no line).
* voice_call_attempts: one row per call (3-per-24h cap, abandon rate, audit).
* voice_dialer_settings: per-tenant switch + dialing rules (off by default).
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    cols = _cols("sms_leads")
    if "yes_reply" not in cols:
        op.add_column("sms_leads", sa.Column("yes_reply", sa.Text(), nullable=True))
    if "yes_at" not in cols:
        op.add_column("sms_leads", sa.Column("yes_at", sa.DateTime(timezone=True), nullable=True))
    if "dial_from" not in cols:
        op.add_column("sms_leads", sa.Column("dial_from", sa.String(32), nullable=True))
    if "next_dial_at" not in cols:
        op.add_column("sms_leads", sa.Column("next_dial_at", sa.DateTime(timezone=True), nullable=True))
    if "dial_attempts" not in cols:
        op.add_column("sms_leads", sa.Column("dial_attempts", sa.Integer(), nullable=False, server_default="0"))

    cols = _cols("sms_queue_agents")
    if "voice_leg_uuid" not in cols:
        op.add_column("sms_queue_agents", sa.Column("voice_leg_uuid", sa.String(64), nullable=True))
    if "voice_line_at" not in cols:
        op.add_column("sms_queue_agents", sa.Column("voice_line_at", sa.DateTime(timezone=True), nullable=True))
    if "voice_ready" not in cols:
        op.add_column("sms_queue_agents", sa.Column("voice_ready", sa.Boolean(), nullable=False, server_default=sa.false()))

    tables = _tables()
    if "voice_call_attempts" not in tables:
        op.create_table(
            "voice_call_attempts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("sms_lead_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("sms_leads.id", ondelete="SET NULL"), nullable=True),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("from_number", sa.String(32), nullable=True),
            sa.Column("direction", sa.String(10), nullable=False, server_default="outbound"),
            sa.Column("call_uuid", sa.String(64), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="DIALING"),
            sa.Column("agent_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("disposition", sa.String(40), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("human_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_seconds", sa.Integer(), nullable=True),
        )
        op.create_index("idx_voice_attempts_tenant_created", "voice_call_attempts", ["tenant_id", "created_at"])
        op.create_index("idx_voice_attempts_phone", "voice_call_attempts", ["tenant_id", "phone_number", "created_at"])
        op.create_index("idx_voice_attempts_call_uuid", "voice_call_attempts", ["call_uuid"])
        op.create_index("idx_voice_attempts_lead", "voice_call_attempts", ["sms_lead_id"])

    elif "disposition" not in _cols("voice_call_attempts"):
        op.add_column("voice_call_attempts", sa.Column("disposition", sa.String(40), nullable=True))

    if "voice_dialer_settings" not in tables:
        op.create_table(
            "voice_dialer_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"),
                      nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("pacing_ratio", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("ring_seconds", sa.Integer(), nullable=False, server_default="25"),
            sa.Column("max_attempts_24h", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("max_total_attempts", sa.Integer(), nullable=False, server_default="6"),
            sa.Column("retry_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("reply_delay_seconds", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("window_start_hour", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("window_end_hour", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("business_name", sa.String(120), nullable=True),
            sa.Column("callback_number", sa.String(32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    elif "reply_delay_seconds" not in _cols("voice_dialer_settings"):
        op.add_column("voice_dialer_settings",
                      sa.Column("reply_delay_seconds", sa.Integer(), nullable=False, server_default="3"))


def downgrade() -> None:
    tables = _tables()
    if "voice_dialer_settings" in tables:
        op.drop_table("voice_dialer_settings")
    if "voice_call_attempts" in tables:
        op.drop_table("voice_call_attempts")
    for col in ("voice_ready", "voice_line_at", "voice_leg_uuid"):
        if col in _cols("sms_queue_agents"):
            op.drop_column("sms_queue_agents", col)
    for col in ("dial_attempts", "next_dial_at", "dial_from", "yes_at", "yes_reply"):
        if col in _cols("sms_leads"):
            op.drop_column("sms_leads", col)
