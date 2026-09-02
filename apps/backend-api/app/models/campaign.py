import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="active")
        # enum: active, paused, completed, draft

    # AI Configuration
    tone = Column(String(50), nullable=False, default="friendly")
        # enum: friendly, professional, casual, urgent
    prompt_template = Column(Text, nullable=True)
    objection_prompts = Column(JSONB, default={})
        # {"pricing": "prompt text", "trust": "prompt text", ...}

    # Retry Configuration
    max_retries = Column(Integer, nullable=False, default=3)
    retry_delay_hours = Column(Integer, nullable=False, default=24)
    retry_tones = Column(JSONB, default=["friendly", "professional", "urgent"])

    # Booking Configuration
    booking_enabled = Column(Boolean, default=True)
    slot_duration_minutes = Column(Integer, default=15)
    max_days_ahead = Column(Integer, default=3)
    business_hours_start = Column(Integer, default=10)  # 10 AM
    business_hours_end = Column(Integer, default=21)    # 9 PM

    # Targeting
    target_sources = Column(JSONB, default=[])
        # ["facebook", "google", "referral", ...]
    target_states = Column(JSONB, default=[])
        # ["FL", "TX", "CA", ...]
    min_lead_score = Column(Integer, default=0)
    max_lead_score = Column(Integer, default=100)

    # Statistics
    total_leads = Column(Integer, default=0)
    total_contacted = Column(Integer, default=0)
    total_replied = Column(Integer, default=0)
    total_booked = Column(Integer, default=0)
    total_completed = Column(Integer, default=0)
    total_won = Column(Integer, default=0)

    # Send-batch control (CSV-upload campaigns on the Upload Leads page).
    # send_state drives the per-campaign Run/Pause/Resume/Stop; only a "running"
    # campaign releases + sends its leads (one running at a time per tenant).
    send_state = Column(String(20), nullable=False, default="ready")
        # enum: ready, running, paused, stopped
    drip_leads = Column(Integer, nullable=False, default=50)     # per-campaign drip: N leads
    drip_minutes = Column(Integer, nullable=False, default=10)   #   ... every M minutes
    # Per-campaign first-template body. When set, the worker sends THIS (with
    # {first_name} rendered) for this campaign's leads instead of the global default.
    first_template = Column(Text, nullable=True)
    # Which lead-SMS provider this campaign sends through: "sinch" (default = the
    # original pipeline) or "engage2" (the independent Engage Cloud pipeline). Drives
    # which account/numbers the drip uses for THIS campaign's first-templates.
    provider = Column(String(20), nullable=False, server_default="sinch", default="sinch")

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)
