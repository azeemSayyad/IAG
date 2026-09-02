"""user avatar: profile photo (small base64 data URL)

Revision ID: 013
Revises: 012
Create Date: 2026-06-14

Adds users.avatar_url (TEXT, nullable) so agents can upload a profile photo.
Idempotent: skips if the column already exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    cols = [c["name"] for c in inspect(op.get_bind()).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("users", "avatar_url"):
        op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "avatar_url"):
        op.drop_column("users", "avatar_url")
