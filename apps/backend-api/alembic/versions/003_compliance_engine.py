"""carrier appointment compliance engine

Revision ID: 003
Revises: 002
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_state_licenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("license_number", sa.String(100), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_state_licenses_tenant_id", "agent_state_licenses", ["tenant_id"])
    op.create_index("ix_agent_state_licenses_agent_id", "agent_state_licenses", ["agent_id"])
    op.create_index("idx_agent_state_licenses_agent_state", "agent_state_licenses", ["tenant_id", "agent_id", "state_code"])
    op.create_index("idx_agent_state_licenses_expiration", "agent_state_licenses", ["tenant_id", "expiration_date"])

    op.create_table(
        "agent_carrier_appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("carrier_name", sa.String(120), nullable=False),
        sa.Column("carrier_key", sa.String(120), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("appointment_number", sa.String(100), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_carrier_appointments_tenant_id", "agent_carrier_appointments", ["tenant_id"])
    op.create_index("ix_agent_carrier_appointments_agent_id", "agent_carrier_appointments", ["agent_id"])
    op.create_index(
        "idx_agent_carrier_appts_agent_carrier_state",
        "agent_carrier_appointments",
        ["tenant_id", "agent_id", "carrier_key", "state_code"],
    )
    op.create_index("idx_agent_carrier_appts_expiration", "agent_carrier_appointments", ["tenant_id", "expiration_date"])
    op.create_index("idx_agent_carrier_appts_status", "agent_carrier_appointments", ["tenant_id", "status"])

    op.create_table(
        "deals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("carrier", sa.String(120), nullable=False),
        sa.Column("carrier_key", sa.String(120), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("plan_type", sa.String(120), nullable=True),
        sa.Column("premium", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="submitted"),
        sa.Column("approval_decision", sa.String(50), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_deals_tenant_id", "deals", ["tenant_id"])
    op.create_index("ix_deals_agent_id", "deals", ["agent_id"])
    op.create_index("ix_deals_lead_id", "deals", ["lead_id"])
    op.create_index("idx_deals_tenant_created", "deals", ["tenant_id", "created_at"])
    op.create_index("idx_deals_agent_created", "deals", ["tenant_id", "agent_id", "created_at"])
    op.create_index("idx_deals_approval", "deals", ["tenant_id", "approval_decision"])

    op.create_table(
        "compliance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_carrier_appointments.id"), nullable=True),
        sa.Column("deal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("carrier", sa.String(120), nullable=True),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False, server_default="info"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_compliance_events_tenant_id", "compliance_events", ["tenant_id"])
    op.create_index("ix_compliance_events_agent_id", "compliance_events", ["agent_id"])
    op.create_index("idx_compliance_events_tenant_created", "compliance_events", ["tenant_id", "created_at"])
    op.create_index("idx_compliance_events_type", "compliance_events", ["tenant_id", "event_type"])
    op.create_index("idx_compliance_events_resolved", "compliance_events", ["tenant_id", "resolved"])

    op.create_table(
        "deal_approval_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("deal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("carrier", sa.String(120), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("decision", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_deal_approval_logs_tenant_id", "deal_approval_logs", ["tenant_id"])
    op.create_index("ix_deal_approval_logs_deal_id", "deal_approval_logs", ["deal_id"])
    op.create_index("ix_deal_approval_logs_agent_id", "deal_approval_logs", ["agent_id"])
    op.create_index("idx_deal_approval_logs_tenant_created", "deal_approval_logs", ["tenant_id", "created_at"])
    op.create_index("idx_deal_approval_logs_decision", "deal_approval_logs", ["tenant_id", "decision"])


def downgrade() -> None:
    op.drop_table("deal_approval_logs")
    op.drop_table("compliance_events")
    op.drop_table("deals")
    op.drop_table("agent_carrier_appointments")
    op.drop_table("agent_state_licenses")
