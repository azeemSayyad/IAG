"""Per-user in-app notifications API (powers the Notifications tab)."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.notification import Notification
from app.models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _serialize(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "resource_type": n.resource_type,
        "resource_id": str(n.resource_id) if n.resource_id else None,
        "meta": n.meta or {},
        "read": bool(n.read),
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("", response_model=dict)
def list_notifications(
    only_unread: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """The signed-in user's own notifications (newest first) + unread count."""
    base = db.query(Notification).filter(
        Notification.tenant_id == tenant_id,
        Notification.user_id == current_user.id,
    )
    unread_count = base.filter(Notification.read.is_(False)).count()
    q = base
    if only_unread:
        q = q.filter(Notification.read.is_(False))
    total = q.count()
    rows = (
        q.order_by(Notification.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "items": [_serialize(n) for n in rows],
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "size": size,
    }


@router.post("/{notification_id}/read", response_model=dict)
def mark_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    row = db.query(Notification).filter(
        Notification.tenant_id == tenant_id,
        Notification.user_id == current_user.id,
        Notification.id == notification_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read = True
    db.commit()
    return {"ok": True}


@router.post("/read-all", response_model=dict)
def mark_all_read(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    updated = (
        db.query(Notification)
        .filter(
            Notification.tenant_id == tenant_id,
            Notification.user_id == current_user.id,
            Notification.read.is_(False),
        )
        .update({Notification.read: True}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "updated": int(updated or 0)}
