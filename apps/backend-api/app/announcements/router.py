"""
Admin announcements API.

An admin sends an announcement to all agents (the default), everyone, or
hand-picked people. Each recipient gets a blocking popup on whatever page they
are on and must press Received; the sender sees "N of M received" and who is
still outstanding.

Delivery is live: after a send every recipient's per-user socket room gets an
``inapp_message`` event with ``kind="announcement"``. That event name is already
whitelisted by both socket layers (services/api.js is deliberately untouched);
the payload is only a nudge — the popup always re-reads /announcements/pending,
so the server stays the one judge of who sees what. announcements.js also polls
every 30s, which covers anyone whose socket is down.
"""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.agent import Agent
from app.models.announcement import Announcement, AnnouncementAck, AnnouncementRecipient
from app.models.user import User
from app.realtime.websocket import emit_to_user_room

router = APIRouter(prefix="/announcements", tags=["announcements"])

_ADMIN_ROLES = ("admin", "tenant_admin", "super_admin", "dev")
AUDIENCES = ("agents", "everyone", "custom")
MAX_BODY = 1000


def _is_admin(u: User) -> bool:
    return (u.role or "").lower() in _ADMIN_ROLES


def _require_admin(u: User) -> None:
    if not _is_admin(u):
        raise HTTPException(status_code=403, detail="Admins only")


def _name(u: User) -> str:
    return f"{u.first_name or ''} {u.last_name or ''}".strip() or (u.email or "User")


def _active_users(db: Session, tenant_id: str):
    return db.query(User).filter(
        User.tenant_id == tenant_id, User.deleted_at.is_(None), User.status == "active"
    )


def _recipients(db: Session, tenant_id: str, ann: Announcement) -> list[User]:
    """Everyone this announcement is for. The sender never gets their own popup."""
    q = _active_users(db, tenant_id)
    if ann.created_by is not None:
        q = q.filter(User.id != ann.created_by)
    if ann.audience == "everyone":
        return q.all()
    if ann.audience == "custom":
        ids = [r[0] for r in db.query(AnnouncementRecipient.user_id)
               .filter(AnnouncementRecipient.announcement_id == ann.id).all()]
        return q.filter(User.id.in_(ids)).all() if ids else []
    if ann.audience is None and ann.target_agent_id is not None:
        # Legacy row: one targeted agent profile.
        uid = db.query(Agent.user_id).filter(Agent.id == ann.target_agent_id).scalar()
        return q.filter(User.id == uid).all() if uid else []
    # 'agents', and legacy broadcast rows.
    return q.filter(User.role == "agent").all()


def _audience_label(ann: Announcement, n: int) -> str:
    if ann.audience == "everyone":
        return "Everyone"
    if ann.audience == "custom" or (ann.audience is None and ann.target_agent_id):
        return f"{n} selected " + ("person" if n == 1 else "people")
    return "All agents"


