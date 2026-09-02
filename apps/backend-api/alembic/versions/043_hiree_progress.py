"""hiree onboarding: in-progress draft state

Revision ID: 043
Revises: 042
Create Date: 2026-06-25

Adds hiree_onboarding.progress (JSONB) — the form's saved navigation/field state
so a half-finished application can be resumed from the backend (any device),
instead of only browser sessionStorage. Drafts use status='draft'; no new
status column or constraint is needed (status is a free String(20)). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "progress"):
        op.add_column(
            "hiree_onboarding",
            sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    if _has_column("hiree_onboarding", "progress"):
        op.drop_column("hiree_onboarding", "progress")
