"""deal customer phone (for the My Deals page "lead number" column)

Revision ID: 017
Revises: 016
Create Date: 2026-06-15

Adds deals.customer_phone so a logged deal carries the customer's number, shown
as "Lead number" on the agent's My Deals page. Nullable; idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "customer_phone" not in _cols("deals"):
        op.add_column("deals", sa.Column("customer_phone", sa.String(length=50), nullable=True))


def downgrade() -> None:
    if "customer_phone" in _cols("deals"):
        op.drop_column("deals", "customer_phone")