@router.get("/users")
def announce_users(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: everyone who can receive an announcement, for the people picker."""
    _require_admin(current_user)
    rows = (_active_users(db, tenant_id).filter(User.id != current_user.id)
            .order_by(User.first_name, User.last_name).all())
    return {"users": [{"id": str(u.id), "name": _name(u), "role": u.role, "email": u.email}
                      for u in rows]}


@router.post("")
async def create_announcement(
    body: dict = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: send to `audience` = agents (default) | everyone | custom (`user_ids`)."""
    _require_admin(current_user)
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Announcement text is required")
    if len(text) > MAX_BODY:
        raise HTTPException(status_code=400, detail=f"Keep it under {MAX_BODY} characters")
    audience = str(body.get("audience") or "agents").lower()
    if audience not in AUDIENCES:
        raise HTTPException(status_code=422, detail="audience must be agents, everyone or custom")

    picked: list[User] = []
    if audience == "custom":
        raw = body.get("user_ids") or []
        if not isinstance(raw, list) or not raw:
            raise HTTPException(status_code=422, detail="Pick at least one person")
        try:
            ids = {UUID(str(x)) for x in raw}
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid user id")
        picked = _active_users(db, tenant_id).filter(User.id.in_(ids), User.id != current_user.id).all()
        if not picked:
            raise HTTPException(status_code=422, detail="None of those people can receive announcements")

    ann = Announcement(tenant_id=tenant_id, body=text, audience=audience,
                       created_by=current_user.id, active=True)
    db.add(ann)
    db.flush()
    for u in picked:
        db.add(AnnouncementRecipient(announcement_id=ann.id, user_id=u.id))
    db.commit()
    db.refresh(ann)

    recipients = _recipients(db, tenant_id, ann)
    nudge = {"kind": "announcement", "announcement_id": str(ann.id)}
    for u in recipients:
        try:
            await emit_to_user_room(str(u.id), "inapp_message", nudge)
        except Exception:
            pass  # the 30s poll in announcements.js still delivers it
    return {"id": str(ann.id), "body": ann.body, "audience": ann.audience,
            "recipients": len(recipients), "created_at": ann.created_at.isoformat()}


@router.get("")
def list_announcements(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: recent announcements with how many recipients pressed Received."""
    _require_admin(current_user)
    anns = (db.query(Announcement).filter(Announcement.tenant_id == tenant_id)
            .order_by(Announcement.created_at.desc()).limit(30).all())
    senders = {u.id: _name(u) for u in db.query(User).filter(
        User.id.in_({a.created_by for a in anns if a.created_by})).all()} if anns else {}
    out = []
    for a in anns:
        recips = _recipients(db, tenant_id, a)
        rids = {u.id for u in recips}
        acked = {r[0] for r in db.query(AnnouncementAck.user_id)
                 .filter(AnnouncementAck.announcement_id == a.id).all()}
        out.append({
            "id": str(a.id), "body": a.body, "audience": a.audience or "agents",
            "audience_label": _audience_label(a, len(recips)),
            "sender_name": senders.get(a.created_by, ""),
            "recipients": len(recips), "received": len(rids & acked),
            "created_at": a.created_at.isoformat(),
        })
    return {"announcements": out}


@router.get("/{announcement_id}/receipts")
def receipts(
    announcement_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Admin: who pressed Received (and when) and who has not yet."""
    _require_admin(current_user)
    a = db.query(Announcement).filter(Announcement.id == announcement_id,
                                      Announcement.tenant_id == tenant_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Announcement not found")
    acked = dict(db.query(AnnouncementAck.user_id, AnnouncementAck.acked_at)
                 .filter(AnnouncementAck.announcement_id == a.id).all())
    received, pending_ = [], []
    for u in _recipients(db, tenant_id, a):
        row = {"user_id": str(u.id), "name": _name(u), "role": u.role}
        if u.id in acked:
            received.append({**row, "acked_at": acked[u.id].isoformat()})
        else:
            pending_.append(row)
    received.sort(key=lambda r: r["acked_at"], reverse=True)
    pending_.sort(key=lambda r: r["name"].lower())
    return {"received": received, "pending": pending_}


@router.get("/pending")
def pending(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """The signed-in user's announcements still waiting for Received, oldest first."""
    acked = {r[0] for r in db.query(AnnouncementAck.announcement_id)
             .filter(AnnouncementAck.user_id == current_user.id).all()}
    picked = {r[0] for r in db.query(AnnouncementRecipient.announcement_id)
              .filter(AnnouncementRecipient.user_id == current_user.id).all()}
    my_agent = db.query(Agent.id).filter(Agent.user_id == current_user.id,
                                         Agent.tenant_id == tenant_id).scalar()
    is_agent = (current_user.role or "").lower() == "agent"

    def mine(a: Announcement) -> bool:
        if a.created_by == current_user.id:
            return False
        if a.audience == "everyone":
            return True
        if a.audience == "custom":
            return a.id in picked
        if a.audience is None and a.target_agent_id is not None:
            return my_agent is not None and a.target_agent_id == my_agent
        return is_agent

    anns = (db.query(Announcement)
            .filter(Announcement.tenant_id == tenant_id, Announcement.active.is_(True))
            .order_by(Announcement.created_at.asc()).all())
    todo = [a for a in anns if a.id not in acked and mine(a)]
    senders = {u.id: _name(u) for u in db.query(User).filter(
        User.id.in_({a.created_by for a in todo if a.created_by})).all()} if todo else {}
    return {"pending": [{"id": str(a.id), "body": a.body, "sender_name": senders.get(a.created_by, ""),
                         "created_at": a.created_at.isoformat()} for a in todo]}


@router.post("/{announcement_id}/ack")
def ack(
    announcement_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """The recipient pressed Received."""
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
