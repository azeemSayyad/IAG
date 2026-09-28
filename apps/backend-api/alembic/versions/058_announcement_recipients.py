"""announcement audiences: all agents / everyone / picked people

Revision ID: 058
Revises: 057
Create Date: 2026-09-28

  * announcements.audience       — 'agents' | 'everyone' | 'custom'. NULL on rows
                                   written before this (they keep the old
                                   target_agent_id meaning).
  * announcement_recipients      — the picked people for audience='custom'.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "audience" not in {c["name"] for c in insp.get_columns("announcements")}:
        op.add_column("announcements", sa.Column("audience", sa.String(20), nullable=True))
    if not insp.has_table("announcement_recipients"):
        op.create_table(
            "announcement_recipients",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("announcement_id", postgresql.UUID(as_uuid=True),
                      sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.UniqueConstraint("announcement_id", "user_id", name="uq_ann_recipient"),
        )
        op.create_index("ix_announcement_recipients_announcement_id", "announcement_recipients", ["announcement_id"])
        op.create_index("ix_announcement_recipients_user_id", "announcement_recipients", ["user_id"])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("announcement_recipients"):
        op.drop_table("announcement_recipients")
    if "audience" in {c["name"] for c in insp.get_columns("announcements")}:
        op.drop_column("announcements", "audience")
