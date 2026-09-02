import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    sender = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    message_type = Column(String(50), nullable=False, default="sms")
    intent = Column(String(50), nullable=True)
    sentiment = Column(String(50), nullable=True)
    msg_metadata = Column("metadata", JSONB, default={})
    provider = Column(String(50), nullable=True)
    provider_message_sid = Column(String(255), nullable=True, index=True)
    delivery_status = Column(String(50), nullable=True)
    delivery_error_code = Column(String(50), nullable=True)
    delivery_error_message = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("idx_messages_conversation", "conversation_id", "created_at"),
        Index("idx_messages_tenant_sender", "tenant_id", "sender"),
    )
