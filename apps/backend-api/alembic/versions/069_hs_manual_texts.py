"""HealthSherpa campaigns: manual texts + "needs reply"

Revision ID: 069
Revises: 068
Create Date: 2026-11-13

* hs_campaign_texts.sent_by: who typed a MANUAL text.
* hs_auth_requests.needs_reply: the client wrote something a person should answer.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "sent_by" not in _cols("hs_campaign_texts"):
        op.add_column("hs_campaign_texts", sa.Column(
            "sent_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    if "needs_reply" not in _cols("hs_auth_requests"):
        op.add_column("hs_auth_requests", sa.Column(
            "needs_reply", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if "needs_reply" in _cols("hs_auth_requests"):
        op.drop_column("hs_auth_requests", "needs_reply")
    if "sent_by" in _cols("hs_campaign_texts"):
        op.drop_column("hs_campaign_texts", "sent_by")
