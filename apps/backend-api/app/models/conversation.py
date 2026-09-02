import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="initiated")
    intent = Column(String(50), nullable=True)
    sentiment = Column(String(50), nullable=True)
    message_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    last_message_from = Column(String(20), nullable=True)
    ai_context = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")

    __table_args__ = (
        Index("idx_conversations_tenant_lead", "tenant_id", "lead_id"),
        Index("idx_conversations_status", "tenant_id", "status"),
        Index("idx_conversations_last_message", "tenant_id", "last_message_at"),
    )
