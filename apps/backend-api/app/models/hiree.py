import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Date, DateTime, Boolean, Text, ForeignKey, Index, Integer, LargeBinary
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


class HireeOnboarding(Base):
    """A self-onboarding application submitted by a prospective agent ("hiree").

    The public onboarding form (hosted separately) POSTs here. The row stays
    `pending` until an admin reviews it. On approval we create the real
    User(role="agent") + Agent records; this row is kept as the historical
    record (with `created_agent_user_id` linking to the issued login).
    """

    __tablename__ = "hiree_onboarding"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="new")  # new | pending | approved | rejected

    # --- Step 1: Account ---
    full_legal_name = Column(String(200), nullable=False)  # composed "First Middle Last" (kept for list/back-compat)
    first_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    email = Column(String(255), nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)  # set on the form; reused to create the login on approval

    # --- Step 2: Personal details ---
    date_of_birth = Column(Date, nullable=True)
    ssn = Column(String(20), nullable=True)
    phone = Column(String(50), nullable=True)
    gender = Column(String(30), nullable=True)
    marital_status = Column(String(30), nullable=True)
    drivers_license_number = Column(String(60), nullable=True)
    street_address = Column(String(255), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(50), nullable=True)
    zip = Column(String(20), nullable=True)

    # --- Step 3: Verify identity (S3 keys) ---
    # id_front_key/id_back_key mirror the driver's-license doc for back-compat.
    id_front_key = Column(String(500), nullable=True)
    id_back_key = Column(String(500), nullable=True)
    # Full list of identity documents the applicant uploaded. Each:
    # {type, front_key, back_key, id_number, issuing_state, issue_date, expiration_date}
    identity_documents = Column(JSONB, nullable=True, default=list)
    # --- FFM certificate (own step; S3 key) ---
    ffm_key = Column(String(500), nullable=True)

    # --- Step 4: Sign agreement (download blank, sign offline, upload signed copy) ---
    agreement_signed = Column(Boolean, nullable=False, default=False)
    agreement_key = Column(String(500), nullable=True)  # S3/DB key of the uploaded signed agreement

    # --- Step 10: W-9 tax form (download blank, sign offline, upload signed copy) ---
    w9_signed = Column(Boolean, nullable=False, default=False)
    w9_key = Column(String(500), nullable=True)  # S3/DB key of the uploaded signed W-9

    # --- Onboarding document (admin-uploaded, post-hoc) ---
    # A standalone signed onboarding document an admin can attach for agents who
    # were hired but never went through the self-onboarding flow (migration aid).
    onboarding_doc_key = Column(String(500), nullable=True)  # S3/DB key of the uploaded onboarding document

    # --- Step 5: Agency releases ---
    needs_release = Column(Boolean, nullable=False, default=False)
    releases = Column(JSONB, nullable=True, default=list)        # [{carrier, status, doc_key}]

    # --- Step 6: Carrier portals ---
    has_carrier_logins = Column(Boolean, nullable=False, default=False)
    carrier_logins = Column(JSONB, nullable=True, default=list)  # [{carrier, username, password}]

    # --- Step 7: States licensed in ---
    # National Producer Number (maps to agents.national_producer_number on approve)
    npn = Column(String(50), nullable=True)
    licensed_states = Column(JSONB, nullable=True, default=list)  # [{state_code, license_number}]

    # --- Step 9: Banking & emergency contact ---
    # {bank_name, routing_number, account_number, account_type, branch_location}
    bank_info = Column(JSONB, nullable=True, default=dict)
    # {contact_name, relationship, phone, email, street_address, city, state}
    emergency_contact = Column(JSONB, nullable=True, default=dict)

    # --- Review meta ---
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    pending_reason = Column(Text, nullable=True)  # why the admin is holding this in 'pending'
    created_agent_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_hiree_onboarding_tenant_status", "tenant_id", "status"),
    )


class OnboardingDocument(Base):
    """A file uploaded on the public onboarding form (ID photos, FFM cert, signed
    releases). Stored in S3 when configured, otherwise inline in the DB so the
    upload + admin viewing always work on any deploy. Keyed by the synthetic
    `key` returned to the form and later referenced on the HireeOnboarding row
    (id_front_key / id_back_key / ffm_key / releases[].doc_key)."""

    __tablename__ = "onboarding_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(600), nullable=False, unique=True, index=True)
    filename = Column(String(255), nullable=True)
    content_type = Column(String(120), nullable=True)
    byte_size = Column(Integer, nullable=False, default=0, server_default="0")
    # 'db' -> bytes live in `data`; 's3' -> object at s3_bucket/s3_key.
    storage = Column(String(10), nullable=False, default="db", server_default="db")
    data = Column(LargeBinary, nullable=True)
    s3_bucket = Column(String(255), nullable=True)
    s3_key = Column(String(600), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
