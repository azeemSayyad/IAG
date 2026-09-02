"""Deal: up to 4 call recordings

Revision ID: 026
Revises: 025
Create Date: 2026-06-18

Adds deals.recording_ids (JSONB) to hold up to 4 call-recording UUIDs per deal.
deals.recording_id stays as the PRIMARY (first) recording for back-compat (the
All Deals download/play column reads it). At least one recording is required by
the Add Deal form's frontend gate. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("deals", "recording_ids"):
        op.add_column(
            "deals",
            sa.Column("recording_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    if _has_column("deals", "recording_ids"):
        op.drop_column("deals", "recording_ids")
