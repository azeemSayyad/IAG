"""lead assigned agent

Revision ID: 006
Revises: 005
Create Date: 2026-06-09

Adds leads.assigned_agent_id so the AI can auto-distribute new leads to agents
(compliance-aware) and head/admin can reassign. Nullable FK to agents.id.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("assigned_agent_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_leads_assigned_agent",
        "leads",
        "agents",
        ["assigned_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_leads_assigned_agent",
        "leads",
        ["tenant_id", "assigned_agent_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_leads_assigned_agent", table_name="leads")
    op.drop_constraint("fk_leads_assigned_agent", "leads", type_="foreignkey")
    op.drop_column("leads", "assigned_agent_id")
