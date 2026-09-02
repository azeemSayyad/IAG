"""deal per-person Social (SSN) on the Add Deal form

Revision ID: 022
Revises: 021
Create Date: 2026-06-16

Adds deals.customer_ssn (the "Social" field) — nullable, additive, so existing
deals and the dashboards keep working. Idempotent. NOTE: this is sensitive PII;
consider masking in the UI and encrypting at rest as a follow-up.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "customer_ssn" not in _cols("deals"):
        op.add_column("deals", sa.Column("customer_ssn", sa.String(length=20), nullable=True))


def downgrade() -> None:
    if "customer_ssn" in _cols("deals"):
        op.drop_column("deals", "customer_ssn")
