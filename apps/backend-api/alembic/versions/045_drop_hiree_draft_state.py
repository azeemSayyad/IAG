"""hiree onboarding: drop in-progress draft state

Revision ID: 045
Revises: 044
Create Date: 2026-06-25

Reverts the in-progress draft-persistence feature (migrations 043 + 044).
The draft 'resume from backend' flow and its admin-notification cadence were
rolled back in the app, so the backing columns are dropped here rather than
deleting 043/044 — those are already applied on existing databases, and
removing them from the chain would break `alembic upgrade head`. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if _has_column("hiree_onboarding", "last_notified_step"):
        op.drop_column("hiree_onboarding", "last_notified_step")
    if _has_column("hiree_onboarding", "last_notified_at"):
        op.drop_column("hiree_onboarding", "last_notified_at")
    if _has_column("hiree_onboarding", "progress"):
        op.drop_column("hiree_onboarding", "progress")


def downgrade() -> None:
    # Re-add the columns (mirrors 043 + 044) so a downgrade restores the schema.
    if not _has_column("hiree_onboarding", "progress"):
        op.add_column(
            "hiree_onboarding",
            sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if not _has_column("hiree_onboarding", "last_notified_at"):
        op.add_column("hiree_onboarding", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("hiree_onboarding", "last_notified_step"):
        op.add_column("hiree_onboarding", sa.Column("last_notified_step", sa.Integer(), nullable=True))
