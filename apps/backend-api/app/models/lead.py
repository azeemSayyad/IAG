import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Lead(Base):
    """
    Production-grade Leads table.

    Supports:
    - Multi-tenant isolation via tenant_id
    - Lead lifecycle tracking
    - AI scoring and status
    - Timezone-aware scheduling
    - Soft deletes
    - Custom fields for flexibility
    """
    __tablename__ = "leads"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Tenant isolation
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Lead source tracking
    source = Column(String(255), nullable=False)
        # e.g., csv_import, webhook, api, manual, facebook, google, referral
    source_metadata = Column(JSONB, default={})
        # Stores source-specific data (UTM params, referrer, etc.)

    # Core identity
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False)
    phone_normalized = Column(String(20), nullable=True)
        # E.164 format for deduplication
    email = Column(String(255), nullable=True)
    email_normalized = Column(String(255), nullable=True)
        # Lowercase for deduplication

    # Geographic data
    state = Column(String(50), nullable=True)
    city = Column(String(255), nullable=True)
    zip_code = Column(String(20), nullable=True)
    timezone = Column(String(50), nullable=True, default="America/New_York")

    # AI scoring
    lead_score = Column(Float, default=0.0)
        # 0-100 composite score
    booking_probability = Column(Float, default=0.0)
        # 0-100 predicted booking likelihood
    conversion_probability = Column(Float, default=0.0)
        # 0-100 predicted conversion likelihood

    # Lifecycle
    lifecycle_stage = Column(String(50), nullable=False, default="new")
        # new, contacted, replied, qualified, booked, completed, unqualified, nurture, cold
    ai_status = Column(String(50), nullable=True)
        # active, paused, stopped, escalated
    status = Column(String(50), nullable=False, default="new")
        # Alias for lifecycle_stage for backward compatibility

    # Campaign assignment
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True)

    # Agent ownership (AI auto-distributes new leads; head/admin can reassign).
    # Compliance-aware: a lead is only assigned to an agent licensed for its state.
    assigned_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)

    # Engagement tracking
    contact_count = Column(Integer, default=0)
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    last_replied_at = Column(DateTime(timezone=True), nullable=True)
    first_response_time_seconds = Column(Integer, nullable=True)

    # Tags and custom data
    tags = Column(JSONB, default=[])
    custom_fields = Column(JSONB, default={})
    notes = Column(String, nullable=True)

    # Appointment Capacity Engine (same-day lead pacing). Inert unless
    # SAME_DAY_PACING_ENABLED is on. pacing_status: held | released |
    # awaiting_slot | booked | parked.
    pacing_status = Column(String(30), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    wave_id = Column(String(64), nullable=True)
    priority_score = Column(Float, default=0.0)

    # Consent tracking
    sms_consent = Column(Boolean, default=True)
    email_consent = Column(Boolean, default=True)
    consent_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)

    # Constraints
    __table_args__ = (
        CheckConstraint("lead_score >= 0 AND lead_score <= 100", name="ck_leads_score_range"),
        CheckConstraint("booking_probability >= 0 AND booking_probability <= 100", name="ck_leads_booking_prob_range"),
        CheckConstraint("conversion_probability >= 0 AND conversion_probability <= 100", name="ck_leads_conversion_prob_range"),
        Index("idx_leads_tenant_id", "tenant_id"),
        Index("idx_leads_tenant_status", "tenant_id", "status"),
        Index("idx_leads_phone", "tenant_id", "phone"),
        Index("idx_leads_email", "tenant_id", "email"),
        Index("idx_leads_score", "tenant_id", "lead_score"),
        Index("idx_leads_campaign", "tenant_id", "campaign_id"),
        Index("idx_leads_created", "tenant_id", "created_at"),
        Index("idx_leads_lifecycle", "tenant_id", "lifecycle_stage"),
        Index("idx_leads_active", "tenant_id", "deleted_at"),
        Index("idx_leads_assigned_agent", "tenant_id", "assigned_agent_id"),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="leads")
    campaign = relationship("Campaign", backref="leads")
    conversations = relationship("Conversation", back_populates="lead")
    appointments = relationship("Appointment", back_populates="lead")
