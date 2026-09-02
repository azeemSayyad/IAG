"""Hiree onboarding.

Two audiences:
  * PUBLIC  — the standalone onboarding form (hosted separately) submits an
              application + uploads ID/release documents. No auth.
  * ADMIN   — portal admins (and dev) review applications and approve/reject.
              Approval creates the real User(role="agent") + Agent + licenses.
"""
import io
import re
import uuid
import zipfile
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.calls.s3_storage import s3_storage
from app.core.database import get_db
from app.core.deps import get_current_active_user, require_role
from app.core.security import hash_password
from app.models.agent import Agent
from app.models.compliance import AgentStateLicense
from app.models.hiree import HireeOnboarding, OnboardingDocument
from app.models.tenant import Tenant
from app.models.user import User
from app.notifications.service import notify_onboarding_admins
from app.onboarding.esign_fields import build_template_fields
from app.onboarding.signwell import AGREEMENT, W9, SignWellError, signwell

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Admins who may review hirees. "dev" bypasses require_role automatically.
_admin = require_role("tenant_admin", "super_admin", "admin")

def _applicant_label(h: "HireeOnboarding") -> str:
    return (h.full_legal_name or "").strip() or h.email or "An applicant"


def _notify_onboarding_safe(db: Session, tenant_id, **kwargs) -> None:
    """Fan a hiree notification to admins; never let it break the request
    (e.g. before the notifications table migration is applied)."""
    try:
        notify_onboarding_admins(db, tenant_id, **kwargs)
    except Exception:
        db.rollback()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class LicensedState(BaseModel):
    state_code: str
    license_number: Optional[str] = None


class CarrierLogin(BaseModel):
    carrier: str
    username: Optional[str] = None
    password: Optional[str] = None


class ReleaseDoc(BaseModel):
    carrier: str
    status: Optional[str] = "not_started"  # not_started | uploaded
    doc_key: Optional[str] = None


class IdentityDocument(BaseModel):
    type: str  # drivers_license | state_id | passport
    front_key: Optional[str] = None
    back_key: Optional[str] = None
    id_number: Optional[str] = None
    issuing_state: Optional[str] = None
    issue_date: Optional[str] = None       # mm/dd/yyyy or ISO; stored as-is
    expiration_date: Optional[str] = None


class BankInfo(BaseModel):
    bank_name: Optional[str] = None
    routing_number: Optional[str] = None
    account_number: Optional[str] = None
    account_type: Optional[str] = None
    branch_location: Optional[str] = None


class EmergencyContact(BaseModel):
    contact_name: Optional[str] = None
    relationship: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None


class EsignCreateRequest(BaseModel):
    """Form data collected so far (browser state) used to prefill a SignWell
    document and open embedded signing. Nothing is persisted here."""
    doc_type: str  # "agreement" | "w9"
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None
    ssn: Optional[str] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    drivers_license_number: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    identity_documents: List[IdentityDocument] = Field(default_factory=list)
    bank_info: Optional[BankInfo] = None
    emergency_contact: Optional[EmergencyContact] = None


class EsignFinalizeRequest(BaseModel):
    document_id: str
    doc_type: str  # "agreement" | "w9"


class OnboardingSubmit(BaseModel):
    # Account
    full_legal_name: str
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    password: Optional[str] = None
    # Personal details
    date_of_birth: Optional[date] = None
    ssn: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    drivers_license_number: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    # Identity (S3 keys returned by /onboarding/upload)
    id_front_key: Optional[str] = None
    id_back_key: Optional[str] = None
    identity_documents: List[IdentityDocument] = Field(default_factory=list)
    # FFM certificate (own step; S3 key)
    ffm_key: Optional[str] = None
    # Agreement (signed copy uploaded on step 4)
    agreement_signed: bool = False
    agreement_key: Optional[str] = None
    # W-9 tax form (signed copy uploaded on step 10)
    w9_signed: bool = False
    w9_key: Optional[str] = None
    # Agency releases
    needs_release: bool = False
    releases: List[ReleaseDoc] = Field(default_factory=list)
    # Carrier portals
    has_carrier_logins: bool = False
    carrier_logins: List[CarrierLogin] = Field(default_factory=list)
    # National Producer Number
    npn: Optional[str] = None
    # States licensed in
    licensed_states: List[LicensedState] = Field(default_factory=list)
    # Banking & emergency contact
    bank_info: Optional[BankInfo] = None
    emergency_contact: Optional[EmergencyContact] = None


