"""hiree onboarding: admin-uploaded onboarding document

Revision ID: 047
Revises: 046
Create Date: 2026-06-30

Adds hiree_onboarding.onboarding_doc_key (S3/DB key of a standalone onboarding
document an admin can attach for agents who were hired but never went through the
self-onboarding flow). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("hiree_onboarding", "onboarding_doc_key"):
        op.add_column("hiree_onboarding", sa.Column("onboarding_doc_key", sa.String(length=500), nullable=True))


def downgrade() -> None:
    if _has_column("hiree_onboarding", "onboarding_doc_key"):
        op.drop_column("hiree_onboarding", "onboarding_doc_key")
