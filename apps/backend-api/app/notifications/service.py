"""Helpers to create per-user in-app notifications (Notifications tab)."""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User
from app.core.permissions import user_has_permission, Permission


def create_notification(
    db: Session,
    tenant_id: str,
    user_id: str,
    *,
    type: str,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[dict] = None,
    commit: bool = True,
) -> Notification:
    """Create one notification for a single recipient user."""
    row = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        link=link,
        resource_type=resource_type,
        resource_id=resource_id,
        meta=meta or {},
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def mark_resource_notifications_read(db: Session, tenant_id: str, resource_type: str, resource_id) -> int:
    """Resolve (mark read) every notification tied to a resource — e.g. when a
    license is approved/rejected, the admins' 'pending review' notifications are
    cleared so their list/badge updates. Non-fatal."""
    try:
        n = (
            db.query(Notification)
            .filter(
                Notification.tenant_id == tenant_id,
                Notification.resource_type == resource_type,
                Notification.resource_id == resource_id,
                Notification.read.is_(False),
            )
            .update({Notification.read: True}, synchronize_session=False)
        )
        db.commit()
        return int(n or 0)
    except Exception:
        db.rollback()
        return 0


def notify_onboarding_admins(
    db: Session,
    tenant_id: str,
    *,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[dict] = None,
    type: str = "onboarding",
) -> List[Notification]:
    """Fan out a notification to every user who can review hirees / create the
    agent login (USER_CREATE) — i.e. the admins who see the Hirees page. One row
    per recipient."""
    users = (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.deleted_at.is_(None), User.status == "active")
        .all()
    )
    created: List[Notification] = []
    for u in users:
        if not user_has_permission(u, Permission.USER_CREATE):
            continue
        created.append(
            create_notification(
                db,
                tenant_id,
                str(u.id),
                type=type,
                title=title,
                body=body,
                link=link,
                resource_type=resource_type,
                resource_id=resource_id,
                meta=meta,
                commit=False,
            )
        )
    db.commit()
    return created


def notify_compliance_admins(
    db: Session,
    tenant_id: str,
    *,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[dict] = None,
    type: str = "compliance",
    exclude_user_id: Optional[str] = None,
) -> List[Notification]:
    """Fan out a notification to every user in the tenant who can MANAGE
    compliance (admins/managers who review licenses). One row per recipient."""
    users = (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.deleted_at.is_(None), User.status == "active")
        .all()
    )
    created: List[Notification] = []
    for u in users:
        if exclude_user_id and str(u.id) == str(exclude_user_id):
            continue
        if not user_has_permission(u, Permission.COMPLIANCE_MANAGE):
            continue
        created.append(
            create_notification(
                db,
                tenant_id,
                str(u.id),
                type=type,
                title=title,
                body=body,
                link=link,
                resource_type=resource_type,
                resource_id=resource_id,
                meta=meta,
                commit=False,
            )
        )
    db.commit()
    return created
