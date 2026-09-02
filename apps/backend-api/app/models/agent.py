import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Agent(Base):
    __tablename__ = "agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    timezone = Column(String(50), nullable=False, default="America/New_York")
    daily_capacity = Column(Integer, nullable=False, default=8)
    max_concurrent = Column(Integer, nullable=False, default=1)
    skills = Column(JSONB, default=[])
    weight = Column(Integer, nullable=False, default=100)
    status = Column(String(50), nullable=False, default="active")
    # Per-agent caller ID (the Sinch voice number the LEAD sees on the call).
    # Admin-assigned from the frontend; distinct from any personal phone.
    caller_number = Column(String(32), nullable=True)
    national_producer_number = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant", back_populates="agents")
    user = relationship("User", back_populates="agent")
    availability = relationship("AgentAvailability", back_populates="agent")
    appointments = relationship("Appointment", back_populates="agent")
