"""hiree onboarding: bank info + emergency contact

Revision ID: 036
Revises: 035
Create Date: 2026-06-22

Adds hiree_onboarding.bank_info and emergency_contact (JSONB). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import inspect


revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = ["bank_info", "emergency_contact"]


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    for name in _COLUMNS:
        if not _has_column("hiree_onboarding", name):
            op.add_column("hiree_onboarding", sa.Column(name, JSONB(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        if _has_column("hiree_onboarding", name):
            op.drop_column("hiree_onboarding", name)
