import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DirectMessage(Base):
    """In-app direct message between two portal users (admin ↔ agent).

    NOT SMS — delivered in realtime over Socket.IO and persisted here as the
    source of truth. A "thread" is the message history between an unordered pair
    of users (sender_id / recipient_id). read_at is set when the recipient opens
    the thread.
    """

    __tablename__ = "direct_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_direct_messages_pair", "tenant_id", "sender_id", "recipient_id", "created_at"),
        Index("idx_direct_messages_recipient_unread", "recipient_id", "read_at"),
    )
