import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Index, Float, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(50), nullable=False, default="confirmed")
    disposition = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    call_duration_seconds = Column(Integer, nullable=True)
    reminder_24h_sent = Column(Boolean, default=False)
    reminder_1h_sent = Column(Boolean, default=False)
    reminder_15m_sent = Column(Boolean, default=False)
    cancelled_reason = Column(String(255), nullable=True)
    rescheduled_from = Column(UUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True)
    booking_source = Column(String(50), nullable=True, default="ai")  # ai, manual, api
    ai_confidence = Column(Float, nullable=True)  # AI booking confidence 0-1
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead", back_populates="appointments")
    agent = relationship("Agent", back_populates="appointments")
    disposition_record = relationship("AppointmentDisposition", back_populates="appointment", uselist=False)

    __table_args__ = (
        Index("idx_appointments_agent_time", "agent_id", "start_time"),
        Index("idx_appointments_tenant_status", "tenant_id", "status"),
        Index("idx_appointments_lead", "lead_id"),
    )


class AppointmentDisposition(Base):
    __tablename__ = "appointment_dispositions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    appointment_id = Column(UUID(as_uuid=True), ForeignKey("appointments.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    submitted_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    disposition_key = Column(String(50), nullable=False)
    disposition_label = Column(String(120), nullable=False)
    outcome_category = Column(String(50), nullable=False)
    customer_picked_up = Column(Boolean, nullable=False, default=False)
    insurance_sold = Column(Boolean, nullable=False, default=False)

    customer_name = Column(String(255), nullable=False)
    customer_phone = Column(String(50), nullable=False)
    appointment_start_time = Column(DateTime(timezone=True), nullable=False)
    appointment_end_time = Column(DateTime(timezone=True), nullable=False)
    agent_name = Column(String(255), nullable=True)

    notes = Column(Text, nullable=True)
    call_duration_seconds = Column(Integer, nullable=True)
    sale_carrier = Column(String(120), nullable=True)
    sale_product = Column(String(120), nullable=True)
    premium_amount = Column(Numeric(12, 2), nullable=True)
    policy_number = Column(String(120), nullable=True)
    extra = Column(JSONB, default={})

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    appointment = relationship("Appointment", back_populates="disposition_record")
    lead = relationship("Lead")
    agent = relationship("Agent")
    submitted_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("appointment_id", name="uq_appointment_dispositions_appointment_id"),
        Index("idx_appt_dispositions_tenant_created", "tenant_id", "created_at"),
        Index("idx_appt_dispositions_agent_created", "tenant_id", "agent_id", "created_at"),
        Index("idx_appt_dispositions_key", "tenant_id", "disposition_key"),
        Index("idx_appt_dispositions_slot", "tenant_id", "appointment_start_time"),
    )
