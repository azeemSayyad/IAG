"""SMS subsystem — queue, agents, messages, poll log

Revision ID: 009
Revises: 008
Create Date: 2026-06-13

Creates the core SMS tables that power the SMS Queue / Manager / Monitoring
features. All tables are new and tenant-scoped; nothing existing is altered.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "sms_leads",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True),
        sa.Column("phone_number", sa.String(length=50), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="NORMAL"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="QUEUED"),
        sa.Column("assigned_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("disposition", sa.String(length=40), nullable=True),
        sa.Column("callback_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispositioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_sms_leads_tenant_status", "sms_leads", ["tenant_id", "status"])
    op.create_index("idx_sms_leads_tenant_agent", "sms_leads", ["tenant_id", "assigned_agent_id"])
    op.create_index("idx_sms_leads_created", "sms_leads", ["tenant_id", "created_at"])

    op.create_table(
        "sms_queue_agents",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OFFLINE"),
        sa.Column("queue_position", sa.Integer(), nullable=True),
        sa.Column("current_lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("consecutive_misses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_leads_handled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_appointments_set", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_response_time_ms", sa.Integer(), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_sms_queue_agents_tenant_user"),
    )
    op.create_index("idx_sms_queue_agents_tenant_status", "sms_queue_agents", ["tenant_id", "status"])

    op.create_table(
        "sms_messages",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("sms_lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sms_leads.id"), nullable=True),
        sa.Column("phone_number", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_sms_messages_lead_created", "sms_messages", ["sms_lead_id", "created_at"])
    op.create_index("idx_sms_messages_tenant_dir_status", "sms_messages", ["tenant_id", "direction", "status"])
    op.create_index("idx_sms_messages_tenant_created", "sms_messages", ["tenant_id", "created_at"])
    op.create_index("ix_sms_messages_provider_message_id", "sms_messages", ["provider_message_id"])

    op.create_table(
        "sms_poll_log",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("messages_pulled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("idx_sms_poll_log_attempted", "sms_poll_log", ["attempted_at"])
    op.create_index("idx_sms_poll_log_tenant_attempted", "sms_poll_log", ["tenant_id", "attempted_at"])


def downgrade() -> None:
    op.drop_table("sms_poll_log")
    op.drop_table("sms_messages")
    op.drop_table("sms_queue_agents")
    op.drop_table("sms_leads")
