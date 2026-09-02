"""appointment capacity engine — lead pacing columns

Revision ID: 008
Revises: 006
Create Date: 2026-06-11

Adds same-day lead-pacing columns to leads so the Appointment Capacity Engine can
hold imported leads and release them in controlled, capacity-aware waves. All
columns are nullable / defaulted and inert unless SAME_DAY_PACING_ENABLED is on.
See docs/APPOINTMENT_CAPACITY_ENGINE*.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("pacing_status", sa.String(length=30), nullable=True))
    op.add_column("leads", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("wave_id", sa.String(length=64), nullable=True))
    op.add_column("leads", sa.Column("priority_score", sa.Float(), nullable=True, server_default="0"))
    # Fast lookup of the per-state held pool, ordered by priority.
    op.create_index(
        "idx_leads_pacing",
        "leads",
        ["tenant_id", "state", "pacing_status", "priority_score"],
    )


def downgrade() -> None:
    op.drop_index("idx_leads_pacing", table_name="leads")
    op.drop_column("leads", "priority_score")
    op.drop_column("leads", "wave_id")
    op.drop_column("leads", "released_at")
    op.drop_column("leads", "pacing_status")