class HireeUpdate(BaseModel):
    """Admin edit of a submitted application — every field optional; only the
    provided ones are changed. Documents themselves aren't re-uploaded here."""
    full_legal_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    date_of_birth: Optional[date] = None
    ssn: Optional[str] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    drivers_license_number: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    npn: Optional[str] = None
    licensed_states: Optional[List[LicensedState]] = None
    bank_info: Optional[BankInfo] = None
    emergency_contact: Optional[EmergencyContact] = None
    identity_documents: Optional[List[IdentityDocument]] = None
    # Document keys (set after an admin re-uploads an image) + carrier portals/releases.
    id_front_key: Optional[str] = None
    id_back_key: Optional[str] = None
    ffm_key: Optional[str] = None
    agreement_key: Optional[str] = None
    w9_key: Optional[str] = None
    onboarding_doc_key: Optional[str] = None
    agreement_signed: Optional[bool] = None
    w9_signed: Optional[bool] = None
    has_carrier_logins: Optional[bool] = None
    carrier_logins: Optional[List[dict]] = None
    releases: Optional[List[dict]] = None


class ReasonRequest(BaseModel):
    # Reason is required so the applicant can later be told why (and the admin
    # has a record). Blank/whitespace is rejected in the handler.
    reason: str = Field(..., min_length=1)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _default_tenant_id(db: Session) -> uuid.UUID:
    """Single-agency mode: attach public submissions to the first tenant.

    (Per-agency routing is a later step once multi-tenancy lands.)
    """
    tenant = db.query(Tenant).order_by(Tenant.created_at.asc()).first()
    if not tenant:
        raise HTTPException(status_code=500, detail="No tenant configured to receive applications")
    return tenant.id


