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
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.hs_auth import service
from app.hs_auth.sheet import SheetError, clean_rows, read_rows
from app.models.user import User

router = APIRouter(prefix="/hs-auth", tags=["hs-auth"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.HsAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


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


# ------------------------------------------------------------ extension only

@router.post("/jobs/{request_id}/report")
def report(request_id: str, data: dict = Body(...), db: Session = Depends(get_db),
           x_hs_job_token: str = Header(None)):
    return _call(service.report, db, request_id, x_hs_job_token, data)


@router.get("/jobs/{request_id}")
def job_state(request_id: str, db: Session = Depends(get_db), x_hs_job_token: str = Header(None)):
    return _call(service.job_state, db, request_id, x_hs_job_token)
