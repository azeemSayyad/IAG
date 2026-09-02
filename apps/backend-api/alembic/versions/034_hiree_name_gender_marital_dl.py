"""hiree onboarding: split name + gender, marital status, driver license

Revision ID: 034
Revises: 033
Create Date: 2026-06-22

Adds first/middle/last name, gender, marital_status, drivers_license_number to
hiree_onboarding. full_legal_name is retained (composed from the parts on
submit). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = [
    ("first_name", sa.String(100)),
    ("middle_name", sa.String(100)),
    ("last_name", sa.String(100)),
    ("gender", sa.String(30)),
    ("marital_status", sa.String(30)),
    ("drivers_license_number", sa.String(60)),
]


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        if not _has_column("hiree_onboarding", name):
            op.add_column("hiree_onboarding", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        if _has_column("hiree_onboarding", name):
            op.drop_column("hiree_onboarding", name)
