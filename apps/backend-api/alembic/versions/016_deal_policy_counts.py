"""deal policy breakdown: aca/dental/vision counts

Revision ID: 016
Revises: 015
Create Date: 2026-06-15

Records how many policies a single enrollment covers, so the Add-Deal wizard's
Dental & Vision toggle can capture e.g. 4 ACA + 2 Dental + 5 Vision (total 11):
  * deals.aca_count     (default 1 — the ACA master policy)
  * deals.dental_count  (default 0)
  * deals.vision_count  (default 0)
Total deals = aca_count + dental_count + vision_count.
Idempotent: skips any column that already exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _cols("deals")
    if "aca_count" not in cols:
        op.add_column("deals", sa.Column("aca_count", sa.Integer(),
                                         nullable=False, server_default="1"))
    if "dental_count" not in cols:
        op.add_column("deals", sa.Column("dental_count", sa.Integer(),
                                         nullable=False, server_default="0"))
    if "vision_count" not in cols:
        op.add_column("deals", sa.Column("vision_count", sa.Integer(),
                                         nullable=False, server_default="0"))


def downgrade() -> None:
    cols = _cols("deals")
    for c in ("vision_count", "dental_count", "aca_count"):
        if c in cols:
            op.drop_column("deals", c)
