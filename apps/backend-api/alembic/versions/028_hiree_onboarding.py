"""hiree onboarding applications

Revision ID: 028
Revises: 027
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hiree_onboarding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # Account
        sa.Column("full_legal_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        # Personal details
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("ssn", sa.String(20), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("street_address", sa.String(255), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(50), nullable=True),
        sa.Column("zip", sa.String(20), nullable=True),
        # Verify identity
        sa.Column("id_front_key", sa.String(500), nullable=True),
        sa.Column("id_back_key", sa.String(500), nullable=True),
        # Sign agreement
        sa.Column("agreement_signed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Agency releases
        sa.Column("needs_release", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("releases", postgresql.JSONB(), nullable=True),
        # Carrier portals
        sa.Column("has_carrier_logins", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("carrier_logins", postgresql.JSONB(), nullable=True),
        # States licensed in
        sa.Column("licensed_states", postgresql.JSONB(), nullable=True),
        # Review meta
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_agent_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hiree_onboarding_tenant_id", "hiree_onboarding", ["tenant_id"])
    op.create_index("ix_hiree_onboarding_email", "hiree_onboarding", ["email"])
    op.create_index("idx_hiree_onboarding_tenant_status", "hiree_onboarding", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_hiree_onboarding_tenant_status", table_name="hiree_onboarding")
    op.drop_index("ix_hiree_onboarding_email", table_name="hiree_onboarding")
    op.drop_index("ix_hiree_onboarding_tenant_id", table_name="hiree_onboarding")
    op.drop_table("hiree_onboarding")