def _to_summary(h: HireeOnboarding) -> dict:
    return {
        "id": str(h.id),
        "full_legal_name": h.full_legal_name,
        "email": h.email,
        "status": h.status,
        "needs_release": h.needs_release,
        "npn": h.npn,
        "licensed_states": h.licensed_states or [],
        "submitted_at": h.submitted_at.isoformat() if h.submitted_at else None,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def _to_detail(h: HireeOnboarding) -> dict:
    d = _to_summary(h)
    d.update({
        "first_name": h.first_name,
        "middle_name": h.middle_name,
        "last_name": h.last_name,
        "date_of_birth": h.date_of_birth.isoformat() if h.date_of_birth else None,
        "ssn": h.ssn,
        "phone": h.phone,
        "gender": h.gender,
        "marital_status": h.marital_status,
        "drivers_license_number": h.drivers_license_number,
        "street_address": h.street_address,
        "city": h.city,
        "state": h.state,
        "zip": h.zip,
        "id_front_key": h.id_front_key,
        "id_back_key": h.id_back_key,
        "identity_documents": h.identity_documents or [],
        "ffm_key": h.ffm_key,
        "id_front_url": s3_storage.signed_url(h.id_front_key) if h.id_front_key else None,
        "id_back_url": s3_storage.signed_url(h.id_back_key) if h.id_back_key else None,
        "ffm_url": s3_storage.signed_url(h.ffm_key) if h.ffm_key else None,
        "agreement_signed": h.agreement_signed,
        "agreement_key": h.agreement_key,
        "agreement_url": s3_storage.signed_url(h.agreement_key) if h.agreement_key else None,
        "w9_signed": h.w9_signed,
        "w9_key": h.w9_key,
        "w9_url": s3_storage.signed_url(h.w9_key) if h.w9_key else None,
        "onboarding_doc_key": h.onboarding_doc_key,
        "onboarding_doc_url": s3_storage.signed_url(h.onboarding_doc_key) if h.onboarding_doc_key else None,
        "releases": h.releases or [],
        "has_carrier_logins": h.has_carrier_logins,
        "carrier_logins": h.carrier_logins or [],
        "bank_info": h.bank_info or {},
        "emergency_contact": h.emergency_contact or {},
        "rejection_reason": h.rejection_reason,
        "pending_reason": h.pending_reason,
        "created_agent_user_id": str(h.created_agent_user_id) if h.created_agent_user_id else None,
        "reviewed_at": h.reviewed_at.isoformat() if h.reviewed_at else None,
    })
    return d


# --------------------------------------------------------------------------- #
# PUBLIC — the standalone onboarding form
# --------------------------------------------------------------------------- #
@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Public document upload (ID photos, FFM cert, signed releases).

    Persists the bytes — in S3 when configured, otherwise inline in the DB — so
    admins can view the file later regardless of deploy config. Returns the key
    the form stores on the application (id_front_key / ffm_key / doc_key / …).
    """
    data = await file.read()
    safe_name = (file.filename or "upload").replace("/", "_").replace("\\", "_")
    key = f"hiree-onboarding/{uuid.uuid4()}/{safe_name}"
    content_type = file.content_type or "application/octet-stream"
    doc = OnboardingDocument(
        key=key, filename=safe_name, content_type=content_type, byte_size=len(data),
    )
    if s3_storage.configured():
        s3_storage.upload_bytes(data, key, content_type=content_type)
        doc.storage = "s3"
        doc.s3_bucket = s3_storage.bucket
        doc.s3_key = key
    else:
        doc.storage = "db"
        doc.data = data
    db.add(doc)
    db.commit()
    return {"key": key, "stored": True}


@router.get("/admin/document")
def get_document(
    key: str = Query(..., description="Document key returned by /upload"),
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Stream a stored onboarding document for admin viewing. DB-stored bytes are
    returned inline; S3-stored files redirect to a short-lived signed URL."""
    doc = db.query(OnboardingDocument).filter(OnboardingDocument.key == key).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.storage == "s3":
        url = s3_storage.signed_url(doc.s3_key)
        if not url:
            raise HTTPException(status_code=404, detail="Document unavailable")
        return RedirectResponse(url)
    return Response(
        content=doc.data or b"",
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{doc.filename or "document"}"'},
    )


def _esign_doc_type(raw: str) -> str:
    dt = (raw or "").strip().lower()
    if dt not in (AGREEMENT, W9):
        raise HTTPException(status_code=422, detail="doc_type must be 'agreement' or 'w9'")
    return dt


def _store_pdf(db: Session, key: str, data: bytes) -> None:
    """Persist signed-PDF bytes the same way /upload does (S3 or inline DB)."""
    doc = OnboardingDocument(
        key=key, filename=key.rsplit("/", 1)[-1],
        content_type="application/pdf", byte_size=len(data),
    )
    if s3_storage.configured():
        s3_storage.upload_bytes(data, key, content_type="application/pdf")
        doc.storage, doc.s3_bucket, doc.s3_key = "s3", s3_storage.bucket, key
    else:
        doc.storage, doc.data = "db", data
    db.add(doc)
    db.commit()


@router.post("/esign/create", status_code=status.HTTP_201_CREATED)
def esign_create(payload: EsignCreateRequest):
    """Public: create a SignWell document from the agreement/W-9 template,
    prefilled from the form data, and return its embedded signing URL."""
    doc_type = _esign_doc_type(payload.doc_type)
    data = payload.model_dump()
    signer_name = " ".join(
        p for p in (payload.first_name, payload.last_name) if p and p.strip()
    ) or "Agent"
    try:
        fields = build_template_fields(doc_type, data)
        return signwell.create_embedded_document(
            doc_type=doc_type,
            signer_name=signer_name,
            signer_email=(payload.email or "").strip(),
            template_fields=fields,
        )
    except SignWellError as exc:
        raise HTTPException(status_code=502, detail=f"SignWell error: {exc}")


@router.post("/esign/finalize", status_code=status.HTTP_201_CREATED)
def esign_finalize(payload: EsignFinalizeRequest, db: Session = Depends(get_db)):
    """Public: after the agent signs, fetch the completed PDF, counter-stamp the
    agency signature (agreement only), store it, and return the document key
    (the form keeps it in localStorage and includes it in the final submit)."""
    doc_type = _esign_doc_type(payload.doc_type)
    key = f"hiree-onboarding/esign/{payload.document_id}/{doc_type}.pdf"

    # Idempotent: if already finalized, just return the existing key.
    if db.query(OnboardingDocument).filter(OnboardingDocument.key == key).first():
        return {"key": key, "signed": True}

    try:
        pdf = signwell.fetch_completed_pdf(payload.document_id)
    except SignWellError as exc:
        raise HTTPException(status_code=409, detail=f"Document not ready: {exc}")

    # Store the completed PDF EXACTLY as SignWell returns it — no post-finalize
    # edits (would break SignWell's tamper seal). The agency counter-signature is
    # baked into the agreement template itself, so it's already in this PDF.
    _store_pdf(db, key, pdf)
    return {"key": key, "signed": True}


@router.get("/esign/status")
def esign_status(document_id: str = Query(...)):
    """Public: lightweight poll for whether a document is completed (fallback to
    the embedded completion event)."""
    try:
        return {"completed": signwell.is_completed(document_id)}
    except SignWellError as exc:
        raise HTTPException(status_code=502, detail=f"SignWell error: {exc}")


@router.post("/submit", status_code=status.HTTP_201_CREATED)
def submit_application(payload: OnboardingSubmit, db: Session = Depends(get_db)):
    """Public: receive a completed onboarding application -> status 'new'.

    Nothing is stored until this point — the form keeps in-progress state in the
    browser only (localStorage), so unsubmitted applicants are never persisted.
    """
    # Conditional rule: if a release is needed, Oscar + Ambetter logins are required.
    if payload.needs_release:
        provided = {(c.carrier or "").strip().lower() for c in payload.carrier_logins
                    if (c.username or "").strip()}
        missing = [c for c in ("oscar", "ambetter") if c not in provided]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Carrier portal logins required for: {', '.join(m.title() for m in missing)}",
            )

    now = datetime.now(timezone.utc)
    hiree = HireeOnboarding(
        tenant_id=_default_tenant_id(db),
        status="new",
        full_legal_name=payload.full_legal_name.strip(),
        first_name=(payload.first_name.strip() if payload.first_name else None),
        middle_name=(payload.middle_name.strip() if payload.middle_name else None),
        last_name=(payload.last_name.strip() if payload.last_name else None),
        email=str(payload.email).strip().lower(),
        password_hash=hash_password(payload.password) if payload.password else None,
        date_of_birth=payload.date_of_birth,
        ssn=payload.ssn,
        phone=payload.phone,
        gender=payload.gender,
        marital_status=payload.marital_status,
        drivers_license_number=payload.drivers_license_number,
        street_address=payload.street_address,
        city=payload.city,
        state=payload.state,
        zip=payload.zip,
        id_front_key=payload.id_front_key,
        id_back_key=payload.id_back_key,
        identity_documents=[d.model_dump() for d in payload.identity_documents],
        ffm_key=payload.ffm_key,
        agreement_signed=payload.agreement_signed,
        agreement_key=payload.agreement_key,
        w9_signed=payload.w9_signed,
        w9_key=payload.w9_key,
        needs_release=payload.needs_release,
        releases=[r.model_dump() for r in payload.releases],
        has_carrier_logins=payload.has_carrier_logins,
        carrier_logins=[c.model_dump() for c in payload.carrier_logins],
        npn=(payload.npn.strip() if payload.npn else None),
        licensed_states=[s.model_dump() for s in payload.licensed_states],
        bank_info=(payload.bank_info.model_dump() if payload.bank_info else None),
        emergency_contact=(payload.emergency_contact.model_dump() if payload.emergency_contact else None),
        submitted_at=now,
    )
    db.add(hiree)
    db.commit()
    db.refresh(hiree)

    # Notify admins a new application is in for review.
    _notify_onboarding_safe(
        db, hiree.tenant_id,
        title="Onboarding application submitted",
        body=f"{_applicant_label(hiree)} submitted their application — ready for review.",
        link="hirees.html",
        resource_type="hiree_onboarding", resource_id=str(hiree.id),
        meta={"event": "submitted"},
    )
    return {"id": str(hiree.id), "status": hiree.status}


