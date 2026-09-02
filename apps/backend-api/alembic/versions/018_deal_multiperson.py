"""deal multi-person form: per-person DOB + per-product plan detail

Revision ID: 018
Revises: 017
Create Date: 2026-06-15

The Add Deal form now captures each PERSON as its own deal, and each person can
hold full plan detail for several products. Adds:
  * deals.customer_dob  (per-person date of birth)
  * deals.products      (JSONB list of {product,carrier,tier,plan_name,premium,
                         effective_date,decision} for this person)
The aca/dental/vision counts are unchanged (still the 0/1 flags the dashboards
sum), so My Deals / All Deals / Leaderboard keep working untouched. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _cols("deals")
    if "customer_dob" not in cols:
        op.add_column("deals", sa.Column("customer_dob", sa.String(length=20), nullable=True))
    if "products" not in cols:
        op.add_column("deals", sa.Column("products", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    cols = _cols("deals")
    for c in ("products", "customer_dob"):
        if c in cols:
            op.drop_column("deals", c)
