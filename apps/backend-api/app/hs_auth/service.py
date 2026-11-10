"""HealthSherpa authorizations — the rules.

Statuses
  PENDING       uploaded, ready to start
  INVALID       the sheet row can't be searched (fix it in the portal)
  IN_PROGRESS   the extension is searching / adding the client
  LINK_SENT     HealthSherpa texted the client; waiting for them to authorize
  AUTHORIZED    done — the client authorized the agent for 2027
  NOT_FOUND     HealthSherpa found no application / no Plan Year 2027 row
  NEEDS_REVIEW  the extension stopped on purpose (several people, phone on file
                differs from the sheet, already in the agent's clients, …)
  EXPIRED       the client didn't authorize in time (after the allowed resends)
  FAILED        something unexpected (page changed, logged out, tab closed…)
  CANCELLED     the agent pressed Stop

Every Start press is an `hs_auth_runs` row; the daily limit counts those per
agent per Eastern day (env HS_AUTH_DAILY_LIMIT, default 20). An agent runs one
client at a time.

The extension never holds the agent's portal login. Start returns a random
job token (only its sha256 is stored); the extension reports with it, and it
dies when the job reaches a final status.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.core.active_agents import user_is_active
from app.hs_auth.sheet import STATES
from app.models.hs_auth import HsAuthBatch, HsAuthRequest, HsAuthRun
from app.models.user import User

ET = ZoneInfo("America/New_York")

ACTIVE = ("IN_PROGRESS", "LINK_SENT")
FINAL = ("AUTHORIZED", "NOT_FOUND", "NEEDS_REVIEW", "EXPIRED", "FAILED", "CANCELLED", "INVALID")
STARTABLE = ("PENDING", "NOT_FOUND", "NEEDS_REVIEW", "EXPIRED", "FAILED", "CANCELLED")
REPORTABLE = ACTIVE + FINAL
ALL_STATUSES = ("PENDING",) + ACTIVE + FINAL

MANAGER_ROLES = ("manager", "head", "tenant_admin", "admin", "super_admin", "dev")

# A job with no report for this long is treated as dead (browser closed, PC
# asleep…). The client link itself only lives 15 minutes, so 30 is generous.
STALE_AFTER = timedelta(minutes=30)
MAX_EVENTS = 60


class HsAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def daily_limit() -> int:
    try:
        return max(1, int(os.getenv("HS_AUTH_DAILY_LIMIT", "20")))
    except ValueError:
        return 20


def max_resends() -> int:
    try:
        return max(0, int(os.getenv("HS_AUTH_MAX_RESENDS", "1")))
    except ValueError:
        return 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_manager(user: User) -> bool:
    return (user.role or "") in MANAGER_ROLES


def _today_start_utc() -> datetime:
    local = _now().astimezone(ET)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def usage(db: Session, tenant_id, user_id) -> dict:
    used = (db.query(func.count(HsAuthRun.id))
            .filter(HsAuthRun.tenant_id == tenant_id, HsAuthRun.user_id == user_id,
                    HsAuthRun.created_at >= _today_start_utc())
            .scalar()) or 0
    limit = daily_limit()
    return {"used": int(used), "limit": limit, "left": max(0, limit - int(used))}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _event(row: HsAuthRequest, step: Optional[str], status: Optional[str], detail: Optional[str]) -> None:
    ev = list(row.events or [])
    ev.append({"at": _now().isoformat(), "step": step, "status": status, "detail": (detail or "")[:500]})
    row.events = ev[-MAX_EVENTS:]


def _set_status(row: HsAuthRequest, status: str, detail: Optional[str] = None, step: Optional[str] = None) -> None:
    now = _now()
    row.status = status
    row.status_detail = detail
    if step:
        row.step = step
    if status == "LINK_SENT" and not row.link_sent_at:
        row.link_sent_at = now
    if status == "AUTHORIZED":
        row.authorized_at = now
    if status in FINAL:
        row.finished_at = now
        row.job_token_hash = None
    row.updated_at = now
    _event(row, step, status, detail)


def sweep_stale(db: Session, tenant_id) -> None:
    """Jobs that stopped reporting become FAILED, so they can be started again."""
    cutoff = _now() - STALE_AFTER
    rows = (db.query(HsAuthRequest)
            .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.status.in_(ACTIVE),
                    func.coalesce(HsAuthRequest.last_report_at, HsAuthRequest.started_at) < cutoff)
            .all())
    for r in rows:
        _set_status(r, "FAILED", "The extension stopped reporting (browser closed or PC asleep). Press Start to try again.")
    if rows:
        db.commit()


# ---------------------------------------------------------------- upload

def create_batch(db: Session, tenant_id, user: User, name: str, cleaned: list[dict],
                 assign_to: Optional[str]) -> dict:
    # Agents' uploads are theirs; a manager may give the list to one agent or
    # leave it open to everyone (NULL).
    if not is_manager(user):
        assign_to = str(user.id)
    elif assign_to:
        target = db.query(User).filter(User.id == assign_to, User.tenant_id == tenant_id).first()
        if not target or not user_is_active(target):
            raise HsAuthError(400, "That agent wasn't found or is disabled.")

    existing = {
        (f.lower(), l.lower(), d, s)
        for f, l, d, s in db.query(HsAuthRequest.first_name, HsAuthRequest.last_name,
                                   HsAuthRequest.dob, HsAuthRequest.state)
        .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.deleted_at.is_(None),
                HsAuthRequest.status != "INVALID")
    }

    batch = HsAuthBatch(tenant_id=tenant_id, name=(name or "Upload")[:200], uploaded_by=user.id)
    db.add(batch)
    db.flush()

    added = invalid = dupes = 0
    seen = set()
    for c in cleaned:
        key = (c["first_name"].lower(), c["last_name"].lower(), c["dob"], c["state"])
        if not c["errors"] and (key in existing or key in seen):
            dupes += 1
            continue
        seen.add(key)
        row = HsAuthRequest(
            tenant_id=tenant_id, batch_id=batch.id, row_number=c["row_number"],
            first_name=c["first_name"], last_name=c["last_name"], dob=c["dob"], state=c["state"],
            phone=c["phone"], email=c["email"], assigned_to=assign_to,
            status="INVALID" if c["errors"] else "PENDING",
            status_detail="; ".join(c["errors"]) or None,
            events=[],
        )
        db.add(row)
        if c["errors"]:
            invalid += 1
        else:
            added += 1
    if added + invalid == 0:
        db.rollback()
        raise HsAuthError(400, f"All {dupes} clients in this file are already in the list.")
    batch.total_rows = added + invalid
    batch.invalid_rows = invalid
    db.commit()
    return {"batch_id": str(batch.id), "added": added, "invalid": invalid, "duplicates": dupes,
            "total_rows": len(cleaned)}


# ---------------------------------------------------------------- reading

def _visible(q, user: User):
    """Agents see their own rows and the open (unassigned) ones."""
    if is_manager(user):
        return q
    return q.filter(or_(HsAuthRequest.assigned_to == user.id, HsAuthRequest.assigned_to.is_(None)))


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.query(User.id, User.first_name, User.last_name, User.status, User.deleted_at).filter(User.id.in_(ids)).all()
    out = {}
    for uid, f, l, st, dl in rows:
        name = f"{f or ''} {l or ''}".strip() or "—"
        out[uid] = name if (st == "active" and dl is None) else f"{name} (disabled)"
    return out


def serialize(row: HsAuthRequest, names: dict, *, with_events: bool = False) -> dict:
    d = {
        "id": str(row.id),
        "batch_id": str(row.batch_id) if row.batch_id else None,
        "row_number": row.row_number,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "dob": row.dob.isoformat() if row.dob else None,
        "state": row.state,
        "phone": row.phone,
        "email": row.email,
        "assigned_to": str(row.assigned_to) if row.assigned_to else None,
        "assigned_name": names.get(row.assigned_to) if row.assigned_to else None,
        "started_by_name": names.get(row.started_by) if row.started_by else None,
        "status": row.status,
        "status_detail": row.status_detail,
        "step": row.step,
        "hs_app_id": row.hs_app_id,
        "link_sends": row.link_sends,
        "runs": row.runs,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "link_sent_at": row.link_sent_at.isoformat() if row.link_sent_at else None,
        "authorized_at": row.authorized_at.isoformat() if row.authorized_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if with_events:
        d["events"] = row.events or []
    return d


def list_requests(db: Session, tenant_id, user: User, *, status: Optional[str], agent: Optional[str],
                  batch: Optional[str], q: Optional[str], limit: int, offset: int) -> dict:
    sweep_stale(db, tenant_id)
    base = _visible(db.query(HsAuthRequest).filter(HsAuthRequest.tenant_id == tenant_id,
                                                   HsAuthRequest.deleted_at.is_(None)), user)
    if agent and is_manager(user):
        base = base.filter(HsAuthRequest.assigned_to.is_(None)) if agent == "open" else base.filter(HsAuthRequest.assigned_to == agent)
    if batch:
        base = base.filter(HsAuthRequest.batch_id == batch)
    if q:
        like = f"%{q.strip()}%"
        base = base.filter(or_(HsAuthRequest.first_name.ilike(like), HsAuthRequest.last_name.ilike(like),
                               (HsAuthRequest.first_name + " " + HsAuthRequest.last_name).ilike(like),
                               HsAuthRequest.phone.ilike(like), HsAuthRequest.hs_app_id.ilike(like)))
    counts = {s: 0 for s in ALL_STATUSES}
    for s, n in base.with_entities(HsAuthRequest.status, func.count(HsAuthRequest.id)).group_by(HsAuthRequest.status):
        counts[s] = n
    rows_q = base.filter(HsAuthRequest.status == status) if status and status != "ALL" else base
    total = rows_q.count()
    # Live jobs first, then ready-to-start, then everything else newest first.
    order = case({"IN_PROGRESS": 0, "LINK_SENT": 1, "PENDING": 2, "INVALID": 3},
                 value=HsAuthRequest.status, else_=9)
    rows = (rows_q.order_by(order, HsAuthRequest.updated_at.desc(), HsAuthRequest.row_number)
            .limit(limit).offset(offset).all())
    names = _names(db, {r.assigned_to for r in rows} | {r.started_by for r in rows})
    batches = (db.query(HsAuthBatch).filter(HsAuthBatch.tenant_id == tenant_id)
               .order_by(HsAuthBatch.created_at.desc()).limit(50).all())
    if not is_manager(user):
        visible_batches = {b for (b,) in _visible(db.query(HsAuthRequest.batch_id).filter(
            HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.deleted_at.is_(None)), user).distinct()}
        batches = [b for b in batches if b.id in visible_batches]
    return {
        "rows": [serialize(r, names) for r in rows],
        "total": total,
        "counts": counts,
        "usage": usage(db, tenant_id, user.id),
        "batches": [{"id": str(b.id), "name": b.name, "created_at": b.created_at.isoformat(),
                     "total_rows": b.total_rows, "invalid_rows": b.invalid_rows} for b in batches],
        "max_resends": max_resends(),
        "is_manager": is_manager(user),
    }


def get_request(db: Session, tenant_id, user: User, request_id: str) -> HsAuthRequest:
    row = (_visible(db.query(HsAuthRequest).filter(HsAuthRequest.tenant_id == tenant_id,
                                                   HsAuthRequest.id == request_id,
                                                   HsAuthRequest.deleted_at.is_(None)), user).first())
    if not row:
        raise HsAuthError(404, "Client not found.")
    return row


def detail(db: Session, tenant_id, user: User, request_id: str) -> dict:
    row = get_request(db, tenant_id, user, request_id)
    names = _names(db, {row.assigned_to, row.started_by})
    return serialize(row, names, with_events=True)


# ---------------------------------------------------------------- actions

def start(db: Session, tenant_id, user: User, request_id: str) -> dict:
    sweep_stale(db, tenant_id)
    row = get_request(db, tenant_id, user, request_id)
    if row.assigned_to and str(row.assigned_to) != str(user.id) and not is_manager(user):
        raise HsAuthError(403, "This client is assigned to another agent.")
    if row.status in ACTIVE:
        raise HsAuthError(409, "This client is already running.")
    if row.status == "AUTHORIZED":
        raise HsAuthError(409, "This client is already authorized.")
    if row.status not in STARTABLE:
        raise HsAuthError(409, "Fix this row first (Edit) — " + (row.status_detail or "it isn't valid."))

    mine = (db.query(HsAuthRequest)
            .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.started_by == user.id,
                    HsAuthRequest.status.in_(ACTIVE), HsAuthRequest.deleted_at.is_(None))
            .first())
    if mine:
        raise HsAuthError(409, f"Finish or stop {mine.first_name} {mine.last_name} first — one client at a time.")

    u = usage(db, tenant_id, user.id)
    if u["left"] <= 0:
        raise HsAuthError(429, f"Daily limit reached ({u['limit']} per day). It resets at midnight Eastern.")

    token = secrets.token_urlsafe(32)
    now = _now()
    row.job_token_hash = _hash(token)
    row.started_by = user.id
    row.started_at = now
    row.last_report_at = now
    row.link_sent_at = None
    row.authorized_at = None
    row.finished_at = None
    row.link_sends = 0
    row.hs_app_id = None
    row.runs = (row.runs or 0) + 1
    if not row.assigned_to:
        row.assigned_to = user.id  # taking an open row makes it yours
    _set_status(row, "IN_PROGRESS", "Starting…", "start")
    db.add(HsAuthRun(tenant_id=tenant_id, request_id=row.id, user_id=user.id))
    db.commit()

    return {
        "job": {
            "id": str(row.id),
            "token": token,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "dob": row.dob.strftime("%m/%d/%Y"),
            "dob_iso": row.dob.isoformat(),
            "state": row.state,
            "state_name": STATES.get(row.state or "", ""),
            "phone": row.phone,
            "email": row.email,
            "max_resends": max_resends(),
            "plan_year": 2027,
        },
        "usage": usage(db, tenant_id, user.id),
    }


def cancel(db: Session, tenant_id, user: User, request_id: str) -> dict:
    row = get_request(db, tenant_id, user, request_id)
    if row.status not in ACTIVE:
        raise HsAuthError(409, "This client isn't running.")
    if not is_manager(user) and str(row.started_by) != str(user.id):
        raise HsAuthError(403, "Only the agent running it (or a manager) can stop it.")
    _set_status(row, "CANCELLED", "Stopped from the portal.", "cancelled")
    db.commit()
    return serialize(row, _names(db, {row.assigned_to, row.started_by}))


def reset(db: Session, tenant_id, user: User, request_id: str) -> dict:
    """Put a finished row back to PENDING (e.g. client asked for a new link tomorrow)."""
    row = get_request(db, tenant_id, user, request_id)
    if row.status in ACTIVE:
        raise HsAuthError(409, "Stop it first.")
    if row.status == "INVALID":
        raise HsAuthError(409, "Fix the row with Edit instead.")
    _set_status(row, "PENDING", None, "reset")
    row.finished_at = None
    db.commit()
    return serialize(row, _names(db, {row.assigned_to, row.started_by}))


def update(db: Session, tenant_id, user: User, request_id: str, data: dict) -> dict:
    from app.hs_auth.sheet import clean_name, parse_dob, parse_email, parse_phone, parse_state

    row = get_request(db, tenant_id, user, request_id)
    if row.status in ACTIVE:
        raise HsAuthError(409, "Stop it before editing.")
    if row.status == "AUTHORIZED":
        raise HsAuthError(409, "An authorized client can't be edited.")
    if "first_name" in data:
        row.first_name = clean_name(data["first_name"])
    if "last_name" in data:
        row.last_name = clean_name(data["last_name"])
    errors = []
    if "dob" in data:
        d = parse_dob(data["dob"])
        if d is None:
            errors.append("Date of birth isn't a valid past date")
        row.dob = d
    if "state" in data:
        s = parse_state(data["state"])
        if s is None:
            errors.append("State isn't a US state")
        row.state = s
    if "phone" in data:
        raw = str(data["phone"] or "").strip()
        p = parse_phone(raw) if raw else None
        if raw and p is None:
            errors.append("Phone isn't a 10-digit US number")
        row.phone = p
    if "email" in data:
        row.email = parse_email(data["email"]) if str(data["email"] or "").strip() else None
    if "assigned_to" in data and is_manager(user):
        a = data["assigned_to"] or None
        if a:
            t = db.query(User).filter(User.id == a, User.tenant_id == tenant_id).first()
            if not t or not user_is_active(t):
                raise HsAuthError(400, "That agent wasn't found or is disabled.")
        row.assigned_to = a
    if not row.first_name:
        errors.append("First name is missing")
    if not row.last_name:
        errors.append("Last name is missing")
    if row.dob is None and "Date of birth isn't a valid past date" not in errors:
        errors.append("Date of birth is missing")
    if row.state is None and "State isn't a US state" not in errors:
        errors.append("State is missing")
    if errors:
        _set_status(row, "INVALID", "; ".join(errors), "edited")
        row.finished_at = None
    elif row.status == "INVALID" or any(k in data for k in ("first_name", "last_name", "dob", "state", "phone")):
        _set_status(row, "PENDING", None, "edited")
        row.finished_at = None
    db.commit()
    return serialize(row, _names(db, {row.assigned_to, row.started_by}))


def delete(db: Session, tenant_id, user: User, request_id: str) -> None:
    row = get_request(db, tenant_id, user, request_id)
    if not is_manager(user) and str(row.assigned_to) != str(user.id):
        raise HsAuthError(403, "You can only remove your own clients.")
    if row.status in ACTIVE:
        raise HsAuthError(409, "Stop it before removing.")
    row.deleted_at = _now()
    db.commit()


# ---------------------------------------------------------------- extension

def report(db: Session, request_id: str, token: str, data: dict) -> dict:
    """The extension's progress report. Auth = the job token from start()."""
    if not token:
        raise HsAuthError(401, "Missing job token.")
    row = db.query(HsAuthRequest).filter(HsAuthRequest.id == request_id).first()
    if not row or row.deleted_at is not None:
        raise HsAuthError(404, "Job not found.")
    if not row.job_token_hash:
        # Final already (stopped from the portal, timed out, …) — tell the
        # extension to stop rather than 401, so it can show why.
        return {"ok": False, "stop": True, "status": row.status}
    if not secrets.compare_digest(row.job_token_hash, _hash(token)):
        raise HsAuthError(401, "Bad job token.")

    step = (str(data.get("step") or "")[:40]) or None
    status = data.get("status")
    msg = str(data.get("detail") or "")[:500] or None
    now = _now()
    row.last_report_at = now
    if data.get("hs_app_id"):
        row.hs_app_id = str(data["hs_app_id"])[:40]
    if data.get("link_sent"):
        row.link_sends = (row.link_sends or 0) + 1
        row.link_sent_at = now  # each send gives the client a fresh 15 minutes

    if status:
        if status not in REPORTABLE:
            raise HsAuthError(400, "Unknown status.")
        _set_status(row, status, msg, step)
    else:
        if step:
            row.step = step
        if msg:
            row.status_detail = msg
        row.updated_at = now
        _event(row, step, None, msg)
    db.commit()
    return {"ok": True, "stop": row.status in FINAL, "status": row.status}


def job_state(db: Session, request_id: str, token: str) -> dict:
    """Lets the extension ask 'should I keep going?' (e.g. after Stop in the portal)."""
    row = db.query(HsAuthRequest).filter(HsAuthRequest.id == request_id).first()
    if not row:
        raise HsAuthError(404, "Job not found.")
    if not row.job_token_hash:
        return {"status": row.status, "stop": True}
    if not secrets.compare_digest(row.job_token_hash, _hash(token or "")):
        raise HsAuthError(401, "Bad job token.")
    return {"status": row.status, "stop": row.status in FINAL}
