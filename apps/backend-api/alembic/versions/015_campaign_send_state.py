"""campaign send-batch control: send_state + per-campaign drip rate

Revision ID: 015
Revises: 014
Create Date: 2026-06-15

Adds per-campaign send control for the Upload-Leads campaign manager:
  * campaigns.send_state  (ready | running | paused | stopped)
  * campaigns.drip_leads  (per-campaign drip count)
  * campaigns.drip_minutes(per-campaign drip interval)
Idempotent: skips any column that already exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _cols("campaigns")
    if "send_state" not in cols:
        op.add_column("campaigns", sa.Column("send_state", sa.String(length=20),
                                             nullable=False, server_default="ready"))
    if "drip_leads" not in cols:
        op.add_column("campaigns", sa.Column("drip_leads", sa.Integer(),
                                             nullable=False, server_default="50"))
    if "drip_minutes" not in cols:
        op.add_column("campaigns", sa.Column("drip_minutes", sa.Integer(),
                                             nullable=False, server_default="10"))


def downgrade() -> None:
    cols = _cols("campaigns")
    for c in ("drip_minutes", "drip_leads", "send_state"):
        if c in cols:
            op.drop_column("campaigns", c)
