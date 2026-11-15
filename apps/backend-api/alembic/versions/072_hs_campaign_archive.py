"""HealthSherpa campaigns can be archived

Revision ID: 072
Revises: 071
Create Date: 2026-11-15

* hs_text_campaigns.archived_at: hidden from the campaign list only — the
  campaign keeps working exactly as before (owner: "just to make the UI clean").
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "archived_at" not in _cols("hs_text_campaigns"):
        op.add_column("hs_text_campaigns", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if "archived_at" in _cols("hs_text_campaigns"):
        op.drop_column("hs_text_campaigns", "archived_at")
