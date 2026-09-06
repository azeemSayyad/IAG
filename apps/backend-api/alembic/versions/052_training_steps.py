"""Agent training program steps

Revision ID: 052
Revises: 051
Create Date: 2026-09-07

Creates `training_steps` — the admin-editable, ordered steps of the agent
Training page. A step's video is either an external link or a file uploaded to
S3 (DB bytes when S3 isn't configured, like deal recordings). Soft-deleted via
`deleted_at`. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("training_steps"):
        return
    op.create_table(
        "training_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("video_kind", sa.String(10), nullable=False, server_default="none"),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("video_filename", sa.String(255), nullable=True),
        sa.Column("video_content_type", sa.String(100), nullable=True),
        sa.Column("video_byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("video_storage", sa.String(10), nullable=True),
        sa.Column("video_s3_bucket", sa.String(255), nullable=True),
        sa.Column("video_s3_key", sa.String(512), nullable=True),
        sa.Column("video_data", sa.LargeBinary(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_training_steps_tenant_id", "training_steps", ["tenant_id"])
    op.create_index("idx_training_steps_tenant_position", "training_steps", ["tenant_id", "position"])


def downgrade() -> None:
    if _has_table("training_steps"):
        op.drop_table("training_steps")
