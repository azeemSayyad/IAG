import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Text, String, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DirectMessage(Base):
    """One in-app message — either 1:1 or to a group. NOT SMS.

    Delivered in realtime over Socket.IO and persisted here as the source of
    truth. Exactly ONE of the two addressing columns is set:

      * ``recipient_id`` -> a 1:1 thread, i.e. the history between an unordered
        pair of users. ``read_at`` is set when the recipient opens the thread.
      * ``group_id``     -> a group thread (see DmGroup). ``read_at`` stays NULL
        for these; "read" is per member and lives on
        ``DmGroupMember.last_read_at``, because one row has many readers.
    """

    __tablename__ = "direct_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("dm_groups.id"), nullable=True, index=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_direct_messages_pair", "tenant_id", "sender_id", "recipient_id", "created_at"),
        Index("idx_direct_messages_recipient_unread", "recipient_id", "read_at"),
        Index("idx_direct_messages_group", "group_id", "created_at"),
    )


class DmGroup(Base):
    """A named in-app group conversation (e.g. "Managers", "All Agents").

    Only a super_admin creates, renames, changes the membership of or deletes a
    group; every member can read it and reply in it. Deletion is soft
    (``deleted_at``) so the message history stays referentially intact.
    """

    __tablename__ = "dm_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_dm_groups_tenant_live", "tenant_id", "deleted_at"),
    )


class DmGroupMember(Base):
    """Membership row. ``last_read_at`` is that member's own read watermark:
    their unread count is the group's messages newer than it, minus their own."""

    __tablename__ = "dm_group_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("dm_groups.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    added_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_dm_group_member"),
    )
