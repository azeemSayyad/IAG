"""hiree onboarding: NPN (National Producer Number)

Revision ID: 031
Revises: 030
Create Date: 2026-06-20

Adds hiree_onboarding.npn; copied to agents.national_producer_number on approve.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "npn"):
        op.add_column("hiree_onboarding", sa.Column("npn", sa.String(50), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "npn"):
        op.drop_column("hiree_onboarding", "npn")
