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
    Float,
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

    # How this lead entered the pool:
    #   REPLY      — the customer texted back to a first-template blast (the
    #                original Sinch flow, mirrored in by lead_ingest / inbound_sync)
    #   CSV_DIRECT — an admin uploaded a list straight to the agent pool; NO SMS
    #                was ever sent. Served after REPLY leads (see
    #                queue_service._next_queued_lead) and worked by phone.
    source = Column(String(20), nullable=False, default="REPLY", server_default="REPLY")
    # The upload batch a CSV_DIRECT lead came from (so a wrong file can be
    # pulled back out of the pool in one action). NULL for REPLY leads.
    batch_id = Column(UUID(as_uuid=True), ForeignKey("sms_pool_batches.id"), nullable=True)
    # Everything else the CSV row carried, shown to the agent on the lead card:
    #   {"fields": [[label, value], ...],   # column order preserved (JSONB reorders object keys)
    #    "address": "…"}                     # composed display address
    details = Column(JSONB, nullable=True)

    # HOT | WARM | NORMAL
    priority = Column(String(20), nullable=False, default="NORMAL")
    # QUEUED | ASSIGNED | IN_PROGRESS | DISPOSITIONED | PARKED | BLOCKED
    #   + the YES-reply auto dialer (app/voice_dialer):
    # READY_TO_CALL — replied YES, waiting for the dialer (NOT in the pool)
    # DIALING       — a call to them is in flight; locked to that one call
    status = Column(String(30), nullable=False, default="QUEUED")

    # --- YES-reply auto dialer (migration 062) ---
    yes_reply = Column(Text, nullable=True)                    # the reply that made them Ready to call
    yes_at = Column(DateTime(timezone=True), nullable=True)    # set => an auto-dial lead
    dial_from = Column(String(32), nullable=True)              # our number they texted (caller ID)
    next_dial_at = Column(DateTime(timezone=True), nullable=True)
    dial_attempts = Column(Integer, nullable=False, default=0, server_default="0")

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
        Index("idx_sms_leads_tenant_source", "tenant_id", "source"),
        Index("idx_sms_leads_batch", "batch_id"),
    )


class SmsPoolBatch(Base):
    """One admin CSV upload that went STRAIGHT into the agent pool (no SMS).

    Groups its sms_leads (source=CSV_DIRECT, batch_id=this) so the upload shows
    as one card on the Upload Leads page and can be removed as a unit. The
    summary counters are frozen at upload time; live pool/worked counts are
    computed from sms_leads."""

    __tablename__ = "sms_pool_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)               # the uploaded filename
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    total_rows = Column(Integer, nullable=False, default=0)
    imported = Column(Integer, nullable=False, default=0)
    skipped_duplicates = Column(Integer, nullable=False, default=0)  # in-file + already in pool
    skipped_dnc = Column(Integer, nullable=False, default=0)         # on the Do-Not-Call list
    failed = Column(Integer, nullable=False, default=0)              # no usable name/phone
    # The extra (non-core) column labels the file carried, in order — what the
    # agent card will show for every lead in this batch.
    columns = Column(JSONB, default=[])

    # Serving order set on the SMS Manager (drag to reorder): lower = served first.
    # Strictly one list after another; replies still come before every list.
    priority = Column(Integer, nullable=True)
    # Paused lists stay intact but their leads are out of the pool until resumed.
    paused_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sms_pool_batches_tenant_created", "tenant_id", "created_at"),
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

    # Auto dialer: the agent's open headset line (Vonage in-app leg). Set while
    # their browser holds the line; a live customer is transferred straight onto it.
    voice_leg_uuid = Column(String(64), nullable=True)
    voice_line_at = Column(DateTime(timezone=True), nullable=True)
    # "Ready for calls" — the agent's own switch in Lead Manager, separate from
    # Join queue. Off = regular pool only, no line open (no Vonage minutes).
    voice_ready = Column(Boolean, nullable=False, default=False, server_default="false")

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


