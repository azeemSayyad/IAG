"""agent National Producer Number (NPN)

Revision ID: 023
Revises: 022
Create Date: 2026-06-17

Adds agents.national_producer_number — the agent's NPN. Nullable, additive, so
existing agents and the dashboards keep working. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "national_producer_number" not in _cols("agents"):
        op.add_column("agents", sa.Column("national_producer_number", sa.String(length=50), nullable=True))


def downgrade() -> None:
    if "national_producer_number" in _cols("agents"):
        op.drop_column("agents", "national_producer_number")
