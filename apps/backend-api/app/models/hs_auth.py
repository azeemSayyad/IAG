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

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, String, Text
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
