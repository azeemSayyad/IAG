"""onboarding documents (stored upload bytes)

Revision ID: 030
Revises: 029
Create Date: 2026-06-20

Persists files uploaded on the public onboarding form (ID photos, FFM cert,
signed releases) so admins can view them — inline in the DB when S3 is not
configured, or in S3 when it is. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(table)


def upgrade() -> None:
    if _has_table("onboarding_documents"):
        return
    op.create_table(
        "onboarding_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(600), nullable=False),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage", sa.String(10), nullable=False, server_default="db"),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("s3_bucket", sa.String(255), nullable=True),
        sa.Column("s3_key", sa.String(600), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ux_onboarding_documents_key", "onboarding_documents", ["key"], unique=True)


def downgrade() -> None:
    if _has_table("onboarding_documents"):
        op.drop_index("ux_onboarding_documents_key", table_name="onboarding_documents")
        op.drop_table("onboarding_documents")