# --------------------------------------------------------------------------- #
# ADMIN — review queue
# --------------------------------------------------------------------------- #
@router.get("/admin/hirees")
def list_hirees(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    q = db.query(HireeOnboarding).filter(HireeOnboarding.tenant_id == current_user.tenant_id)
    if status_filter:
        q = q.filter(HireeOnboarding.status == status_filter)
    rows = q.order_by(HireeOnboarding.created_at.desc()).all()
    return {"hirees": [_to_summary(h) for h in rows]}


@router.get("/admin/hirees/by-user/{user_id}")
def get_hiree_by_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """The onboarding application that produced this portal user (an approved hiree),
    so the admin User detail modal can surface the original hiree info. Returns
    {"hiree": <detail>|null} — null (200) when the user didn't come from a hiree."""
    h = (
        db.query(HireeOnboarding)
        .filter(
            HireeOnboarding.created_agent_user_id == user_id,
            HireeOnboarding.tenant_id == current_user.tenant_id,
        )
        .order_by(HireeOnboarding.created_at.desc())
        .first()
    )
    return {"hiree": _to_detail(h) if h else None}


@router.post("/admin/hirees/ensure-for-user/{user_id}")
def ensure_hiree_for_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Return the onboarding record linked to this user, creating a minimal one
    (linked + approved) if none exists — so the admin User modal can edit/upload the
    info for agents who never went through onboarding. {"hiree": <detail>}."""
    h = (
        db.query(HireeOnboarding)
        .filter(
            HireeOnboarding.created_agent_user_id == user_id,
            HireeOnboarding.tenant_id == current_user.tenant_id,
        )
        .order_by(HireeOnboarding.created_at.desc())
        .first()
    )
    if not h:
        u = (
            db.query(User)
            .filter(User.id == user_id, User.tenant_id == current_user.tenant_id, User.deleted_at.is_(None))
            .first()
        )
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        name = f"{u.first_name or ''} {u.last_name or ''}".strip() or (u.email or "Agent")
        h = HireeOnboarding(
            tenant_id=current_user.tenant_id,
            full_legal_name=name,
            first_name=u.first_name,
            last_name=u.last_name,
            email=u.email or f"agent-{user_id}@placeholder.local",
            status="approved",
            created_agent_user_id=user_id,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
    return {"hiree": _to_detail(h)}


@router.get("/admin/hirees/{hiree_id}")
def get_hiree(
    hiree_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    return _to_detail(h)


@router.post("/admin/hirees/{hiree_id}/approve")
def approve_hiree(
    hiree_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Approve an application: create the agent's login + Agent + state licenses."""
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    if h.status == "approved":
        raise HTTPException(status_code=409, detail="Application already approved")

    # Re-approval after a reopen: the agent login was already created on the
    # first approval — just flip the status back without recreating anything.
    if h.created_agent_user_id:
        prior = db.query(User).filter(
            User.id == h.created_agent_user_id, User.deleted_at.is_(None)
        ).first()
        if prior:
            # Re-activate the login in case a prior rejection deactivated it, so a
            # re-approved agent can log in and receive leads again (mirrors the
            # Manage Users active/inactive semantics: user.status active, agent active).
            prior.status = "active"
            prior_agent = db.query(Agent).filter(Agent.user_id == prior.id).first()
            if prior_agent:
                prior_agent.status = "active"
            h.status = "approved"
            h.reviewed_at = datetime.now(timezone.utc)
            h.reviewed_by = current_user.id
            db.commit()
            return {"id": str(h.id), "status": h.status, "agent_user_id": str(prior.id)}

    existing = db.query(User).filter(User.email == h.email, User.deleted_at.is_(None)).first()
    if existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    parts = (h.full_legal_name or "").split()
    first_name = (h.first_name or "").strip() or (parts[0] if parts else h.email.split("@")[0])
    last_name = (h.last_name or "").strip() or (" ".join(parts[1:]) if len(parts) > 1 else "")

    user = User(
        tenant_id=h.tenant_id,
        email=h.email,
        password_hash=h.password_hash or hash_password(uuid.uuid4().hex),
        first_name=first_name[:100],
        last_name=last_name[:100],
        role="agent",
        status="active",
    )
    db.add(user)
    db.flush()

    agent = Agent(tenant_id=h.tenant_id, user_id=user.id, status="active",
                  national_producer_number=(h.npn or None))
    db.add(agent)
    db.flush()

    today = date.today()
    for entry in (h.licensed_states or []):
        code = (entry.get("state_code") or "").strip().upper()[:2]
        if not code:
            continue
        db.add(AgentStateLicense(
            tenant_id=h.tenant_id,
            agent_id=agent.id,
            state_code=code,
            license_number=(entry.get("license_number") or "PENDING")[:100],
            effective_date=today,
            status="ACTIVE",
        ))

    h.status = "approved"
    h.reviewed_at = datetime.now(timezone.utc)
    h.reviewed_by = current_user.id
    h.created_agent_user_id = user.id
    db.commit()
    return {"id": str(h.id), "status": h.status, "agent_user_id": str(user.id)}


@router.post("/admin/hirees/{hiree_id}/reject")
def reject_hiree(
    hiree_id: uuid.UUID,
    payload: ReasonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A rejection reason is required")
    h.status = "rejected"
    h.rejection_reason = reason
    h.reviewed_at = datetime.now(timezone.utc)
    h.reviewed_by = current_user.id
    # If this hiree was previously approved (an agent login was created from it),
    # rejecting now deactivates that account: it stops being a usable agent.
    # Mirrors the Manage Users active/inactive toggle: user -> "suspended",
    # Agent record -> "inactive" (so they no longer receive leads / count as active).
    if h.created_agent_user_id:
        agent_user = db.query(User).filter(
            User.id == h.created_agent_user_id, User.deleted_at.is_(None)
        ).first()
        if agent_user:
            agent_user.status = "suspended"
            linked_agent = db.query(Agent).filter(Agent.user_id == agent_user.id).first()
            if linked_agent:
                linked_agent.status = "inactive"
    db.commit()
    return {"id": str(h.id), "status": h.status}


@router.post("/admin/hirees/{hiree_id}/pending")
def set_pending_hiree(
    hiree_id: uuid.UUID,
    payload: ReasonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Move an application to 'pending' with a required reason (why it's being
    held). Works from any status. Any agent login created on a previous approval
    is left untouched."""
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A reason is required to hold the application in pending")
    was_pending = h.status == "pending"
    h.status = "pending"
    h.pending_reason = reason
    # Keep the original "pending since" timestamp when only the reason is edited;
    # only stamp it when the application first enters pending.
    if not was_pending:
        h.reviewed_at = datetime.now(timezone.utc)
    h.reviewed_by = current_user.id
    db.commit()
    return {"id": str(h.id), "status": h.status}


@router.post("/admin/hirees/{hiree_id}/set-new")
def set_new_hiree(
    hiree_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Move an application back to the default 'new' state, clearing any prior
    review decision (pending/rejection reason, reviewer). Any agent login created
    on a previous approval is left untouched."""
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    h.status = "new"
    h.pending_reason = None
    h.rejection_reason = None
    h.reviewed_at = None
    h.reviewed_by = None
    db.commit()
    return {"id": str(h.id), "status": h.status}


@router.delete("/admin/hirees/{hiree_id}")
def delete_hiree(
    hiree_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Permanently remove an application record.

    Only deletes the application itself — if it was already approved, the agent
    login that was created from it is left untouched.
    """
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")
    db.delete(h)
    db.commit()
    return {"id": str(hiree_id), "deleted": True}


@router.patch("/admin/hirees/{hiree_id}")
def update_hiree(
    hiree_id: uuid.UUID,
    payload: HireeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Admin edit of any field on a submitted application (allowed in any status,
    including after approval). Only fields present in the request are changed.

    Note: editing the email here does NOT change the login of an agent that was
    already created on approval — that account is separate.
    """
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")

    data = payload.model_dump(exclude_unset=True)
    str_fields = [
        "full_legal_name", "first_name", "middle_name", "last_name", "phone", "ssn",
        "gender", "marital_status", "drivers_license_number", "street_address",
        "city", "state", "zip", "npn",
        "id_front_key", "id_back_key", "ffm_key", "agreement_key", "w9_key",
        "onboarding_doc_key",
    ]
    for f in str_fields:
        if f in data:
            v = data[f]
            setattr(h, f, v.strip() if isinstance(v, str) else v)
    for f in ("agreement_signed", "w9_signed", "has_carrier_logins"):
        if f in data and data[f] is not None:
            setattr(h, f, bool(data[f]))
    if "carrier_logins" in data and data["carrier_logins"] is not None:
        h.carrier_logins = data["carrier_logins"]
    if "releases" in data and data["releases"] is not None:
        h.releases = data["releases"]
    if "email" in data and data["email"]:
        h.email = str(data["email"]).strip().lower()
    if "date_of_birth" in data:
        h.date_of_birth = data["date_of_birth"]
    if "licensed_states" in data and payload.licensed_states is not None:
        h.licensed_states = [s.model_dump() for s in payload.licensed_states]
    if "bank_info" in data and payload.bank_info is not None:
        h.bank_info = payload.bank_info.model_dump()
    if "emergency_contact" in data and payload.emergency_contact is not None:
        h.emergency_contact = payload.emergency_contact.model_dump()
    if "identity_documents" in data and payload.identity_documents is not None:
        h.identity_documents = [d.model_dump() for d in payload.identity_documents]
    # Recompute the display name from parts unless it was set explicitly.
    if any(k in data for k in ("first_name", "middle_name", "last_name")) and "full_legal_name" not in data:
        parts = [h.first_name, h.middle_name, h.last_name]
        composed = " ".join(p.strip() for p in parts if p and p.strip())
        if composed:
            h.full_legal_name = composed
    h.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(h)
    return _to_detail(h)


def _doc_bytes(doc: OnboardingDocument) -> Optional[bytes]:
    """Raw bytes for a stored document (DB-inline or S3)."""
    if doc is None:
        return None
    if doc.storage == "s3":
        try:
            obj = s3_storage._client().get_object(Bucket=doc.s3_bucket or s3_storage.bucket, Key=doc.s3_key)
            return obj["Body"].read()
        except Exception:
            return None
    return doc.data or b""


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "file")).strip("_") or "file"


@router.get("/admin/hirees/{hiree_id}/attachments.zip")
def export_attachments(
    hiree_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(_admin),
):
    """Bundle every document uploaded on an application into a single .zip."""
    h = db.query(HireeOnboarding).filter(
        HireeOnboarding.id == hiree_id,
        HireeOnboarding.tenant_id == current_user.tenant_id,
    ).first()
    if not h:
        raise HTTPException(status_code=404, detail="Application not found")

    # (label, key) for every attachment on the application.
    items: List[tuple] = []
    id_labels = {"drivers_license": "Drivers-License", "state_id": "State-ID", "passport": "Passport"}
    for d in (h.identity_documents or []):
        lbl = id_labels.get(d.get("type"), d.get("type") or "ID")
        if d.get("front_key"):
            items.append((f"{lbl}-Front", d["front_key"]))
        if d.get("back_key"):
            items.append((f"{lbl}-Back", d["back_key"]))
    if not h.identity_documents:
        if h.id_front_key:
            items.append(("ID-Front", h.id_front_key))
        if h.id_back_key:
            items.append(("ID-Back", h.id_back_key))
    if h.ffm_key:
        items.append(("FFM-Certificate", h.ffm_key))
    if h.agreement_key:
        items.append(("Signed-Agreement", h.agreement_key))
    if h.w9_key:
        items.append(("Signed-W9", h.w9_key))
    if h.onboarding_doc_key:
        items.append(("Onboarding-Document", h.onboarding_doc_key))
    for r in (h.releases or []):
        if r.get("doc_key"):
            items.append((f"{(r.get('carrier') or 'Release').title()}-Release", r["doc_key"]))

    if not items:
        raise HTTPException(status_code=404, detail="No documents to export")

    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, key in items:
            doc = db.query(OnboardingDocument).filter(OnboardingDocument.key == key).first()
            content = _doc_bytes(doc)
            if content is None:
                continue
            ext = ""
            fname = (doc.filename if doc else "") or ""
            if "." in fname:
                ext = "." + fname.rsplit(".", 1)[1]
            elif doc and doc.content_type == "application/pdf":
                ext = ".pdf"
            elif doc and (doc.content_type or "").startswith("image/"):
                ext = "." + doc.content_type.split("/", 1)[1]
            name = f"{_safe_name(label)}{ext}"
            # avoid collisions
            n, base = 2, name
            while name in used:
                stem = base.rsplit(".", 1)[0]
                name = f"{stem}-{n}{ext}"
                n += 1
            used.add(name)
            zf.writestr(name, content)

    buf.seek(0)
    applicant = _safe_name(h.full_legal_name or "applicant")
    headers = {"Content-Disposition": f'attachment; filename="{applicant}-attachments.zip"'}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)
