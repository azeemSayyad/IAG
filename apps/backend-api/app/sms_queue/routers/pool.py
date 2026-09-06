"""SMS pool — direct CSV uploads (no SMS sent).

Mounted under /api/v1/sms/pool. An admin uploads a list and the rows go
straight into the agent pool as QUEUED; agents work them by phone. Upload /
remove are admin-only; listing is open to managers so the SMS Manager board can
show the batches.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_role
from app.models.user import User
from app.realtime.websocket import emit_to_agent, emit_to_tenant
from app.sms_queue.services import pool_ingest

router = APIRouter(prefix="/sms/pool", tags=["sms-pool"])

_require_admin = require_role("tenant_admin", "super_admin")
_require_manager = require_role("manager", "head", "tenant_admin", "admin", "super_admin")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


async def _flush(events: list[dict]) -> None:
    for e in events:
        to, _id, event, data = e["to"], e["id"], e["event"], e["data"]
        if to == "agent" and _id:
            await emit_to_agent(_id, event, data)
        elif to == "tenant" and _id:
            await emit_to_tenant(_id, event, data)


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = Form(None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    """Upload one CSV straight into the agent pool. Nothing is texted."""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (15 MB max)")
    try:
        content = raw.decode("utf-8-sig")  # strips a leading BOM (Excel / Sheets exports)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    result, events = pool_ingest.ingest_csv(
        db, tenant_id, content, name=(name or file.filename or "upload.csv"), uploaded_by=str(user.id)
    )
    if not result.get("ok"):
        first = (result.get("errors") or [{}])[0].get("error")
        detail = first or "No leads were added — check the file has a name and phone column."
        s = result.get("summary")
        if s and s.get("total_rows"):
            detail = (
                f"No leads were added from {s['total_rows']} rows "
                f"({s['skipped_duplicates']} already in the pool, {s['skipped_dnc']} on Do-Not-Call, "
                f"{s['failed']} with no usable name/phone)."
            )
        raise HTTPException(status_code=400, detail=detail)
    await _flush(events)
    return result


@router.get("/batches")
def batches(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
) -> dict:
    return pool_ingest.list_batches(db, tenant_id, limit)


@router.delete("/batches/{batch_id}")
async def remove_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_admin),
):
    """Pull a batch's un-worked leads back out of the pool."""
    data, events = pool_ingest.delete_batch(db, tenant_id, batch_id)
    if not data.get("ok"):
        raise HTTPException(status_code=404, detail="Batch not found")
    await _flush(events)
    return data
