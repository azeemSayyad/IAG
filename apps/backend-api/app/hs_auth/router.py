"""HealthSherpa authorizations — /api/v1/hs-auth.

Portal (signed-in) routes: upload a sheet, list, start / stop / reset / edit /
remove a client. Extension routes (/hs-auth/jobs/*) are authenticated ONLY by
the per-job token that /start hands out — the extension never sees the agent's
portal login.

Every route is a plain `def` (FastAPI runs it in the threadpool) except the
upload, which hands its DB work to the threadpool itself — DB code must never
block the event loop (see the dialer's QueuePool incident in CLAUDE.md).
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.hs_auth import campaigns, service
from app.hs_auth.sheet import SheetError, clean_rows, read_rows
from app.models.user import User

router = APIRouter(prefix="/hs-auth", tags=["hs-auth"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.HsAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except campaigns.CampaignError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = Form(None),
    assign_to: str = Form(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    fname = file.filename or "clients.csv"
    if not fname.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Upload an Excel (.xlsx) or CSV file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large — up to 5 MB.")

    def work():
        try:
            cleaned = clean_rows(read_rows(fname, raw))
        except SheetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception:
            raise HTTPException(status_code=400, detail="Couldn't read this file. Save it as .xlsx or .csv and try again.")
        if not cleaned:
            raise HTTPException(status_code=400, detail="No client rows found under the header row.")
        return _call(service.create_batch, db, tenant_id, user, name or fname, cleaned, assign_to or None)

    return await run_in_threadpool(work)


@router.get("/requests")
def list_requests(
    status: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    batch: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return _call(service.list_requests, db, tenant_id, user, status=status, agent=agent,
                 batch=batch, q=q, limit=limit, offset=offset)


@router.get("/requests/{request_id}")
def get_request(request_id: str, db: Session = Depends(get_db),
                tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    return _call(service.detail, db, tenant_id, user, request_id)


@router.post("/requests/{request_id}/start")
def start(request_id: str, db: Session = Depends(get_db),
          tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    return _call(service.start, db, tenant_id, user, request_id)


@router.post("/requests/{request_id}/cancel")
def cancel(request_id: str, db: Session = Depends(get_db),
           tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    return _call(service.cancel, db, tenant_id, user, request_id)


@router.post("/requests/{request_id}/reset")
def reset(request_id: str, db: Session = Depends(get_db),
          tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    return _call(service.reset, db, tenant_id, user, request_id)


@router.patch("/requests/{request_id}")
def update(request_id: str, data: dict = Body(...), db: Session = Depends(get_db),
           tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    return _call(service.update, db, tenant_id, user, request_id, data)


@router.delete("/requests/{request_id}")
def delete(request_id: str, db: Session = Depends(get_db),
           tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    _call(service.delete, db, tenant_id, user, request_id)
    return {"ok": True}


@router.get("/agents")
def agents(db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
           user: User = Depends(get_current_active_user)):
    """Who a manager can give a list to (enabled users)."""
    if not service.is_manager(user):
        return []
    from app.core.active_agents import active_users_query
    rows = active_users_query(db, tenant_id).order_by(User.first_name, User.last_name).all()
    return [{"id": str(u.id), "name": f"{u.first_name} {u.last_name}".strip(), "role": u.role} for u in rows]


# ------------------------------------------------- HealthSherpa text campaigns
# Separate from the Auto Dialer's text campaigns (app/hs_auth/campaigns.py).
# Viewing: manager-class (and the campaign's HealthSherpa agent); changing a
# campaign: super_admin (+dev). Auto mode: the campaign's own agent only.

def _viewer(db: Session, tenant_id: str, user: User) -> None:
    if campaigns.can_view(user):
        return
    from app.models.hs_auth import HsTextCampaign
    if db.query(HsTextCampaign.id).filter(HsTextCampaign.tenant_id == tenant_id,
                                          HsTextCampaign.hs_agent_id == user.id).first() is None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _manager_of_campaigns(user: User) -> None:
    if not campaigns.can_manage(user):
        raise HTTPException(status_code=403, detail="Only the owner (super admin) can change HealthSherpa campaigns.")


@router.get("/campaigns")
def campaigns_overview(db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
                       user: User = Depends(get_current_active_user)):
    _viewer(db, tenant_id, user)
    campaigns.tick(db, tenant_id)  # locally the page's poll is the heartbeat
    return campaigns.overview(db, tenant_id, user)


@router.post("/campaigns")
async def campaigns_create(
    file: UploadFile = File(...),
    name: str = Form(""),
    template: str = Form(""),
    hs_agent_id: str = Form(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _manager_of_campaigns(user)
    fname = file.filename or "clients.csv"
    if not fname.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Upload an Excel (.xlsx) or CSV file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large — up to 5 MB.")

    def work():
        try:
            c = _call(campaigns.create, db, tenant_id, user, name=name, template=template, file_name=fname,
                      raw=raw, hs_agent_id=hs_agent_id or None)
        except SheetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return campaigns.campaign_dict(c, campaigns._counts(db, [c.id])[c.id], service._names(db, {c.hs_agent_id}))

    return await run_in_threadpool(work)


@router.get("/campaigns/clients")
def campaigns_clients(
    campaign: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _viewer(db, tenant_id, user)
    return campaigns.clients(db, tenant_id, campaign=campaign or None, stage=stage or None, q=q,
                             limit=limit, offset=offset)


@router.get("/campaigns/clients/{request_id}")
def campaigns_client(request_id: str, db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
                     user: User = Depends(get_current_active_user)):
    _viewer(db, tenant_id, user)
    return _call(campaigns.client_detail, db, tenant_id, request_id)


@router.get("/campaigns/export")
def campaigns_export(
    campaign: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _viewer(db, tenant_id, user)
    body = campaigns.export_csv(db, tenant_id, campaign=campaign or None, stage=stage or None, q=q)
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="healthsherpa-clients.csv"'})


@router.post("/campaigns/sim/age")
def campaigns_sim_age(request_id: str = Body(..., embed=True), minutes: float = Body(..., embed=True),
                      db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
                      user: User = Depends(get_current_active_user)):
    """Local testing only (Telnyx simulator): move a client's clocks back."""
    _manager_of_campaigns(user)
    return _call(campaigns.sim_age, db, tenant_id, request_id, minutes)


