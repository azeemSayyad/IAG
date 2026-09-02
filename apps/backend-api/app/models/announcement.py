"""
Admin announcements + per-user acknowledgements.

An admin pushes an announcement to ALL agents (target_agent_id NULL) or to ONE
agent. Every targeted agent must acknowledge it (a blocking, blurred popup) before
the UI unblocks — so acks are tracked per user in announcement_acks.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    # NULL = broadcast to every agent; otherwise the single targeted agent.
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
