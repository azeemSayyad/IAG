"""
Admin announcements API.

Admin pushes an announcement to all agents (target_agent_id NULL) or one agent.
Targeted agents must acknowledge it before their UI unblocks (blocking popup).
"""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.agent import Agent
from app.models.announcement import Announcement, AnnouncementAck
from app.models.user import User

router = APIRouter(prefix="/announcements", tags=["announcements"])

_ADMIN_ROLES = ("admin", "tenant_admin", "super_admin")


def _is_admin(u: User) -> bool:
    return (u.role or "").lower() in _ADMIN_ROLES


@router.post("")
def create_announcement(
    body: dict = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: push an announcement to all agents (no target_agent_id) or one agent."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admins only")
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Announcement text is required")
    target = body.get("target_agent_id") or None
    if target:
        if not db.query(Agent).filter(Agent.id == target, Agent.tenant_id == tenant_id).first():
            raise HTTPException(status_code=404, detail="Agent not found")
    ann = Announcement(tenant_id=tenant_id, body=text, target_agent_id=target,
                       created_by=current_user.id, active=True)
    db.add(ann); db.commit(); db.refresh(ann)
    return {"id": str(ann.id), "body": ann.body,
            "target_agent_id": str(ann.target_agent_id) if ann.target_agent_id else None,
            "created_at": ann.created_at.isoformat()}


@router.get("")
def list_announcements(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: list sent announcements with per-announcement ack counts."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admins only")
    anns = (db.query(Announcement).filter(Announcement.tenant_id == tenant_id)
            .order_by(Announcement.created_at.desc()).limit(100).all())
    name_map = {}
    tgt_ids = {a.target_agent_id for a in anns if a.target_agent_id}
    if tgt_ids:
        for aid, fn, ln in (db.query(Agent.id, User.first_name, User.last_name)
                            .join(User, Agent.user_id == User.id)
                            .filter(Agent.id.in_(tgt_ids)).all()):
            name_map[aid] = (f"{fn or ''} {ln or ''}".strip() or "Agent")
    out = []
    for a in anns:
        acks = db.query(AnnouncementAck).filter(AnnouncementAck.announcement_id == a.id).count()
        out.append({"id": str(a.id), "body": a.body, "active": a.active, "acks": acks,
                    "target_agent_id": str(a.target_agent_id) if a.target_agent_id else None,
                    "target_name": name_map.get(a.target_agent_id) if a.target_agent_id else "All agents",
                    "created_at": a.created_at.isoformat()})
    return {"announcements": out}


@router.get("/agents")
def announce_agents(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: agents in this tenant, for the target dropdown."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admins only")
    rows = (db.query(Agent.id, User.first_name, User.last_name)
            .join(User, Agent.user_id == User.id)
            .filter(Agent.tenant_id == tenant_id).all())
    return {"agents": [{"agent_id": str(aid), "name": (f"{fn or ''} {ln or ''}".strip() or "Agent")}
                       for aid, fn, ln in rows]}


@router.get("/pending")
def pending(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """The signed-in AGENT's unacknowledged announcements (broadcast or targeted).
    Non-agents (admins) get an empty list — they are never blocked."""
    agent = db.query(Agent).filter(Agent.user_id == current_user.id,
                                   Agent.tenant_id == tenant_id).first()
    if not agent:
        return {"pending": []}
    acked = [r[0] for r in db.query(AnnouncementAck.announcement_id)
             .filter(AnnouncementAck.user_id == current_user.id).all()]
    q = db.query(Announcement).filter(
        Announcement.tenant_id == tenant_id,
        Announcement.active.is_(True),
        ((Announcement.target_agent_id.is_(None)) | (Announcement.target_agent_id == agent.id)),
    )
    if acked:
        q = q.filter(Announcement.id.notin_(acked))
    q = q.order_by(Announcement.created_at.asc())
    return {"pending": [{"id": str(a.id), "body": a.body, "created_at": a.created_at.isoformat()}
                        for a in q.all()]}


@router.post("/{announcement_id}/ack")
def ack(
    announcement_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Agent confirms they have seen + agree — unblocks their UI for this one."""
    a = db.query(Announcement).filter(Announcement.id == announcement_id,
                                      Announcement.tenant_id == tenant_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Announcement not found")
    exists = db.query(AnnouncementAck).filter(
        AnnouncementAck.announcement_id == announcement_id,
        AnnouncementAck.user_id == current_user.id).first()
    if not exists:
        db.add(AnnouncementAck(announcement_id=announcement_id, user_id=current_user.id))
        try:
            db.commit()
        except Exception:
            db.rollback()
    return {"ok": True}
