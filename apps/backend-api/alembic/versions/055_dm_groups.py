"""in-app group conversations (super_admin creates, members reply)

Revision ID: 055
Revises: 054
Create Date: 2026-09-09

Adds group chat to the Inbox alongside the existing 1:1 in-app DMs:

  * dm_groups        — a named group ("Managers", "All Agents"), soft-deleted.
  * dm_group_members — who is in it + that member's own read watermark. Read
                       state CANNOT live on the message for a group (one row,
                       many readers), hence last_read_at per member.

direct_messages carries both kinds: recipient_id set = 1:1, group_id set =
group. recipient_id therefore becomes nullable. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import inspect


revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    if not _has_table("dm_groups"):
        op.create_table(
            "dm_groups",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_dm_groups_tenant_id", "dm_groups", ["tenant_id"])
        op.create_index("idx_dm_groups_tenant_live", "dm_groups", ["tenant_id", "deleted_at"])

    if not _has_table("dm_group_members"):
        op.create_table(
            "dm_group_members",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("dm_groups.id"), nullable=False),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("group_id", "user_id", name="uq_dm_group_member"),
        )
        op.create_index("idx_dm_group_members_group_id", "dm_group_members", ["group_id"])
        op.create_index("idx_dm_group_members_user_id", "dm_group_members", ["user_id"])

    if not _has_column("direct_messages", "group_id"):
        op.add_column(
            "direct_messages",
            sa.Column("group_id", UUID(as_uuid=True), sa.ForeignKey("dm_groups.id"), nullable=True),
        )
        op.create_index("idx_direct_messages_group", "direct_messages", ["group_id", "created_at"])

    # A group message has no single recipient.
    op.alter_column("direct_messages", "recipient_id", existing_type=UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    if _has_column("direct_messages", "group_id"):
        op.execute("DELETE FROM direct_messages WHERE group_id IS NOT NULL")
        op.drop_index("idx_direct_messages_group", table_name="direct_messages")
        op.drop_column("direct_messages", "group_id")
    op.alter_column("direct_messages", "recipient_id", existing_type=UUID(as_uuid=True), nullable=False)
    if _has_table("dm_group_members"):
        op.drop_table("dm_group_members")
    if _has_table("dm_groups"):
        op.drop_table("dm_groups")
