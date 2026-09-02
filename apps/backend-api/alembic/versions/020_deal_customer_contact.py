"""deal per-person contact/identity fields on the Add Deal form

Revision ID: 020
Revises: 019
Create Date: 2026-06-16

The Add Deal form now captures more per-person detail. Adds (all nullable):
  * deals.customer_email
  * deals.customer_address
  * deals.customer_city
  * deals.customer_zip
  * deals.customer_gender
Additive only, so every existing deal and My Deals / All Deals / Leaderboard
keep working untouched. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW = {
    "customer_email": sa.String(length=255),
    "customer_address": sa.String(length=255),
    "customer_city": sa.String(length=120),
    "customer_zip": sa.String(length=20),
    "customer_gender": sa.String(length=20),
}


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _cols("deals")
    for name, coltype in _NEW.items():
        if name not in cols:
            op.add_column("deals", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    cols = _cols("deals")
    for name in reversed(list(_NEW)):
        if name in cols:
            op.drop_column("deals", name)
