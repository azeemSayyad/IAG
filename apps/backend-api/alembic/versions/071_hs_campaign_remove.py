"""HealthSherpa campaigns can be removed

Revision ID: 071
Revises: 070
Create Date: 2026-11-14

* hs_text_campaigns.deleted_at: a campaign that already texted is hidden, not
  deleted — its texts stay so late replies are still recognised (never dialed).
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "deleted_at" not in _cols("hs_text_campaigns"):
        op.add_column("hs_text_campaigns", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if "deleted_at" in _cols("hs_text_campaigns"):
        op.drop_column("hs_text_campaigns", "deleted_at")
