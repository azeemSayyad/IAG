import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class Notification(Base):
    """A per-user in-app notification, shown in the Notifications tab.

    Recipient-scoped (one row per user who should see it), so an agent only ever
    reads their own. Used for the license review flow (agent submits → admins
    notified; admin approves/rejects → agent notified). SMS wiring is separate.
    """

    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    # The user who should SEE this notification.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(60), nullable=False, default="system")
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String(300), nullable=True)
    # Optional pointer to the related resource (e.g. the license id).
    resource_type = Column(String(60), nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    meta = Column(JSONB, default={})
    read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_notifications_user_read", "tenant_id", "user_id", "read"),
        Index("idx_notifications_user_created", "tenant_id", "user_id", "created_at"),
    )
