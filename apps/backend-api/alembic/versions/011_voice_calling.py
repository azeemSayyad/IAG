"""voice calling: per-agent caller number + sinch/s3 call fields

Revision ID: 011
Revises: 010
Create Date: 2026-06-09

Adds agents.caller_number (per-agent Sinch caller ID) and extends call_recordings
with Sinch WebRTC outbound-call lifecycle + permanent-S3 storage fields.

Chained after 010 (sms manager panels). The SMS subsystem migrations (009, 010)
landed on production first, so this voice migration is re-numbered to keep the
alembic history linear (…008 -> 009 -> 010 -> 011) with a single head.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from sqlalchemy import inspect


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


# Idempotent: a column is only added if it isn't already present, so this runs
# cleanly on a fresh DB (production) AND on a DB where an earlier WIP already
# created some of these columns (e.g. a dev box). Same for the index.
_CALL_COLS = [
    ("provider", sa.String(), {"server_default": "sinch"}),
    ("sinch_call_id", sa.String(), {}),
    ("sinch_recording_id", sa.String(), {}),
    ("direction", sa.String(), {"server_default": "outbound"}),
    ("from_number", sa.String(), {}),
    ("to_number", sa.String(), {}),
    ("call_status", sa.String(), {"server_default": "initiated"}),
    ("disclosure_played", sa.Integer(), {"server_default": "0"}),
    ("started_at", sa.DateTime(timezone=True), {}),
    ("answered_at", sa.DateTime(timezone=True), {}),
    ("ended_at", sa.DateTime(timezone=True), {}),
    ("s3_bucket", sa.String(), {}),
    ("s3_key", sa.String(), {}),
    ("recording_status", sa.String(), {"server_default": "none"}),
]


def upgrade() -> None:
    if "caller_number" not in _cols("agents"):
        op.add_column("agents", sa.Column("caller_number", sa.String(32), nullable=True))

    existing = _cols("call_recordings")
    for name, coltype, kw in _CALL_COLS:
        if name not in existing:
            op.add_column("call_recordings", sa.Column(name, coltype, nullable=True, **kw))

    if "idx_call_recordings_sinch_call" not in _indexes("call_recordings"):
        op.create_index("idx_call_recordings_sinch_call", "call_recordings", ["sinch_call_id"])


def downgrade() -> None:
    if "idx_call_recordings_sinch_call" in _indexes("call_recordings"):
        op.drop_index("idx_call_recordings_sinch_call", table_name="call_recordings")
    existing = _cols("call_recordings")
    for name, _coltype, _kw in _CALL_COLS:
        if name in existing:
            op.drop_column("call_recordings", name)
    if "caller_number" in _cols("agents"):
        op.drop_column("agents", "caller_number")
