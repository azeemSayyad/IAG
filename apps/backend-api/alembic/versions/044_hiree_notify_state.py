"""hiree onboarding: in-progress notification tracking

Revision ID: 044
Revises: 043
Create Date: 2026-06-25

Adds hiree_onboarding.last_notified_at + last_notified_step so admin
notifications fire on the right cadence: once when an application is started,
then again only when the applicant returns ON A LATER CALENDAR DAY and has
progressed further (never once-per-step within a single sitting). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "last_notified_at"):
        op.add_column("hiree_onboarding", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("hiree_onboarding", "last_notified_step"):
        op.add_column("hiree_onboarding", sa.Column("last_notified_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "last_notified_step"):
        op.drop_column("hiree_onboarding", "last_notified_step")
    if _has_column("hiree_onboarding", "last_notified_at"):
        op.drop_column("hiree_onboarding", "last_notified_at")
