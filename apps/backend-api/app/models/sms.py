"""SMS subsystem models (Queue / Manager / Monitoring).

Tenant-scoped, isolated from the existing conversation `messages` table so the
SMS feature can evolve independently. Mirrors the proven Gamified Call Center
schema, adapted to this codebase's conventions (UUID PKs, tenant_id FK,
tz-aware timestamps, soft-delete where useful).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SmsLead(Base):
    """A customer in the SMS queue lifecycle."""

    __tablename__ = "sms_leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True)

    phone_number = Column(String(50), nullable=False)
    customer_name = Column(String(255), nullable=True)
    last_message = Column(Text, nullable=True)

    # HOT | WARM | NORMAL
    priority = Column(String(20), nullable=False, default="NORMAL")
    # QUEUED | ASSIGNED | IN_PROGRESS | DISPOSITIONED | PARKED | BLOCKED
    status = Column(String(30), nullable=False, default="QUEUED")

    assigned_agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    disposition = Column(String(40), nullable=True)
    callback_time = Column(DateTime(timezone=True), nullable=True)

    response_time_ms = Column(Integer, nullable=True)
    message_count = Column(Integer, nullable=False, default=0)
    pass_count = Column(Integer, nullable=False, default=0)

    accepted_at = Column(DateTime(timezone=True), nullable=True)
    dispositioned_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index("idx_sms_leads_tenant_status", "tenant_id", "status"),
        Index("idx_sms_leads_tenant_agent", "tenant_id", "assigned_agent_id"),
        Index("idx_sms_leads_created", "tenant_id", "created_at"),
    )


class SmsQueueAgent(Base):
    """An agent's live state within the SMS queue."""

    __tablename__ = "sms_queue_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # AVAILABLE | ON_CALL | AWAY | OFFLINE | NOT_WORKING
    status = Column(String(20), nullable=False, default="OFFLINE")
    queue_position = Column(Integer, nullable=True)
    current_lead_id = Column(UUID(as_uuid=True), nullable=True)
    consecutive_misses = Column(Integer, nullable=False, default=0)

    total_leads_handled = Column(Integer, nullable=False, default=0)
    total_appointments_set = Column(Integer, nullable=False, default=0)
    avg_response_time_ms = Column(Integer, nullable=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_sms_queue_agents_tenant_user"),
        Index("idx_sms_queue_agents_tenant_status", "tenant_id", "status"),
    )


class SmsMessage(Base):
    """SMS message audit trail (separate from conversation messages)."""

    __tablename__ = "sms_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    sms_lead_id = Column(UUID(as_uuid=True), ForeignKey("sms_leads.id"), nullable=True)

    phone_number = Column(String(50), nullable=False)
    # INBOUND | OUTBOUND
    direction = Column(String(10), nullable=False)
    body = Column(Text, nullable=False)
    # CUSTOMER | AGENT | SYSTEM
    sender_type = Column(String(20), nullable=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # PENDING | SENT | DELIVERED | FAILED | RECEIVED
    status = Column(String(20), nullable=False, default="PENDING")
    provider = Column(String(50), nullable=True)
    provider_message_id = Column(String(255), nullable=True, index=True)
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("idx_sms_messages_lead_created", "sms_lead_id", "created_at"),
        Index("idx_sms_messages_tenant_dir_status", "tenant_id", "direction", "status"),
        Index("idx_sms_messages_tenant_created", "tenant_id", "created_at"),
    )


class SmsSettings(Base):
    """Per-tenant SMS settings (e.g. the manager polling toggle)."""

    __tablename__ = "sms_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True)
    polling_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class SmsPollLog(Base):
    """One row per inbound-polling attempt; powers monitoring health stats."""

    __tablename__ = "sms_poll_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)

    attempted_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    succeeded = Column(Boolean, nullable=False, default=False)
    messages_pulled = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    details = Column(JSONB, default={})

    __table_args__ = (
        Index("idx_sms_poll_log_attempted", "attempted_at"),
        Index("idx_sms_poll_log_tenant_attempted", "tenant_id", "attempted_at"),
    )


class SmsAgentBreak(Base):
    """A break an agent took, with reason + start/end so we can report duration."""

    __tablename__ = "sms_agent_breaks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # Lunch | Bathroom | Meeting | Personal | Other
    reason = Column(String(40), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sms_agent_breaks_tenant_user", "tenant_id", "user_id", "started_at"),
        Index("idx_sms_agent_breaks_open", "user_id", "ended_at"),
    )


class SmsAgentAction(Base):
    """A timestamped agent decision on an offered lead.

    Powers the per-agent "passed vs kept" tally, sortable by day/week/month.
    KEEP is logged when an agent accepts an offered lead; PASS when they pass it.
    """

    __tablename__ = "sms_agent_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sms_lead_id = Column(UUID(as_uuid=True), ForeignKey("sms_leads.id"), nullable=True)
    # PASS | KEEP
    action = Column(String(10), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("idx_sms_agent_actions_tenant_user", "tenant_id", "user_id", "created_at"),
        Index("idx_sms_agent_actions_tenant_action", "tenant_id", "action", "created_at"),
    )


class SmsDoNotCall(Base):
    """Permanent Do-Not-Call suppression list. A number lands here when a lead is
    dispositioned Unqualified / Wrong Number / Not Interested (the "Parked —
    Unqualified" panel). The SMS queue must NEVER re-ingest a number in this
    table — even if the originating SmsLead row is later deleted."""

    __tablename__ = "sms_do_not_call"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    phone_number = Column(String(32), nullable=False)   # digits-only, normalized
    reason = Column(String(40), nullable=True)           # the disposition that parked it
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_sms_dnc_tenant_phone"),
        Index("idx_sms_dnc_tenant_phone", "tenant_id", "phone_number"),
    )
