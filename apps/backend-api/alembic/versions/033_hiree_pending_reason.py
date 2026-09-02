"""hiree onboarding: pending reason

Revision ID: 033
Revises: 032
Create Date: 2026-06-22

Adds hiree_onboarding.pending_reason (why an admin is holding the application
in 'pending'). Submissions now default to status 'new'. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "pending_reason"):
        op.add_column("hiree_onboarding", sa.Column("pending_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "pending_reason"):
        op.drop_column("hiree_onboarding", "pending_reason")
