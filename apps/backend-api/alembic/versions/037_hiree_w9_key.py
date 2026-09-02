"""hiree onboarding: W-9 tax form key

Revision ID: 037
Revises: 036
Create Date: 2026-06-23

Adds hiree_onboarding.w9_signed + w9_key (the signed W-9 the hiree uploads on
step 10). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "w9_signed"):
        op.add_column(
            "hiree_onboarding",
            sa.Column("w9_signed", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column("hiree_onboarding", "w9_key"):
        op.add_column("hiree_onboarding", sa.Column("w9_key", sa.String(500), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "w9_key"):
        op.drop_column("hiree_onboarding", "w9_key")
    if _has_column("hiree_onboarding", "w9_signed"):
        op.drop_column("hiree_onboarding", "w9_signed")
