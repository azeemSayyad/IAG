"""pool list serving order + pause

Revision ID: 057
Revises: 056
Create Date: 2026-09-25

sms_pool_batches gains:
  * priority   — serving order set on the SMS Manager (lower = first). Backfilled
                 oldest-upload-first, which is exactly the order leads were served
                 in before, so nothing changes until someone reorders.
  * paused_at  — a paused list keeps its leads but they leave the pool until resumed.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols() -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("sms_pool_batches")}


def upgrade() -> None:
    cols = _cols()
    if "priority" not in cols:
        op.add_column("sms_pool_batches", sa.Column("priority", sa.Integer(), nullable=True))
        op.execute("""
            UPDATE sms_pool_batches b SET priority = o.rn
            FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY created_at) AS rn
                  FROM sms_pool_batches) o
            WHERE b.id = o.id
        """)
    if "paused_at" not in cols:
        op.add_column("sms_pool_batches", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    cols = _cols()
    if "paused_at" in cols:
        op.drop_column("sms_pool_batches", "paused_at")
    if "priority" in cols:
        op.drop_column("sms_pool_batches", "priority")
