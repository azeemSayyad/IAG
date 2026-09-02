"""deal call recording: audio gate on the Add Deal form

Revision ID: 019
Revises: 018
Create Date: 2026-06-16

The Add Deal form now requires the agent to upload the call recording before the
form unlocks. Adds:
  * deal_recordings      (the uploaded audio: an S3 reference OR inline bytes)
  * deals.recording_id   (nullable FK -> deal_recordings.id)
Both are additive / nullable, so every existing deal and My Deals / All Deals /
Leaderboard keep working untouched. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names())


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "deal_recordings" not in _tables():
        op.create_table(
            "deal_recordings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("content_type", sa.String(length=100), nullable=True),
            sa.Column("byte_size", sa.Integer(), server_default="0", nullable=False),
            sa.Column("storage", sa.String(length=10), server_default="db", nullable=False),
            sa.Column("data", sa.LargeBinary(), nullable=True),
            sa.Column("s3_bucket", sa.String(length=255), nullable=True),
            sa.Column("s3_key", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_deal_recordings_tenant_created", "deal_recordings", ["tenant_id", "created_at"])
        op.create_index(op.f("ix_deal_recordings_tenant_id"), "deal_recordings", ["tenant_id"])
        op.create_index(op.f("ix_deal_recordings_agent_id"), "deal_recordings", ["agent_id"])

    if "recording_id" not in _cols("deals"):
        op.add_column("deals", sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index(op.f("ix_deals_recording_id"), "deals", ["recording_id"])
        op.create_foreign_key("fk_deals_recording_id", "deals", "deal_recordings", ["recording_id"], ["id"])


def downgrade() -> None:
    if "recording_id" in _cols("deals"):
        try:
            op.drop_constraint("fk_deals_recording_id", "deals", type_="foreignkey")
        except Exception:
            pass
        op.drop_column("deals", "recording_id")
    if "deal_recordings" in _tables():
        op.drop_table("deal_recordings")
