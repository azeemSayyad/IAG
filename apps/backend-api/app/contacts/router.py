"""Work contact book — admin-class only.

The internal phone book: agents, staff, vendors, carrier reps. Separate from
Leads (customers) and Users (logins) on purpose — this is just the numbers an
admin needs to hand, and a contact can be saved before that person exists
anywhere else in the system.

Contacts are soft-deleted so a removal can be undone.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_audit_event
from app.core.database import get_db
from app.core.deps import get_tenant_id, require_role
from app.models.contact import Contact
from app.models.user import User
from app.schemas.contact import ContactCreate, ContactResponse, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])

# Admin-class (plus dev, which passes every gate). Agents don't get the company
# phone book; they have their own leads.
_require_admin = require_role("tenant_admin", "super_admin", "admin")


def _audit(db: Session, tenant_id: str, user: User, action: str, contact_id, details: dict) -> None:
    try:
        log_audit_event(
            tenant_id=tenant_id, action=action, resource_type="contact",
            resource_id=str(contact_id), user_id=str(user.id), details=details, db=db,
        )
    except Exception:
        db.rollback()


def _own(db: Session, tenant_id: str, contact_id: UUID) -> Contact:
    c = (
        db.query(Contact)
        .filter(Contact.id == contact_id, Contact.tenant_id == tenant_id,
                Contact.deleted_at.is_(None))
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    return c


@router.get("", response_model=list[ContactResponse])
def list_contacts(
    q: Optional[str] = Query(None, description="Match on name, phone, email or role"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_admin),
):
    query = db.query(Contact).filter(
        Contact.tenant_id == tenant_id, Contact.deleted_at.is_(None)
    )
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(
            Contact.name.ilike(like),
            Contact.phone.ilike(like),
            Contact.email.ilike(like),
            Contact.role.ilike(like),
        ))
    return query.order_by(Contact.name).limit(limit).all()


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    c = Contact(tenant_id=tenant_id, created_by=user.id, **payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    _audit(db, tenant_id, user, "create", c.id, {"name": c.name, "phone": c.phone})
    return c


@router.patch("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: UUID,
    payload: ContactUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    c = _own(db, tenant_id, contact_id)
    before = {"name": c.name, "phone": c.phone, "email": c.email, "role": c.role}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(c, field, value)
    db.commit()
    db.refresh(c)
    _audit(db, tenant_id, user, "update", c.id,
           {"before": before, "after": {"name": c.name, "phone": c.phone,
                                        "email": c.email, "role": c.role}})
    return c


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    """Soft-delete: the row stays so a mistaken removal can be undone."""
    c = _own(db, tenant_id, contact_id)
    c.deleted_at = datetime.now(timezone.utc)
    db.commit()
    _audit(db, tenant_id, user, "delete", c.id, {"name": c.name, "phone": c.phone})
    return None
