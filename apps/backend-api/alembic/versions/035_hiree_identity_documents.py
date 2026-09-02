"""hiree onboarding: identity documents list

Revision ID: 035
Revises: 034
Create Date: 2026-06-22

Adds hiree_onboarding.identity_documents (JSONB list of uploaded identity
documents — driver's license, state ID, passport — each with front/back keys,
id number, issuing state, issue date, expiration date). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import inspect


revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "identity_documents"):
        op.add_column("hiree_onboarding", sa.Column("identity_documents", JSONB(), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "identity_documents"):
        op.drop_column("hiree_onboarding", "identity_documents")
