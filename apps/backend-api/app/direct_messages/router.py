"""In-app direct messaging between portal users (NOT SMS).

Channel: EVERY active user in the tenant can message EVERY other active user,
whatever their roles (admin ↔ agent, agent ↔ agent, admin ↔ admin, …). It used
to be restricted to admin ↔ agent pairs; that restriction was lifted so the
single Inbox page is one place for all in-app conversations.

Groups: a super_admin can create a named group (dm_groups) with a free pick of
members. Every member can read it and reply in it; only a super_admin renames
it, changes its membership or deletes it.

Messages persist in direct_messages (source of truth) and are pushed in realtime
over Socket.IO to each recipient's per-user room as an ``inapp_message`` event
(group messages carry ``group_id``; 1:1 messages carry ``peer_id``).
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.direct_message import DirectMessage, DmGroup, DmGroupMember
from app.models.user import User
from app.realtime.websocket import emit_to_user_room

router = APIRouter(prefix="/inbox/dm", tags=["direct-messages"])

_ROLE_LABELS = {
    "agent": "Agent", "lead": "Team Lead", "manager": "Manager", "head": "Head Manager",
    "tenant_admin": "Admin", "super_admin": "Super Admin", "admin": "Admin", "dev": "Dev",
}


# Only a super_admin may create / rename / re-member / delete a group. Everyone
# else can read and reply in the groups they were put in.
_GROUP_ADMIN_ROLES = ("super_admin",)


class SendMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    member_ids: List[str] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    member_ids: Optional[List[str]] = None


def _name(u: User) -> str:
    n = " ".join(p for p in [u.first_name, u.last_name] if p).strip()
    return n or (u.email or "User")


def _counterpart_query(db: Session, me: User):
    """Users the current user is allowed to DM, as a SQLAlchemy query (or None).

    Everyone in the tenant, any role. Deactivated / deleted users drop out of
    the roster (their history stays in direct_messages).
    """
    return db.query(User).filter(
        User.tenant_id == me.tenant_id,
        User.deleted_at.is_(None),
        User.status == "active",
        User.id != me.id,
    )


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
        "recipient_id": str(m.recipient_id) if m.recipient_id else None,
        "mine": str(m.sender_id) == str(me_id),
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "read_at": m.read_at.isoformat() if m.read_at else None,
    }


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Total unread in-app messages for the current user — 1:1 threads PLUS the
    groups they belong to (for the sidebar Inbox badge)."""
    n = (
        db.query(func.count(DirectMessage.id))
        .filter(
            DirectMessage.tenant_id == me.tenant_id,
            DirectMessage.group_id.is_(None),
            DirectMessage.recipient_id == me.id,
            DirectMessage.read_at.is_(None),
        )
        .scalar()
    )
    total = int(n or 0)
    for _g, mem in _my_groups(db, me):
        total += _group_unread(db, me, mem)
    return {"unread": total}


