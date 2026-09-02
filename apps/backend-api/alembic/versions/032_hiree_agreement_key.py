"""hiree onboarding: signed agreement key

Revision ID: 032
Revises: 031
Create Date: 2026-06-22

Adds hiree_onboarding.agreement_key (S3/DB key of the signed onboarding
agreement the hiree uploads on step 4). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "agreement_key"):
        op.add_column("hiree_onboarding", sa.Column("agreement_key", sa.String(500), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "agreement_key"):
        op.drop_column("hiree_onboarding", "agreement_key")
