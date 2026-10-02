"""deal source: carrier deal vs EAP deal

Revision ID: 059
Revises: 058
Create Date: 2026-10-03

Adds deals.deal_source — 'carrier' (the default, e.g. an Anthem sale) or 'eap'.
Set per person on the Add Deal form. Existing rows become 'carrier'. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    return "deal_source" in {c["name"] for c in inspect(op.get_bind()).get_columns("deals")}


def upgrade() -> None:
    if not _has_column():
        op.add_column("deals", sa.Column("deal_source", sa.String(20), nullable=False,
                                         server_default="carrier"))


def downgrade() -> None:
    if _has_column():
        op.drop_column("deals", "deal_source")