class VoiceCallAttempt(Base):
    """One auto-dialer call to a YES-responder (or one inbound callback).

    The audit trail behind every dialing rule: the 3-per-24h cap and the
    abandoned-call rate are both counted from here."""

    __tablename__ = "voice_call_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    sms_lead_id = Column(UUID(as_uuid=True), ForeignKey("sms_leads.id", ondelete="SET NULL"), nullable=True)
    phone_number = Column(String(32), nullable=False)       # +1XXXXXXXXXX
    from_number = Column(String(32), nullable=True)         # caller ID used
    direction = Column(String(10), nullable=False, default="outbound")  # outbound | inbound
    call_uuid = Column(String(64), nullable=True)            # Vonage call leg uuid

    # DIALING -> RINGING -> ANSWERED -> CONNECTED -> COMPLETED
    # or a final miss: MACHINE | NO_ANSWER | BUSY | FAILED | ABANDONED | CANCELLED
    status = Column(String(20), nullable=False, default="DIALING")
    agent_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    recording_id = Column(UUID(as_uuid=True), nullable=True)   # deal_recordings.id
    # The result the agent set for this call with Mark… (SALE, APPOINTMENT_SET,
    # NOT_INTERESTED, VOICEMAIL, …) — what the manager sees as the call's outcome.
    disposition = Column(String(40), nullable=True)
    error = Column(Text, nullable=True)
    # Inbound caller on hold (status HOLDING) waiting for their own agent;
    # NULL = waiting for whoever is free first.
    hold_for_user_id = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    human_at = Column(DateTime(timezone=True), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_voice_attempts_tenant_created", "tenant_id", "created_at"),
        Index("idx_voice_attempts_phone", "tenant_id", "phone_number", "created_at"),
        Index("idx_voice_attempts_call_uuid", "call_uuid"),
        Index("idx_voice_attempts_lead", "sms_lead_id"),
    )


class ManualCall(Base):
    """One call placed by hand from the Dial drawer (any user, any number).

    Its own log on purpose: the auto dialer's rules, stats and Called list
    never see these. While one is in flight its user is busy — no pool offer
    and no dialer bridge reaches them (queue_service._available_agents)."""

    __tablename__ = "manual_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    phone_number = Column(String(32), nullable=False)       # who was called
    from_number = Column(String(32), nullable=True)         # caller ID used
    app_leg_uuid = Column(String(64), nullable=True)         # the caller's browser leg
    callee_leg_uuid = Column(String(64), nullable=True)      # the phone leg

    # DIALING -> RINGING -> ANSWERED -> COMPLETED
    # or a final miss: NO_ANSWER | BUSY | FAILED | CANCELLED
    status = Column(String(20), nullable=False, default="DIALING")
    recording_id = Column(UUID(as_uuid=True), nullable=True)   # deal_recordings.id
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_manual_calls_tenant_created", "tenant_id", "created_at"),
        Index("idx_manual_calls_user_created", "user_id", "created_at"),
        Index("idx_manual_calls_app_leg", "app_leg_uuid"),
    )


class VoiceDialerSettings(Base):
    """Per-tenant auto-dialer switch + dialing rules (edited on SMS Manager)."""

    __tablename__ = "voice_dialer_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=False)
    pacing_ratio = Column(Float, nullable=False, default=1.0)       # UNUSED: pacing is fixed at 1 call per free agent
    ring_seconds = Column(Integer, nullable=False, default=25)       # 15 – 45
    max_attempts_24h = Column(Integer, nullable=False, default=3)
    max_total_attempts = Column(Integer, nullable=False, default=6)  # then -> regular pool
    retry_minutes = Column(Integer, nullable=False, default=30)
    reply_delay_seconds = Column(Integer, nullable=False, default=3)  # wait after a reply before calling (0 – 30)
    window_start_hour = Column(Integer, nullable=False, default=8)   # client's local time
    window_end_hour = Column(Integer, nullable=False, default=20)
    business_name = Column(String(120), nullable=True)
    callback_number = Column(String(32), nullable=True)
    # Inbound calls: longest hold before "we'll call you back", and open hours
    # (outside them a caller hears the closed message). Off = always open.
    max_hold_seconds = Column(Integer, nullable=False, default=300)  # 60 – 600
    hours_enabled = Column(Boolean, nullable=False, default=False)
    open_days = Column(String(20), nullable=False, default="0,1,2,3,4")  # Mon=0 … Sun=6
    open_start_minute = Column(Integer, nullable=False, default=540)      # 9:00
    open_end_minute = Column(Integer, nullable=False, default=1080)       # 18:00
    hours_tz = Column(String(40), nullable=False, default="America/New_York")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class VoiceClientOwner(Base):
    """The agent a client's callbacks go to, by phone number: the agent who
    most recently called or talked to them (source "call"), or an admin's
    pick (source "admin"). Managers never own a client."""

    __tablename__ = "voice_client_owners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    phone_number = Column(String(32), nullable=False)   # +1XXXXXXXXXX
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    source = Column(String(20), nullable=False, default="call")   # call | admin
    set_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_voice_client_owner_phone"),
        Index("idx_voice_client_owners_user", "tenant_id", "user_id"),
    )

