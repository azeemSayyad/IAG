"""Inbox — admin/dev SMS chat with everyone: job applicants (hirees) AND portal
users. The contact is polymorphic (``contact_type`` = 'hiree' | 'user').

Threads auto-populate from every hiree and every portal user in the tenant.
Hirees are tagged "Hiree"; users show their role. Admins (and dev) send messages
here; replies arrive via the SMS webhook once a live provider is wired (/inbound).

No live provider yet: outbound messages are recorded locally with
settings.APPLICANT_SMS_FROM_NUMBER as the sender. A user's textable number is the
optional ``preferences.personal_phone`` (most have none → send is disabled in the
UI; the API returns 400 if asked to send with no number).
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_role
from app.models.applicant_message import ApplicantMessage
from app.models.hiree import HireeOnboarding
from app.models.user import User

router = APIRouter(prefix="/applicant-inbox", tags=["applicant-inbox"])

# Admins (and dev, which bypasses require_role) may use the inbox.
_admin = require_role("tenant_admin", "super_admin", "admin")

_ROLE_LABELS = {
    "agent": "Agent",
    "lead": "Team Lead",
    "manager": "Manager",
    "head": "Head Manager",
    "tenant_admin": "Admin",
    "super_admin": "Super Admin",
    "dev": "Dev",
}


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class SendMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=1600)


class InboundMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=1600)
    from_number: Optional[str] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _user_phone(u: User) -> str:
    prefs = u.preferences or {}
    if isinstance(prefs, dict):
        return str(prefs.get("personal_phone") or "").strip()
    return ""


def _user_name(u: User) -> str:
    name = " ".join(p for p in [u.first_name, u.last_name] if p).strip()
    return name or (u.email or "User")


def _hiree_name(h: HireeOnboarding) -> str:
    name = (h.full_legal_name or "").strip()
    if name:
        return name
    name = " ".join(p for p in [h.first_name, h.last_name] if p).strip()
    return name or (h.email or "Applicant")


def _msg_dict(m: ApplicantMessage) -> dict:
    return {
        "id": str(m.id),
        "contact_type": m.contact_type,
        "direction": m.direction,
        "sender_type": m.sender_type,
        "body": m.body,
        "status": m.status,
        "from_number": m.from_number,
        "phone_number": m.phone_number,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _thread_key(contact_type: str, contact_id) -> str:
    return f"{contact_type}:{contact_id}"


def _resolve_contact(db: Session, tenant_id, contact_type: str, contact_id: str) -> dict:
    """Return a normalized contact dict, or raise 404. Keys: type, id, name,
    email, phone, status, tag, link, can_text."""
    try:
        cid = uuid.UUID(str(contact_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Contact not found")

    if contact_type == "hiree":
        h = (
            db.query(HireeOnboarding)
            .filter(HireeOnboarding.id == cid, HireeOnboarding.tenant_id == tenant_id)
            .first()
        )
        if not h:
            raise HTTPException(status_code=404, detail="Applicant not found")
        phone = (h.phone or "").strip()
        return {
            "type": "hiree", "id": str(h.id), "name": _hiree_name(h), "email": h.email,
            "phone": phone, "status": h.status, "tag": "Hiree",
            "link": f"hirees.html?id={h.id}", "can_text": bool(phone),
        }
    if contact_type == "user":
        u = (
            db.query(User)
            .filter(User.id == cid, User.tenant_id == tenant_id, User.deleted_at.is_(None))
            .first()
        )
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        phone = _user_phone(u)
        return {
            "type": "user", "id": str(u.id), "name": _user_name(u), "email": u.email,
            "phone": phone, "status": u.status, "tag": _ROLE_LABELS.get(u.role, u.role.title()),
            "link": f"settings.html?user={u.id}#team", "can_text": bool(phone),
        }
    raise HTTPException(status_code=400, detail="Invalid contact type")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/threads")
def list_threads(db: Session = Depends(get_db), current_user: User = Depends(_admin)):
    """One SMS thread per hiree (job applicant) in the tenant, newest activity first.

    Portal users (agents) are handled by the in-app DM channel (/inbox/dm), NOT
    here — this domain is SMS-to-applicants only.
    """
    tenant_id = current_user.tenant_id

    contacts = []

    # Hirees (job applicants).
    for h in db.query(HireeOnboarding).filter(HireeOnboarding.tenant_id == tenant_id).all():
        phone = (h.phone or "").strip()
        contacts.append({
            "contact_type": "hiree", "contact_id": str(h.id), "key": _thread_key("hiree", h.id),
            "name": _hiree_name(h), "email": h.email, "phone": phone,
            "tag": "Hiree", "kind": "hiree",
            "status": h.status, "link": f"hirees.html?id={h.id}", "can_text": bool(phone),
        })

    # Last message + count per thread, in one pass over the tenant's messages.
    last_by_key: dict = {}
    count_by_key: dict = {}
    for m in (
        db.query(ApplicantMessage)
        .filter(ApplicantMessage.tenant_id == tenant_id)
        .order_by(ApplicantMessage.created_at.asc())
        .all()
    ):
        cid = m.user_id if m.contact_type == "user" else m.hiree_id
        key = _thread_key(m.contact_type, cid)
        last_by_key[key] = m
        count_by_key[key] = count_by_key.get(key, 0) + 1

    threads = []
    for c in contacts:
        last = last_by_key.get(c["key"])
        c = dict(c)
        c.pop("key", None)
        c.update({
            "last_message": last.body if last else None,
            "last_message_at": last.created_at.isoformat() if last and last.created_at else None,
            "last_direction": last.direction if last else None,
            "message_count": count_by_key.get(_thread_key(c["contact_type"], c["contact_id"]), 0),
        })
        threads.append(c)

    # Newest activity first; never-messaged contacts after, by name.
    messaged = [t for t in threads if t["last_message_at"]]
    unmessaged = [t for t in threads if not t["last_message_at"]]
    messaged.sort(key=lambda t: t["last_message_at"], reverse=True)
    unmessaged.sort(key=lambda t: (t["name"] or "").lower())

    return {"threads": messaged + unmessaged, "from_number": settings.APPLICANT_SMS_FROM_NUMBER}


@router.get("/threads/{contact_type}/{contact_id}")
def get_thread(
    contact_type: str, contact_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(_admin),
):
    """Contact details + the full message history for the thread."""
    tenant_id = current_user.tenant_id
    contact = _resolve_contact(db, tenant_id, contact_type, contact_id)

    col = ApplicantMessage.user_id if contact_type == "user" else ApplicantMessage.hiree_id
    messages = (
        db.query(ApplicantMessage)
        .filter(
            ApplicantMessage.tenant_id == tenant_id,
            ApplicantMessage.contact_type == contact_type,
            col == uuid.UUID(contact["id"]),
        )
        .order_by(ApplicantMessage.created_at.asc())
        .all()
    )
    return {
        "contact": contact,
        "from_number": settings.APPLICANT_SMS_FROM_NUMBER,
        "messages": [_msg_dict(m) for m in messages],
    }


@router.post("/threads/{contact_type}/{contact_id}/send", status_code=status.HTTP_201_CREATED)
def send_message(
    contact_type: str, contact_id: str, payload: SendMessage,
    db: Session = Depends(get_db), current_user: User = Depends(_admin),
):
    """Admin → contact. Recorded locally (no live provider wired yet)."""
    tenant_id = current_user.tenant_id
    contact = _resolve_contact(db, tenant_id, contact_type, contact_id)

    if not contact["can_text"]:
        raise HTTPException(status_code=400, detail="No phone number on file for this contact")

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Message body is required")

    # Live-send hiree texts from the DEDICATED applicant number (a separate channel
    # from lead outreach — it never goes through the first-template lockdown). When the
    # provider is unconfigured/disabled the send is 'skipped' and we record locally, as
    # before; a hard provider error is recorded as a FAILED message so it stays visible.
    from_number = settings.APPLICANT_SMS_FROM_NUMBER
    status = "SENT"
    if contact_type == "hiree":
        from app.applicant_inbox.provider import applicant_provider
        result = applicant_provider.send(to=contact["phone"], body=body) or {}
        if result.get("from"):
            from_number = result["from"]
        if (result.get("status") or "").lower() == "failed":
            status = "FAILED"

    msg = ApplicantMessage(
        tenant_id=tenant_id,
        contact_type=contact_type,
        hiree_id=uuid.UUID(contact["id"]) if contact_type == "hiree" else None,
        user_id=uuid.UUID(contact["id"]) if contact_type == "user" else None,
        phone_number=contact["phone"],
        from_number=from_number,
        direction="OUTBOUND",
        body=body,
        sender_type="ADMIN",
        status=status,
        sent_by=current_user.id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"message": _msg_dict(msg)}


@router.post("/threads/{contact_type}/{contact_id}/inbound", status_code=status.HTTP_201_CREATED)
def record_inbound(
    contact_type: str, contact_id: str, payload: InboundMessage,
    db: Session = Depends(get_db), current_user: User = Depends(_admin),
):
    """Contact → admin. Seam for the SMS webhook to deliver replies once a live
    provider is connected. Stored as an INBOUND message on the thread."""
    tenant_id = current_user.tenant_id
    contact = _resolve_contact(db, tenant_id, contact_type, contact_id)
    phone = (payload.from_number or contact["phone"] or "").strip()

    msg = ApplicantMessage(
        tenant_id=tenant_id,
        contact_type=contact_type,
        hiree_id=uuid.UUID(contact["id"]) if contact_type == "hiree" else None,
        user_id=uuid.UUID(contact["id"]) if contact_type == "user" else None,
        phone_number=phone,
        from_number=phone,
        direction="INBOUND",
        body=payload.body.strip(),
        sender_type="APPLICANT" if contact_type == "hiree" else "USER",
        status="RECEIVED",
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"message": _msg_dict(msg)}
