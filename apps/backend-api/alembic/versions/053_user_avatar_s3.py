"""Profile photos in S3

Revision ID: 053
Revises: 052
Create Date: 2026-09-07

Adds users.avatar_s3_bucket / avatar_s3_key. A profile photo is now uploaded to
S3 (Railway Buckets etc. via app.calls.s3_storage — the same client as training
videos and deal consent forms) and only the object key is kept on the row.
`avatar_url` stays as the inline data-URL fallback when S3 isn't configured;
existing data-URL photos are moved to S3 lazily on the next GET /auth/me.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("users", "avatar_s3_bucket"):
        op.add_column("users", sa.Column("avatar_s3_bucket", sa.String(255), nullable=True))
    if not _has_column("users", "avatar_s3_key"):
        op.add_column("users", sa.Column("avatar_s3_key", sa.String(512), nullable=True))


def downgrade() -> None:
    if _has_column("users", "avatar_s3_key"):
        op.drop_column("users", "avatar_s3_key")
    if _has_column("users", "avatar_s3_bucket"):
        op.drop_column("users", "avatar_s3_bucket")
