"""In-app direct messaging between admins and agents (NOT SMS).

Channel:
  * An ADMIN-side user (tenant_admin / super_admin / admin / dev) chats with AGENTS.
  * An AGENT chats with ADMINS (tenant_admin / super_admin / admin).
Other roles (lead / manager / head) are not part of this channel.

Messages persist in direct_messages (source of truth) and are pushed in realtime
over Socket.IO to the recipient's per-user room as an ``inapp_message`` event.
"""
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.direct_message import DirectMessage
from app.models.user import User
from app.realtime.websocket import emit_to_user_room

router = APIRouter(prefix="/inbox/dm", tags=["direct-messages"])

# Roles whose Inbox shows AGENTS (they sit on the "admin" side of the channel).
ADMIN_SIDE = {"tenant_admin", "super_admin", "admin", "dev"}
# Admin roles an AGENT may message (the contacts shown in their Admin Inbox).
ADMIN_CONTACTS = {"tenant_admin", "super_admin", "admin"}

_ROLE_LABELS = {
    "agent": "Agent", "lead": "Team Lead", "manager": "Manager", "head": "Head Manager",
    "tenant_admin": "Admin", "super_admin": "Super Admin", "admin": "Admin", "dev": "Dev",
}


class SendMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


def _name(u: User) -> str:
    n = " ".join(p for p in [u.first_name, u.last_name] if p).strip()
    return n or (u.email or "User")


def _counterpart_query(db: Session, me: User):
    """Users the current user is allowed to DM, as a SQLAlchemy query (or None)."""
    q = db.query(User).filter(User.tenant_id == me.tenant_id, User.deleted_at.is_(None), User.id != me.id)
    if me.role in ADMIN_SIDE:
        # Admins only DM ACTIVE agents — deactivated agents drop out of the inbox roster.
        return q.filter(User.role == "agent", User.status == "active")
    if me.role == "agent":
        return q.filter(User.role.in_(list(ADMIN_CONTACTS)))
    return None


def _resolve_counterpart(db: Session, me: User, other_id: str) -> User:
    cq = _counterpart_query(db, me)
    if cq is None:
        raise HTTPException(status_code=403, detail="Not part of the in-app messaging channel")
    try:
        oid = uuid.UUID(str(other_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Contact not found")
    other = cq.filter(User.id == oid).first()
    if not other:
        raise HTTPException(status_code=404, detail="Contact not found")
    return other


def _msg_dict(m: DirectMessage, me_id) -> dict:
    return {
        "id": str(m.id),
        "sender_id": str(m.sender_id),
        "recipient_id": str(m.recipient_id),
        "mine": str(m.sender_id) == str(me_id),
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "read_at": m.read_at.isoformat() if m.read_at else None,
    }


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Total unread in-app messages addressed to the current user (for the
    sidebar Inbox / Admin Inbox badge)."""
    n = (
        db.query(func.count(DirectMessage.id))
        .filter(
            DirectMessage.tenant_id == me.tenant_id,
            DirectMessage.recipient_id == me.id,
            DirectMessage.read_at.is_(None),
        )
        .scalar()
    )
    return {"unread": int(n or 0)}


@router.get("/threads")
def list_threads(db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Counterpart list (admins↔agents), newest activity first, with unread counts."""
    cq = _counterpart_query(db, me)
    if cq is None:
        return {"threads": [], "self_id": str(me.id)}
    counterparts = cq.all()

    # All messages involving me, one pass → last message + unread per peer.
    msgs = (
        db.query(DirectMessage)
        .filter(
            DirectMessage.tenant_id == me.tenant_id,
            or_(DirectMessage.sender_id == me.id, DirectMessage.recipient_id == me.id),
        )
        .order_by(DirectMessage.created_at.asc())
        .all()
    )
    last_by_peer: dict = {}
    unread_by_peer: dict = {}
    for m in msgs:
        peer = str(m.recipient_id) if str(m.sender_id) == str(me.id) else str(m.sender_id)
        last_by_peer[peer] = m
        if str(m.recipient_id) == str(me.id) and m.read_at is None:
            unread_by_peer[peer] = unread_by_peer.get(peer, 0) + 1

    threads = []
    for u in counterparts:
        last = last_by_peer.get(str(u.id))
        threads.append({
            "user_id": str(u.id),
            "name": _name(u),
            "email": u.email,
            "tag": _ROLE_LABELS.get(u.role, u.role.title()),
            "role": u.role,
            "channel": "inapp",
            "last_message": last.body if last else None,
            "last_message_at": last.created_at.isoformat() if last and last.created_at else None,
            "last_mine": (str(last.sender_id) == str(me.id)) if last else None,
            "unread": unread_by_peer.get(str(u.id), 0),
        })

    messaged = [t for t in threads if t["last_message_at"]]
    unmessaged = [t for t in threads if not t["last_message_at"]]
    messaged.sort(key=lambda t: t["last_message_at"], reverse=True)
    unmessaged.sort(key=lambda t: (t["name"] or "").lower())
    return {"threads": messaged + unmessaged, "self_id": str(me.id)}


@router.get("/threads/{user_id}")
def get_thread(user_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Full message history with one counterpart; marks their messages read."""
    other = _resolve_counterpart(db, me, user_id)
    pair = or_(
        and_(DirectMessage.sender_id == me.id, DirectMessage.recipient_id == other.id),
        and_(DirectMessage.sender_id == other.id, DirectMessage.recipient_id == me.id),
    )
    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.tenant_id == me.tenant_id, pair)
        .order_by(DirectMessage.created_at.asc())
        .all()
    )
    # Mark incoming unread as read.
    now = datetime.now(timezone.utc)
    changed = False
    for m in messages:
        if str(m.recipient_id) == str(me.id) and m.read_at is None:
            m.read_at = now
            changed = True
    if changed:
        db.commit()

    return {
        "contact": {
            "user_id": str(other.id), "name": _name(other), "email": other.email,
            "tag": _ROLE_LABELS.get(other.role, other.role.title()), "role": other.role,
            "channel": "inapp",
        },
        "self_id": str(me.id),
        "messages": [_msg_dict(m, me.id) for m in messages],
    }


@router.post("/threads/{user_id}/send", status_code=status.HTTP_201_CREATED)
async def send_message(
    user_id: str, payload: SendMessage,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Send an in-app message to a counterpart + push it in realtime."""
    other = _resolve_counterpart(db, me, user_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Message body is required")

    msg = DirectMessage(
        tenant_id=me.tenant_id,
        sender_id=me.id,
        recipient_id=other.id,
        body=body,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # Realtime push to the recipient's per-user room (their Inbox / Admin Inbox
    # appends it live; if offline they get it on next load from the DB).
    payload_out = {
        "id": str(msg.id),
        "peer_id": str(me.id),          # from the recipient's perspective, the thread peer
        "sender_id": str(me.id),
        "sender_name": _name(me),
        "recipient_id": str(other.id),
        "body": msg.body,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
    try:
        # Deliver to the recipient only. We intentionally do NOT echo back to the
        # sender: the sender's UI shows the message optimistically (instantly) and
        # reconciles on the HTTP response, so an echo would duplicate it.
        await emit_to_user_room(str(other.id), "inapp_message", payload_out)
    except Exception:
        pass

    return {"message": _msg_dict(msg, me.id)}
