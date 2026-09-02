"""in-app direct messages (admin↔agent realtime chat)

Revision ID: 041
Revises: 040
Create Date: 2026-06-24

Adds the direct_messages table powering the in-app (Socket.IO) admin↔agent chat:
the admin Inbox's agent threads and the agent's "Admin Inbox" page. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import inspect


revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    if _has_table("direct_messages"):
        return
    op.create_table(
        "direct_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("sender_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recipient_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_direct_messages_tenant_id", "direct_messages", ["tenant_id"])
    op.create_index("idx_direct_messages_pair", "direct_messages", ["tenant_id", "sender_id", "recipient_id", "created_at"])
    op.create_index("idx_direct_messages_recipient_unread", "direct_messages", ["recipient_id", "read_at"])


def downgrade() -> None:
    if _has_table("direct_messages"):
        op.drop_table("direct_messages")