@router.get("/threads")
def list_threads(db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Counterpart list (every other active user), newest activity first, with unread counts."""
    cq = _counterpart_query(db, me)
    if cq is None:
        return {"threads": [], "self_id": str(me.id)}
    counterparts = cq.all()

    # All messages involving me, one pass → last message + unread per peer.
    msgs = (
        db.query(DirectMessage)
        .filter(
            DirectMessage.tenant_id == me.tenant_id,
            DirectMessage.group_id.is_(None),   # group messages have no single peer
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
async def get_thread(user_id: str, db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
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
        # Tell MY other tabs/pages the thread is read so their sidebar Inbox
        # badge drops without waiting for the focus / 30s refresh.
        try:
            await emit_to_user_room(str(me.id), "inapp_read", {"peer_id": str(other.id)})
        except Exception:
            pass

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


# ===========================================================================
# Groups
# ===========================================================================
# A group message is a direct_messages row with group_id set and recipient_id
# NULL, so one row serves every member. "Read" therefore CANNOT live on the
# message (many readers, one row) — each member carries their own watermark in
# DmGroupMember.last_read_at, and their unread is everything newer than it that
# they did not send themselves. A member added later starts from added_at, so
# joining a busy group does not hand them a badge for the whole backlog.


def _is_group_admin(me: User) -> bool:
    return (me.role or "").lower() in _GROUP_ADMIN_ROLES


def _require_group_admin(me: User) -> None:
    if not _is_group_admin(me):
        raise HTTPException(status_code=403, detail="Only a super admin can manage groups")


def _group_read_floor(mem: DmGroupMember):
    return mem.last_read_at or mem.added_at


def _my_groups(db: Session, me: User) -> List[tuple]:
    """(group, my membership) for every live group the user belongs to."""
    return (
        db.query(DmGroup, DmGroupMember)
        .join(DmGroupMember, DmGroupMember.group_id == DmGroup.id)
        .filter(
            DmGroup.tenant_id == me.tenant_id,
            DmGroup.deleted_at.is_(None),
            DmGroupMember.user_id == me.id,
        )
        .all()
    )


def _group_unread(db: Session, me: User, mem: DmGroupMember) -> int:
    q = db.query(func.count(DirectMessage.id)).filter(
        DirectMessage.group_id == mem.group_id,
        DirectMessage.sender_id != me.id,
    )
    floor = _group_read_floor(mem)
    if floor is not None:
        q = q.filter(DirectMessage.created_at > floor)
    return int(q.scalar() or 0)


def _group_member_ids(db: Session, group_id) -> List[str]:
    rows = db.query(DmGroupMember.user_id).filter(DmGroupMember.group_id == group_id).all()
    return [str(r[0]) for r in rows]


def _last_group_message(db: Session, group_id):
    return (
        db.query(DirectMessage)
        .filter(DirectMessage.group_id == group_id)
        .order_by(DirectMessage.created_at.desc())
        .first()
    )


def _resolve_group(db: Session, me: User, group_id: str, *, manage: bool = False):
    """Return (group, my membership or None).

    Members get read/reply access. A super_admin may manage a group even if they
    are not in it — otherwise removing themselves would strand the group.
    """
    try:
        gid = uuid.UUID(str(group_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Group not found")
    group = (
        db.query(DmGroup)
        .filter(DmGroup.id == gid, DmGroup.tenant_id == me.tenant_id, DmGroup.deleted_at.is_(None))
        .first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    mem = (
        db.query(DmGroupMember)
        .filter(DmGroupMember.group_id == group.id, DmGroupMember.user_id == me.id)
        .first()
    )
    if manage:
        _require_group_admin(me)
    elif not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return group, mem


def _group_dict(db: Session, me: User, group: DmGroup, mem: Optional[DmGroupMember]) -> dict:
    last = _last_group_message(db, group.id)
    member_ids = _group_member_ids(db, group.id)
    return {
        "group_id": str(group.id),
        "name": group.name,
        "channel": "group",
        "member_count": len(member_ids),
        "member_ids": member_ids,
        "created_by": str(group.created_by),
        "can_manage": _is_group_admin(me),
        "last_message": last.body if last else None,
        "last_message_at": last.created_at.isoformat() if last and last.created_at else None,
        "last_mine": (str(last.sender_id) == str(me.id)) if last else None,
        "unread": _group_unread(db, me, mem) if mem else 0,
    }


def _valid_member_ids(db: Session, me: User, ids: List[str]) -> List[uuid.UUID]:
    """Keep only active users of this tenant; always include the acting admin so
    a group is never created without someone who can see it."""
    wanted = set()
    for raw in ids or []:
        try:
            wanted.add(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    wanted.add(me.id)
    if not wanted:
        return []
    rows = (
        db.query(User.id)
        .filter(
            User.tenant_id == me.tenant_id,
            User.deleted_at.is_(None),
            User.status == "active",
            User.id.in_(list(wanted)),
        )
        .all()
    )
    return [r[0] for r in rows]


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), me: User = Depends(get_current_active_user)):
    """Groups the current user belongs to, newest activity first."""
    groups = [_group_dict(db, me, g, m) for g, m in _my_groups(db, me)]
    groups.sort(key=lambda g: (g["last_message_at"] or "", (g["name"] or "").lower()), reverse=True)
    return {"groups": groups, "self_id": str(me.id), "can_manage": _is_group_admin(me)}


@router.post("/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Create a group (super_admin only). The creator is always a member."""
    _require_group_admin(me)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")

    group = DmGroup(tenant_id=me.tenant_id, name=name, created_by=me.id)
    db.add(group)
    db.flush()
    for uid in _valid_member_ids(db, me, payload.member_ids):
        db.add(DmGroupMember(group_id=group.id, user_id=uid))
    db.commit()
    db.refresh(group)

    mem = (
        db.query(DmGroupMember)
        .filter(DmGroupMember.group_id == group.id, DmGroupMember.user_id == me.id)
        .first()
    )
    return {"group": _group_dict(db, me, group, mem)}


@router.patch("/groups/{group_id}")
def update_group(
    group_id: str, payload: GroupUpdate,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Rename a group and/or replace its membership (super_admin only)."""
    group, _mem = _resolve_group(db, me, group_id, manage=True)

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Group name is required")
        group.name = name

    if payload.member_ids is not None:
        keep = {str(u) for u in _valid_member_ids(db, me, payload.member_ids)}
        existing = {
            str(m.user_id): m
            for m in db.query(DmGroupMember).filter(DmGroupMember.group_id == group.id).all()
        }
        for uid, row in existing.items():
            if uid not in keep:
                db.delete(row)
        for uid in keep - set(existing):
            db.add(DmGroupMember(group_id=group.id, user_id=uuid.UUID(uid)))

    db.commit()
    db.refresh(group)
    mem = (
        db.query(DmGroupMember)
        .filter(DmGroupMember.group_id == group.id, DmGroupMember.user_id == me.id)
        .first()
    )
    return {"group": _group_dict(db, me, group, mem)}


@router.delete("/groups/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Soft-delete a group (super_admin only). History is kept, not shown."""
    group, _mem = _resolve_group(db, me, group_id, manage=True)
    group.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"deleted": True, "group_id": str(group.id)}


@router.get("/groups/{group_id}")
def get_group_thread(
    group_id: str,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Full group history + members; moves this member's read watermark to now."""
    group, mem = _resolve_group(db, me, group_id)

    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.group_id == group.id)
        .order_by(DirectMessage.created_at.asc())
        .all()
    )
    names = {
        str(u.id): {"name": _name(u), "tag": _ROLE_LABELS.get(u.role, (u.role or "").title())}
        for u in db.query(User).filter(User.tenant_id == me.tenant_id).all()
    }
    members = [
        {"user_id": uid, "name": names.get(uid, {}).get("name", "User"), "tag": names.get(uid, {}).get("tag", "")}
        for uid in _group_member_ids(db, group.id)
    ]

    out = []
    for m in messages:
        d = _msg_dict(m, me.id)
        d["group_id"] = str(group.id)
        d["sender_name"] = names.get(str(m.sender_id), {}).get("name", "User")
        out.append(d)

    if mem:
        mem.last_read_at = datetime.now(timezone.utc)
        db.commit()

    return {
        "group": {
            "group_id": str(group.id), "name": group.name, "channel": "group",
            "member_count": len(members), "can_manage": _is_group_admin(me),
            "member_ids": [m["user_id"] for m in members],
        },
        "members": members,
        "self_id": str(me.id),
        "messages": out,
    }


