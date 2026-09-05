import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class AgentStateLicense(Base):
    __tablename__ = "agent_state_licenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    state_code = Column(String(2), nullable=False)
    license_number = Column(String(100), nullable=False)
    effective_date = Column(Date, nullable=False)
    expiration_date = Column(Date, nullable=True)
    status = Column(String(50), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent = relationship("Agent")

    __table_args__ = (
        Index("idx_agent_state_licenses_agent_state", "tenant_id", "agent_id", "state_code"),
        Index("idx_agent_state_licenses_expiration", "tenant_id", "expiration_date"),
    )


class AgentCarrierAppointment(Base):
    __tablename__ = "agent_carrier_appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    carrier_name = Column(String(120), nullable=False)
    carrier_key = Column(String(120), nullable=False)
    state_code = Column(String(2), nullable=False)
    appointment_number = Column(String(100), nullable=True)
    effective_date = Column(Date, nullable=False)
    expiration_date = Column(Date, nullable=True)
    status = Column(String(50), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent = relationship("Agent")

    __table_args__ = (
        Index("idx_agent_carrier_appts_agent_carrier_state", "tenant_id", "agent_id", "carrier_key", "state_code"),
        Index("idx_agent_carrier_appts_expiration", "tenant_id", "expiration_date"),
        Index("idx_agent_carrier_appts_status", "tenant_id", "status"),
    )


class ComplianceEvent(Base):
    __tablename__ = "compliance_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)
    appointment_id = Column(UUID(as_uuid=True), ForeignKey("agent_carrier_appointments.id"), nullable=True)
    deal_id = Column(UUID(as_uuid=True), ForeignKey("deals.id"), nullable=True)
    event_type = Column(String(80), nullable=False)
    carrier = Column(String(120), nullable=True)
    state = Column(String(2), nullable=True)
    message = Column(Text, nullable=False)
    severity = Column(String(30), nullable=False, default="info")
    resolved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = relationship("Agent")

    __table_args__ = (
        Index("idx_compliance_events_tenant_created", "tenant_id", "created_at"),
        Index("idx_compliance_events_type", "tenant_id", "event_type"),
        Index("idx_compliance_events_resolved", "tenant_id", "resolved"),
    )


class Deal(Base):
    __tablename__ = "deals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    customer_dob = Column(String(20), nullable=True)   # per-person date of birth
    customer_email = Column(String(255), nullable=True)
    customer_address = Column(String(255), nullable=True)
    customer_city = Column(String(120), nullable=True)
    customer_zip = Column(String(20), nullable=True)
    customer_gender = Column(String(20), nullable=True)
    customer_marital_status = Column(String(30), nullable=True)
    customer_tobacco = Column(String(20), nullable=True)   # Non-smoker | Smoker
    customer_income = Column(String(50), nullable=True)    # annual household income (freeform)
    customer_ssn = Column(String(20), nullable=True)       # "Social" (SSN) — sensitive PII
    carrier = Column(String(120), nullable=False)
    carrier_key = Column(String(120), nullable=False)
    state = Column(String(2), nullable=False)
    plan_type = Column(String(120), nullable=True)
    premium = Column(Numeric(12, 2), nullable=True)
    # Policy breakdown for one enrollment (ACA master + ancillary dental/vision).
    # Total deals = aca_count + dental_count + vision_count. Defaults model a
    # plain single ACA policy (1/0/0) so existing behavior is unchanged.
    aca_count = Column(Integer, nullable=False, default=1, server_default="1")
    dental_count = Column(Integer, nullable=False, default=0, server_default="0")
    vision_count = Column(Integer, nullable=False, default=0, server_default="0")
    # Full per-product plan detail for THIS person's deal: a list of
    # {product, carrier, tier, plan_name, premium, effective_date, decision}.
    # The aca/dental/vision counts above stay as the 0/1 flags the dashboards sum.
    products = Column(JSONB, nullable=True)
    # Call recording attached on the Add Deal form (compliance gate). Nullable so
    # every existing deal + any non-form writer keeps working unchanged. One
    # recording can back several people's deals in the same enrollment.
    recording_id = Column(UUID(as_uuid=True), ForeignKey("deal_recordings.id"), nullable=True, index=True)
    # Up to 4 call recordings (list of UUID strings). recording_id above stays the
    # PRIMARY (first) for back-compat; this holds the full set for this submission.
    recording_ids = Column(JSONB, nullable=True)
    # Signed consent / scope-of-appointment paperwork for this enrollment (list of
    # deal_recordings UUID strings with kind='consent'). Optional: the call recording
    # is the hard gate on the Add Deal form, consent forms are attached alongside it.
    consent_form_ids = Column(JSONB, nullable=True)
    status = Column(String(50), nullable=False, default="submitted")
    approval_decision = Column(String(50), nullable=True)
    approval_reason = Column(Text, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent = relationship("Agent")
    lead = relationship("Lead")

    __table_args__ = (
        Index("idx_deals_tenant_created", "tenant_id", "created_at"),
        Index("idx_deals_agent_created", "tenant_id", "agent_id", "created_at"),
        Index("idx_deals_approval", "tenant_id", "approval_decision"),
    )


class DealApprovalLog(Base):
    __tablename__ = "deal_approval_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    deal_id = Column(UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    carrier = Column(String(120), nullable=False)
    state = Column(String(2), nullable=False)
    decision = Column(String(50), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    deal = relationship("Deal")
    agent = relationship("Agent")

    __table_args__ = (
        Index("idx_deal_approval_logs_tenant_created", "tenant_id", "created_at"),
        Index("idx_deal_approval_logs_decision", "tenant_id", "decision"),
    )


class DealRecording(Base):
    """Audio recording of a sales call, uploaded on the Add Deal form BEFORE the
    deal can be logged (compliance gate). Stored in S3 when configured, otherwise
    inline in the DB so the upload always works on any deploy. One recording can
    back several deals (one per person in a household enrollment) via
    deals.recording_id."""

    __tablename__ = "deal_recordings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)
    filename = Column(String(255), nullable=True)
    content_type = Column(String(100), nullable=True)
    # 'recording' -> audio/video of the sales call; 'consent' -> a signed consent form
    # (PDF/image/doc) uploaded on the same Add Deal form. Same storage, different gate.
    kind = Column(String(20), nullable=False, default="recording", server_default="recording")
    byte_size = Column(Integer, nullable=False, default=0, server_default="0")
    # 'db' -> bytes live in `data`; 's3' -> object at s3_bucket/s3_key.
    storage = Column(String(10), nullable=False, default="db", server_default="db")
    data = Column(LargeBinary, nullable=True)
    s3_bucket = Column(String(255), nullable=True)
    s3_key = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = relationship("Agent")

    __table_args__ = (
        Index("idx_deal_recordings_tenant_created", "tenant_id", "created_at"),
    )
