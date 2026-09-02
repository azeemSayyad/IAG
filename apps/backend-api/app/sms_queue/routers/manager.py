"""SMS Manager — read-only board endpoints.

Mounted under /api/v1/sms/manager. Manager/admin only.
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_role
from app.core.api_key import sms_send_auth
from app.models.user import User
from app.realtime.websocket import emit_to_agent, emit_to_tenant, emit_to_user
from app.sms_queue.services import manager_service, queue_service

router = APIRouter(prefix="/sms/manager", tags=["sms-manager"])

_require_manager = require_role("manager", "head", "tenant_admin", "admin", "super_admin")


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


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_overview(db, tenant_id)


@router.get("/engine-status")
def engine_status(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    """Capacity-pacing flags + per-state free-agent capacity + per-carrier fleet
    health/usage (multi-carrier failover) for the manager dashboard."""
    return manager_service.get_engine_status(db, tenant_id)


@router.post("/engine-flag")
def engine_flag(
    name: str = Body(..., embed=True),
    enabled: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    """Toggle a UI-controllable engine flag (capacity pacing / fatigue) live — the
    Redis override beats the env default until cleared. The first-template lockdown is
    NOT toggleable here."""
    from fastapi import HTTPException
    try:
        return manager_service.set_engine_flag(name, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/fleet-dashboard")
def fleet_dashboard(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    """Per-carrier today usage (sent / daily capacity) + a 'need new DID in X days'
    provisioning forecast. Read-only; records only a per-day rollup. Never sends."""
    return manager_service.get_fleet_dashboard()


@router.get("/carrier-lookup-test")
def carrier_lookup_test(
    number: str = Query(..., description="A destination number to test the lookup against"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    """Self-check for the number-lookup service: calls the CONFIGURED lookup live for one
    number and reports the carrier it returned + whether that's T-Mobile — so you can
    confirm the CARRIER_LOOKUP_* env vars work right after you set them. Read-only; does
    not cache, send, or touch the first-template lockdown."""
    from app.ai.services import carrier_lookup, carrier_caps
    from app.core.config import settings
    configured = bool((getattr(settings, "CARRIER_LOOKUP_URL", "") or "").strip())
    carrier = carrier_lookup.http_backend(number) if configured else ""
    return {
        "configured": configured,
        "method": getattr(settings, "CARRIER_LOOKUP_METHOD", "GET"),
        "field": getattr(settings, "CARRIER_LOOKUP_FIELD", "carrier"),
        "number": number,
        "carrier": carrier or None,
        "is_tmobile": carrier_caps.is_tmobile(carrier),
    }


@router.get("/queued")
def queued(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_queued(db, tenant_id, limit)


@router.get("/active")
def active(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_active(db, tenant_id, limit)


@router.get("/leaderboard")
def leaderboard(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_leaderboard(db, tenant_id, from_, to)


@router.get("/funnel")
def funnel(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_funnel(db, tenant_id, from_, to)


@router.get("/parked")
def parked(
    kind: str = Query("ATTEMPTED"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_parked(db, tenant_id, kind, limit)


@router.get("/manage-leads")
def manage_leads(
    category: str = Query(...),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_manage_leads(db, tenant_id, category, limit)


@router.get("/dropped-leads")
def dropped_leads(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_dropped_leads(db, tenant_id)


@router.get("/agent-activity")
def agent_activity(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_agent_activity(db, tenant_id, from_, to)


@router.get("/callbacks")
def callbacks(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_callbacks(db, tenant_id)


@router.get("/webhook-reliability")
def webhook_reliability(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_webhook_reliability(db, tenant_id)


@router.get("/pass-keep")
def pass_keep(
    period: str = Query("day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_pass_keep(db, tenant_id, period)


@router.get("/agent-dispositions")
def agent_dispositions(
    period: str = Query("day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_agent_dispositions(db, tenant_id, period)


@router.get("/yes-leads")
def yes_leads(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_restorable_yes(db, tenant_id, limit)


@router.get("/breaks-today")
def breaks_today(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_breaks_today(db, tenant_id)


@router.get("/daily-summary")
def daily_summary(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_daily_summary(db, tenant_id)


@router.get("/sold-tank")
def sold_tank(
    search: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_sold_tank(db, tenant_id, search, from_, to, limit)


@router.get("/polling")
def get_polling(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return queue_service.get_polling(db, tenant_id)


@router.post("/polling")
async def set_polling(
    enabled: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    data = queue_service.set_polling(db, tenant_id, enabled)
    await emit_to_tenant(tenant_id, "sms:queue_updated", {"reason": "polling"})
    return data


@router.post("/parked/{lead_id}/restore")
async def restore_parked(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.restore_parked(db, tenant_id, lead_id)
    await _flush(events)
    return data


@router.post("/bulk-delete")
async def bulk_delete(
    category: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.bulk_delete(db, tenant_id, category)
    await _flush(events)
    return data


@router.post("/send")
async def manager_send(
    to_number: str = Body(..., embed=True),
    message: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    # Accepts a manager+ JWT user OR a master/scoped API key (sms:send scope).
    tenant_id: str = Depends(sms_send_auth),
):
    data, events = queue_service.manager_send(db, tenant_id, to_number, message)
    await _flush(events)
    return data


@router.get("/pool-counts")
def pool_counts(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return manager_service.get_pool_counts(db, tenant_id)


# ---- Actions ----

@router.post("/reassign")
async def reassign(
    lead_id: str = Body(..., embed=True),
    agent_user_id: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.reassign(db, tenant_id, lead_id, agent_user_id)
    await _flush(events)
    return data


@router.post("/ping-agent")
async def ping_agent(
    lead_id: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.ping_agent(db, tenant_id, lead_id)
    await _flush(events)
    return data


@router.post("/disposition")
async def disposition_lead(
    lead_id: str = Body(..., embed=True),
    disposition: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """Mark a pool lead directly (Wrong Number / Unqualified) without an agent."""
    data, events = queue_service.manager_disposition(db, tenant_id, lead_id, disposition)
    await _flush(events)
    return data


@router.post("/assign-next")
async def assign_next(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.assign_next(db, tenant_id)
    await _flush(events)
    return data


@router.post("/rebroadcast")
async def rebroadcast(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.rebroadcast(db, tenant_id)
    await _flush(events)
    return data


@router.post("/distribute-all")
async def distribute_all(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.distribute_all(db, tenant_id)
    await _flush(events)
    return data


@router.post("/kick-all")
async def kick_all(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.kick_all(db, tenant_id)
    await _flush(events)
    return data


@router.delete("/lead/{lead_id}")
async def delete_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    data, events = queue_service.delete_lead(db, tenant_id, lead_id)
    await _flush(events)
    return data
