"""campaign first_template — per-campaign first-message body

Revision ID: 042
Revises: 041
Create Date: 2026-06-24
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("first_template", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "first_template")
