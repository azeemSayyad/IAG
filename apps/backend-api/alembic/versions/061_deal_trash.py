"""deal trash: admins move a sale to Trash instead of deleting it

Revision ID: 061
Revises: 060
Create Date: 2026-10-04

Adds deals.trashed_at / trashed_by. A trashed deal is hidden from every read
(core/database.py filters it out of every Deal query) so it stops counting
toward commissions and the weekly tier, and can be restored. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols() -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("deals")}


def upgrade() -> None:
    cols = _cols()
    if "trashed_at" not in cols:
        op.add_column("deals", sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_deals_trashed_at", "deals", ["trashed_at"])
    if "trashed_by" not in cols:
        op.add_column("deals", sa.Column("trashed_by", postgresql.UUID(as_uuid=True),
                                         sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    cols = _cols()
    if "trashed_by" in cols:
        op.drop_column("deals", "trashed_by")
    if "trashed_at" in cols:
        op.drop_index("ix_deals_trashed_at", table_name="deals")
        op.drop_column("deals", "trashed_at")