@router.post("/groups/{group_id}/send", status_code=status.HTTP_201_CREATED)
async def send_group_message(
    group_id: str, payload: SendMessage,
    db: Session = Depends(get_db), me: User = Depends(get_current_active_user),
):
    """Post to a group — ANY member may reply, not just the super_admin."""
    group, mem = _resolve_group(db, me, group_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Message body is required")

    msg = DirectMessage(tenant_id=me.tenant_id, sender_id=me.id, group_id=group.id, body=body)
    db.add(msg)
    if mem:
        # Sending is reading — don't let my own message age into my unread count.
        mem.last_read_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg)

    payload_out = {
        "id": str(msg.id),
        "group_id": str(group.id),
        "group_name": group.name,
        "sender_id": str(me.id),
        "sender_name": _name(me),
        "body": msg.body,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
    # Fan out to every member except the sender (whose UI already shows it
    # optimistically — an echo would duplicate the bubble, as in the 1:1 path).
    for uid in _group_member_ids(db, group.id):
        if uid == str(me.id):
            continue
        try:
            await emit_to_user_room(uid, "inapp_message", payload_out)
        except Exception:
            pass

    out = _msg_dict(msg, me.id)
    out["group_id"] = str(group.id)
    out["sender_name"] = _name(me)
    return {"message": out}
