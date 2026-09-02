"""zip timezone cache

Revision ID: 005
Revises: 004
Create Date: 2026-06-04

Persistent ZIP -> IANA timezone mapping (populated from Geoapify, cached in Redis too).
Display-only metadata; never gates slot availability.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zip_timezone",
        sa.Column("zip_code", sa.String(10), primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="geoapify"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("zip_timezone")
