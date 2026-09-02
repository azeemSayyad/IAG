"""campaign provider — per-campaign lead-SMS provider ("sinch" | "engage2")

Adds a NOT NULL column with server_default 'sinch', so every existing campaign
backfills to the original Sinch pipeline (zero behaviour change). "engage2" selects
the independent Engage Cloud pipeline for that campaign's first-templates.

Revision ID: 046
Revises: 045
Create Date: 2026-06-28
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="sinch"),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "provider")
