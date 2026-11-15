"""HealthSherpa campaigns: editable follow-up text

Revision ID: 073
Revises: 072
Create Date: 2026-11-15

* hs_text_campaigns.followup_text: the text sent after a YES, per campaign.
  NULL = the approved default (campaigns.FOLLOWUP_TEXT). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "followup_text" not in _cols("hs_text_campaigns"):
        op.add_column("hs_text_campaigns", sa.Column("followup_text", sa.Text(), nullable=True))


def downgrade() -> None:
    if "followup_text" in _cols("hs_text_campaigns"):
        op.drop_column("hs_text_campaigns", "followup_text")
