"""deal per-person marital status, tobacco use, annual income

Revision ID: 021
Revises: 020
Create Date: 2026-06-16

More per-person detail captured on the Add Deal form. Adds (all nullable):
  * deals.customer_marital_status
  * deals.customer_tobacco        (Non-smoker | Smoker)
  * deals.customer_income         (annual household income, freeform)
Additive only — existing deals and the dashboards keep working. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW = {
    "customer_marital_status": sa.String(length=30),
    "customer_tobacco": sa.String(length=20),
    "customer_income": sa.String(length=50),
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
