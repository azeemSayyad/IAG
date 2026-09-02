"""SMS Queue — agent workspace endpoints.

Mounted under /api/v1/sms/queue. The service layer returns (data, events);
this layer flushes the socket events via the realtime emit_to_* helpers.
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id, require_role
from app.models.user import User
from app.realtime.websocket import (
    emit_to_agent,
    emit_to_tenant,
    emit_to_user,
)
from app.sms_queue.services import lead_ingest, queue_service

router = APIRouter(prefix="/sms/queue", tags=["sms-queue"])


async def _flush(events: list[dict]) -> None:
    for e in events:
        to, _id, event, data = e["to"], e["id"], e["event"], e["data"]
        if not _id and to != "tenant":
            continue
        if to == "agent":
            await emit_to_agent(_id, event, data)
        elif to == "user":
            await emit_to_user(_id, event, data)
        elif to == "tenant":
            await emit_to_tenant(_id, event, data)


# ---- Agent lifecycle ----

@router.post("/join")
async def join(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    # Admins never work the queue — they can't join (and so never get offered leads).
    if (user.role or "").lower() in queue_service.ADMIN_ROLES:
        return {"status": "OFFLINE", "blocked": "admin"}
    data, events = queue_service.join(db, tenant_id, str(user.id))
    await _flush(events)
    return data


@router.post("/leave")
async def leave(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.leave(db, tenant_id, str(user.id))
    await _flush(events)
    return data


@router.post("/wrap")
def wrap(
    body: dict = Body(default={}),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Wrap-up presence: the Add Deal form heartbeats here while an agent fills it
    (after-call work). Display-only for the Agent Availability 'Wrapping' KPI — it
    does NOT change queue status, lead assignment, or the send path (lockdown intact)."""
    from app.sms_queue.services.wrap_presence import mark_wrapping
    active = bool(body.get("active", True))
    mark_wrapping(str(tenant_id), str(user.id), active)
    return {"ok": True, "active": active}


@router.post("/break")
async def set_break(
    on_break: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.set_break(db, tenant_id, str(user.id), on_break)
    await _flush(events)
    return data


@router.post("/break/start")
async def break_start(
    reason: str = Body("Other", embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.start_break(db, tenant_id, str(user.id), reason)
    await _flush(events)
    return data


@router.post("/break/end")
async def break_end(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.end_break(db, tenant_id, str(user.id))
    await _flush(events)
    return data


# ---- Lead actions ----

@router.post("/accept/{lead_id}")
async def accept(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.accept(db, tenant_id, str(user.id), lead_id)
    await _flush(events)
    return data


@router.post("/pass/{lead_id}")
async def pass_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.pass_lead(db, tenant_id, str(user.id), lead_id)
    await _flush(events)
    return data


@router.post("/send/{lead_id}")
async def send_message(
    lead_id: str,
    body: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.send_message(db, tenant_id, str(user.id), lead_id, body)
    await _flush(events)
    return data


@router.post("/disposition/{lead_id}")
async def disposition(
    lead_id: str,
    disposition: str = Body(..., embed=True),
    callback_time: str | None = Body(None, embed=True),
    appointment_time: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    data, events = queue_service.disposition(
        db, tenant_id, str(user.id), lead_id, disposition, callback_time, appointment_time
    )
    await _flush(events)
    return data


# ---- Queries ----

@router.post("/ingest")
async def ingest(
    since_hours: int = Body(168, embed=True),
    limit: int = Body(200, embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(require_role("manager", "head", "tenant_admin", "admin", "super_admin")),
):
    """Bridge real Sinch-fed inbound leads into the SMS queue. Manager/admin only."""
    result = lead_ingest.ingest_inbound_leads(db, tenant_id, since_hours, limit)
    if result.get("ingested"):
        await emit_to_tenant(tenant_id, "sms:queue_updated", {"reason": "ingested"})
    return result


@router.get("/status")
def status(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return queue_service.get_status(db, tenant_id, str(user.id))


@router.get("/my-stats")
def my_stats(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return queue_service.get_my_stats(db, tenant_id, str(user.id))


@router.get("/current")
def current(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return queue_service.get_current(db, tenant_id, str(user.id))


@router.get("/my-leads")
def my_leads(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return queue_service.get_my_leads(db, tenant_id, str(user.id), limit)


@router.get("/conversation/{lead_id}")
def conversation(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return queue_service.get_conversation(db, tenant_id, lead_id)


@router.post("/dev/simulate-inbound/{lead_id}")
async def simulate_inbound(
    lead_id: str,
    body: str = Body("Yes, I'm interested", embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """DEV-only: simulate a customer reply. Disabled outside development."""
    if settings.APP_ENV not in ("development", "dev", "local"):
        return {"ok": False, "reason": "disabled"}
    data, events = queue_service.simulate_inbound(db, tenant_id, lead_id, body)
    await _flush(events)
    return data
