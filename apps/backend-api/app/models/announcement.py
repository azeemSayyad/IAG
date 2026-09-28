"""
Admin announcements + per-user acknowledgements.

An admin pushes an announcement to all agents, everyone, or hand-picked people
(`audience`). Every recipient sees a blocking popup on whatever page they are on
and must press Received — so acks are tracked per user in announcement_acks,
which is what the sender's "N of M received" reads.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    # 'agents' | 'everyone' | 'custom' (people in announcement_recipients).
    # NULL = a row from before audiences: target_agent_id below decides.
    audience = Column(String(20), nullable=True)
    # Legacy: NULL = every agent; otherwise the single targeted agent.
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("idx_announcements_tenant_active", "tenant_id", "active"),)


class AnnouncementAck(Base):
    __tablename__ = "announcement_acks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    announcement_id = Column(UUID(as_uuid=True), ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    acked_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("uq_ann_ack", "announcement_id", "user_id", unique=True),)


class AnnouncementRecipient(Base):
    """A hand-picked recipient of an audience='custom' announcement."""

    __tablename__ = "announcement_recipients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    announcement_id = Column(UUID(as_uuid=True), ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("announcement_id", "user_id", name="uq_ann_recipient"),)