@router.put("/campaigns/{campaign_id}")
def campaigns_update(campaign_id: str, data: dict = Body(...), db: Session = Depends(get_db),
                     tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    _manager_of_campaigns(user)
    c = _call(campaigns.find, db, tenant_id, campaign_id)
    _call(campaigns.update, db, c, data)
    return campaigns.campaign_dict(c, campaigns._counts(db, [c.id])[c.id], service._names(db, {c.hs_agent_id}))


@router.post("/campaigns/{campaign_id}/{action}")
def campaigns_action(campaign_id: str, action: str, db: Session = Depends(get_db),
                     tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    _manager_of_campaigns(user)
    fn = {"start": campaigns.start, "resume": campaigns.start, "pause": campaigns.pause,
          "stop": campaigns.stop}.get(action)
    if fn is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    c = _call(campaigns.find, db, tenant_id, campaign_id)
    _call(fn, db, c)
    if action in ("start", "resume"):
        campaigns.tick(db, tenant_id)  # first texts go now, not on the next beat
    return campaigns.campaign_dict(c, campaigns._counts(db, [c.id])[c.id], service._names(db, {c.hs_agent_id}))


@router.delete("/campaigns/{campaign_id}")
def campaigns_delete(campaign_id: str, db: Session = Depends(get_db),
                     tenant_id: str = Depends(get_tenant_id), user: User = Depends(get_current_active_user)):
    _manager_of_campaigns(user)
    c = _call(campaigns.find, db, tenant_id, campaign_id)
    _call(campaigns.delete, db, c)
    return {"ok": True}


# ---- auto mode: the HealthSherpa agent's portal tab asks for the next job

@router.post("/auto/next")
def auto_next(db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
              user: User = Depends(get_current_active_user)):
    return _call(campaigns.next_job, db, tenant_id, user)


@router.post("/auto/{request_id}/release")
def auto_release(request_id: str, db: Session = Depends(get_db), tenant_id: str = Depends(get_tenant_id),
                 user: User = Depends(get_current_active_user)):
    return _call(campaigns.release, db, tenant_id, user, request_id)


# ------------------------------------------------------------ extension only

@router.post("/jobs/{request_id}/report")
def report(request_id: str, data: dict = Body(...), db: Session = Depends(get_db),
           x_hs_job_token: str = Header(None)):
    return _call(service.report, db, request_id, x_hs_job_token, data)


@router.get("/jobs/{request_id}")
def job_state(request_id: str, db: Session = Depends(get_db), x_hs_job_token: str = Header(None)):
    return _call(service.job_state, db, request_id, x_hs_job_token)
