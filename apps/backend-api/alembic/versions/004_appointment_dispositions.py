"""appointment dispositions

Revision ID: 004
Revises: 003
Create Date: 2026-06-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointment_dispositions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("appointments.id"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("submitted_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("disposition_key", sa.String(50), nullable=False),
        sa.Column("disposition_label", sa.String(120), nullable=False),
        sa.Column("outcome_category", sa.String(50), nullable=False),
        sa.Column("customer_picked_up", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("insurance_sold", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(50), nullable=False),
        sa.Column("appointment_start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("appointment_end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("call_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("sale_carrier", sa.String(120), nullable=True),
        sa.Column("sale_product", sa.String(120), nullable=True),
        sa.Column("premium_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("policy_number", sa.String(120), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("appointment_id", name="uq_appointment_dispositions_appointment_id"),
    )
    op.create_index("ix_appointment_dispositions_tenant_id", "appointment_dispositions", ["tenant_id"])
    op.create_index("ix_appointment_dispositions_appointment_id", "appointment_dispositions", ["appointment_id"])
    op.create_index("ix_appointment_dispositions_lead_id", "appointment_dispositions", ["lead_id"])
    op.create_index("ix_appointment_dispositions_agent_id", "appointment_dispositions", ["agent_id"])
    op.create_index("idx_appt_dispositions_tenant_created", "appointment_dispositions", ["tenant_id", "created_at"])
    op.create_index("idx_appt_dispositions_agent_created", "appointment_dispositions", ["tenant_id", "agent_id", "created_at"])
    op.create_index("idx_appt_dispositions_key", "appointment_dispositions", ["tenant_id", "disposition_key"])
    op.create_index("idx_appt_dispositions_slot", "appointment_dispositions", ["tenant_id", "appointment_start_time"])


def downgrade() -> None:
    op.drop_table("appointment_dispositions")
