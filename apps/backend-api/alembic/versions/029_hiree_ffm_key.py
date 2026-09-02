"""hiree onboarding: FFM certificate key

Revision ID: 029
Revises: 028
Create Date: 2026-06-20

Adds hiree_onboarding.ffm_key (S3 key for the agent's FFM/CMS certificate,
uploaded on its own onboarding step). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "ffm_key"):
        op.add_column("hiree_onboarding", sa.Column("ffm_key", sa.String(500), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "ffm_key"):
        op.drop_column("hiree_onboarding", "ffm_key")
