import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    subscription_plan = Column(String(50), nullable=False, default="starter")
    status = Column(String(50), nullable=False, default="active")
    max_agents = Column(Integer, nullable=False, default=5)
    max_leads_per_month = Column(Integer, nullable=False, default=1000)
    settings = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    users = relationship("User", back_populates="tenant")
    agents = relationship("Agent", back_populates="tenant")
    leads = relationship("Lead", back_populates="tenant")
