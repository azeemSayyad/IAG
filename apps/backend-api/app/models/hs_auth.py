"""HealthSherpa 2027 client authorizations, run by the IAG Chrome extension.

An admin or agent uploads a sheet of clients (first/last name, DOB, state,
optional phone/email). Each row is one `hs_auth_requests` row. An agent presses
Start in the portal; the extension then drives HealthSherpa in the agent's own
logged-in Chrome tab (search -> Plan Year 2027 -> Add to clients -> Text link ->
wait -> Finish) and reports every step back with a one-job token.

Nothing here talks to HealthSherpa — the portal only stores the list, enforces
the daily limit and keeps the status the extension reports.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


class HsAuthBatch(Base):
    """One uploaded sheet."""

    __tablename__ = "hs_auth_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    total_rows = Column(Integer, nullable=False, default=0, server_default="0")
    invalid_rows = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class HsAuthRequest(Base):
    """One client to authorize."""

    __tablename__ = "hs_auth_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("hs_auth_batches.id"), nullable=True, index=True)
    row_number = Column(Integer, nullable=True)  # line in the uploaded sheet

    first_name = Column(String(80), nullable=False, default="", server_default="")
    last_name = Column(String(80), nullable=False, default="", server_default="")
    dob = Column(Date, nullable=True)
    state = Column(String(2), nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(200), nullable=True)

    # Who works it. NULL = any agent may take it.
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    # PENDING | INVALID | IN_PROGRESS | LINK_SENT | AUTHORIZED | NOT_FOUND |
    # NEEDS_REVIEW | EXPIRED | FAILED | CANCELLED   (see app/hs_auth/service.py)
    status = Column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    status_detail = Column(Text, nullable=True)
    step = Column(String(40), nullable=True)  # last step the extension reported

    hs_app_id = Column(String(40), nullable=True)  # Marketplace application ID of the 2027 row
    link_sends = Column(Integer, nullable=False, default=0, server_default="0")
    runs = Column(Integer, nullable=False, default=0, server_default="0")

    started_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    link_sent_at = Column(DateTime(timezone=True), nullable=True)
    authorized_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    last_report_at = Column(DateTime(timezone=True), nullable=True)

    # sha256 of the current run's job token (the extension's only credential).
    job_token_hash = Column(String(64), nullable=True)

    # [{at, step, status, detail}] — newest last, capped in the service.
    events = Column(JSONB, nullable=True)

    # ---- HealthSherpa TEXT CAMPAIGN rows only (NULL campaign_id = a plain
    # uploaded row worked with Start). See app/hs_auth/campaigns.py.
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("hs_text_campaigns.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    # The text conversation: QUEUED | INVALID | SKIPPED | BLAST_SENT | BLAST_FAILED |
    # REPLIED | FOLLOWUP_SENT | NO_ANSWER | ANSWERED | DNC | CANCELLED
    convo = Column(String(16), nullable=True)
    convo_detail = Column(Text, nullable=True)
    yes_text = Column(Text, nullable=True)            # their first (positive) reply
    yes_at = Column(DateTime(timezone=True), nullable=True)
    followup_sent_at = Column(DateTime(timezone=True), nullable=True)
    renewing_company = Column(Text, nullable=True)    # their answer to the follow-up
    answered_at = Column(DateTime(timezone=True), nullable=True)
    reminder_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_text_at = Column(DateTime(timezone=True), nullable=True)   # newest text either way
    # The client wrote something the automation can't act on (a question, or a
    # text after they were queued) — a person should answer. Cleared by sending
    # a manual text or "Mark as handled".
    needs_reply = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_hs_auth_requests_tenant_status", "tenant_id", "status"),
        Index("ix_hs_auth_requests_started", "tenant_id", "started_by", "started_at"),
    )


class HsAuthRun(Base):
    """One press of Start — what the daily limit counts.

    Kept separate from the request row so a retried client counts again, and so
    deleting a row never gives back a used slot.
    """

    __tablename__ = "hs_auth_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    request_id = Column(UUID(as_uuid=True), ForeignKey("hs_auth_requests.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_hs_auth_runs_user_day", "tenant_id", "user_id", "created_at"),)


class HsTextCampaign(Base):
    """A HealthSherpa text campaign: one CSV of clients, one blast message.

    Deliberately NOT the Auto Dialer's text_campaigns: replies to it never
    reach the dialer — a positive reply gets the approved follow-up text and,
    once answered, a HealthSherpa job for `hs_agent_id` (app/hs_auth/campaigns.py).
    """

    __tablename__ = "hs_text_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    template = Column(Text, nullable=False)          # the blast; {name} = first name
    # DRAFT -> RUNNING <-> PAUSED -> COMPLETED (blast done) | STOPPED
    status = Column(String(12), nullable=False, default="DRAFT", server_default="DRAFT")
    pause_reason = Column(Text, nullable=True)
    file_name = Column(String(255), nullable=True)
    # The agent whose HealthSherpa account authorizes these clients (Stan).
    hs_agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("hs_auth_batches.id"), nullable=True)

    rows_in_file = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_invalid = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_no_phone = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_duplicate = Column(Integer, nullable=False, default=0, server_default="0")
    skipped_dnc = Column(Integer, nullable=False, default=0, server_default="0")

    # Timers / settings (edited from the campaign drawer).
    answer_wait_minutes = Column(Integer, nullable=False, default=10, server_default="10")
    reminder_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    reminder_text = Column(Text, nullable=True)
    reminder_minutes = Column(Integer, nullable=False, default=7, server_default="7")
    expire_minutes = Column(Integer, nullable=False, default=15, server_default="15")
    resend_on_expiry = Column(Boolean, nullable=False, default=False, server_default="false")

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class HsCampaignText(Base):
    """Every text of a HealthSherpa campaign, both ways — the client's history."""

    __tablename__ = "hs_campaign_texts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("hs_text_campaigns.id", ondelete="CASCADE"), nullable=False)
    request_id = Column(UUID(as_uuid=True), ForeignKey("hs_auth_requests.id", ondelete="CASCADE"), nullable=False)
    phone_number = Column(String(32), nullable=False)        # +1XXXXXXXXXX (the client)
    direction = Column(String(3), nullable=False)            # OUT | IN
    # OUT: BLAST | FOLLOWUP | REMINDER | MANUAL (typed by a person);  IN: REPLY
    kind = Column(String(10), nullable=False)
    sent_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # MANUAL: who typed it
    body = Column(Text, nullable=True)
    from_number = Column(String(32), nullable=True)          # our number (OUT)
    # OUT: QUEUED -> SENDING -> SENT -> DELIVERED | FAILED; SKIPPED; CANCELLED.  IN: RECEIVED
    status = Column(String(10), nullable=False, default="QUEUED")
    reply_kind = Column(String(12), nullable=True)           # IN: replies.classify()
    provider_message_id = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_hs_campaign_texts_request", "request_id", "created_at"),
        Index("ix_hs_campaign_texts_phone", "tenant_id", "phone_number", "sent_at"),
        Index("ix_hs_campaign_texts_queue", "tenant_id", "status"),
        Index("ix_hs_campaign_texts_message", "provider_message_id"),
    )
