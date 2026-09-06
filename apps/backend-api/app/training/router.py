"""Agent training program — read for everyone signed in, edit for admin-class.

Steps are an ordered list per tenant. A step's video is either an external link
(Vimeo / YouTube / …) or a file uploaded here: S3 when configured (Railway
Buckets etc. via app.calls.s3_storage), inline DB bytes otherwise — the same
degrade-gracefully rule as deal recordings. The first time a tenant with no
steps at all opens the page, the default program is seeded.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.audit import log_audit_event
from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id, require_role
from app.core.security import decode_token
from app.models.training import TrainingStep
from app.models.user import User
from app.schemas.training import (
    TrainingReorder,
    TrainingStepCreate,
    TrainingStepResponse,
    TrainingStepUpdate,
)
from app.training.defaults import DEFAULT_STEPS

router = APIRouter(prefix="/training", tags=["training"])

_require_admin = require_role("tenant_admin", "super_admin", "admin")

_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv")
_VIDEO_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB — a long screen recording


def _audit(db: Session, tenant_id: str, user: User, action: str, step_id, details: dict) -> None:
    try:
        log_audit_event(
            tenant_id=tenant_id, action=action, resource_type="training_step",
            resource_id=str(step_id), user_id=str(user.id), details=details, db=db,
        )
    except Exception:
        db.rollback()


def _live(db: Session, tenant_id: str):
    return (
        db.query(TrainingStep)
        .filter(TrainingStep.tenant_id == tenant_id, TrainingStep.deleted_at.is_(None))
        .order_by(TrainingStep.position, TrainingStep.created_at)
    )


def _own(db: Session, tenant_id: str, step_id: UUID) -> TrainingStep:
    s = _live(db, tenant_id).filter(TrainingStep.id == step_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Training step not found")
    return s


def _renumber(steps: list[TrainingStep]) -> None:
    for i, s in enumerate(steps):
        if s.position != i:
            s.position = i


def _to_response(s: TrainingStep) -> TrainingStepResponse:
    out = TrainingStepResponse.model_validate(s)
    if s.video_kind == "upload":
        out.video_src = f"/api/v1/training/steps/{s.id}/video"
    return out


def _ensure_defaults(db: Session, tenant_id: str, user: User) -> None:
    # Any row at all (even soft-deleted) means the tenant has been seeded before.
    if db.query(TrainingStep.id).filter(TrainingStep.tenant_id == tenant_id).first():
        return
    for i, (title, description, video_url, content) in enumerate(DEFAULT_STEPS):
        db.add(TrainingStep(
            tenant_id=tenant_id, position=i, title=title, description=description,
            content=content, video_kind="link" if video_url else "none",
            video_url=video_url, created_by=user.id,
        ))
    db.commit()


# --- read -------------------------------------------------------------------

@router.get("/steps", response_model=list[TrainingStepResponse])
def list_steps(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _ensure_defaults(db, tenant_id, user)
    return [_to_response(s) for s in _live(db, tenant_id).all()]


# --- write (admin-class) ----------------------------------------------------

@router.post("/steps", response_model=TrainingStepResponse, status_code=status.HTTP_201_CREATED)
def create_step(
    payload: TrainingStepCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    steps = _live(db, tenant_id).all()
    s = TrainingStep(
        tenant_id=tenant_id, title=payload.title, description=payload.description,
        content=payload.content, video_kind="link" if payload.video_url else "none",
        video_url=payload.video_url, created_by=user.id,
    )
    pos = len(steps) if payload.position is None else min(payload.position, len(steps))
    steps.insert(pos, s)
    _renumber(steps)
    db.add(s)
    db.commit()
    db.refresh(s)
    _audit(db, tenant_id, user, "create", s.id, {"title": s.title, "position": s.position})
    return _to_response(s)


@router.patch("/steps/{step_id}", response_model=TrainingStepResponse)
def update_step(
    step_id: UUID,
    payload: TrainingStepUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    s = _own(db, tenant_id, step_id)
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        s.title = data["title"]
    if "description" in data:
        s.description = (data["description"] or "").strip() or None
    if "content" in data:
        s.content = (data["content"] or "").strip() or None
    if "video_url" in data:
        url = (data["video_url"] or "").strip()
        _clear_upload(s)
        s.video_kind = "link" if url else "none"
        s.video_url = url or None
    db.commit()
    db.refresh(s)
    _audit(db, tenant_id, user, "update", s.id, {"fields": sorted(data.keys())})
    return _to_response(s)


@router.delete("/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_step(
    step_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    s = _own(db, tenant_id, step_id)
    s.deleted_at = datetime.now(timezone.utc)
    db.flush()
    _renumber(_live(db, tenant_id).all())
    db.commit()
    _audit(db, tenant_id, user, "delete", s.id, {"title": s.title})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/steps/reorder", response_model=list[TrainingStepResponse])
def reorder_steps(
    payload: TrainingReorder,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    steps = _live(db, tenant_id).all()
    by_id = {s.id: s for s in steps}
    ordered = [by_id[i] for i in payload.ids if i in by_id]
    if len(ordered) != len(steps) or len(set(payload.ids)) != len(payload.ids):
        raise HTTPException(status_code=400, detail="ids must list every step exactly once")
    _renumber(ordered)
    db.commit()
    _audit(db, tenant_id, user, "reorder", None, {"order": [str(i) for i in payload.ids]})
    return [_to_response(s) for s in _live(db, tenant_id).all()]


# --- video upload / playback ------------------------------------------------

def _clear_upload(s: TrainingStep) -> None:
    if s.video_kind == "upload" and s.video_storage == "s3" and s.video_s3_key:
        try:
            from app.calls.s3_storage import s3_storage
            if s3_storage.configured():
                s3_storage._client().delete_object(Bucket=s.video_s3_bucket or s3_storage.bucket, Key=s.video_s3_key)
        except Exception:
            pass
    s.video_filename = s.video_content_type = None
    s.video_byte_size = 0
    s.video_storage = s.video_s3_bucket = s.video_s3_key = None
    s.video_data = None


@router.post("/steps/{step_id}/video", response_model=TrainingStepResponse)
async def upload_step_video(
    step_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    s = _own(db, tenant_id, step_id)
    fname = (file.filename or "video.mp4").strip()
    ctype = (file.content_type or "").lower()
    if not (ctype.startswith("video/") or fname.lower().endswith(_VIDEO_EXTS)):
        raise HTTPException(status_code=415, detail="Please upload a video file (mp4, webm, mov…).")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(raw) > _VIDEO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Video is too large (max 2 GB).")

    _clear_upload(s)
    s.video_kind = "upload"
    s.video_url = None
    s.video_filename = fname[:255]
    s.video_content_type = (ctype or "video/mp4")[:100]
    s.video_byte_size = len(raw)

    stored_to_s3 = False
    try:
        from app.calls.s3_storage import s3_storage
        if s3_storage.configured():
            ext = (fname.rsplit(".", 1)[-1] if "." in fname else "mp4").lower()[:8] or "mp4"
            key = f"training-videos/{tenant_id}/{uuid4()}.{ext}"
            out = s3_storage.upload_bytes(raw, key, content_type=s.video_content_type)
            s.video_storage, s.video_s3_bucket, s.video_s3_key = "s3", out["bucket"], out["key"]
            stored_to_s3 = True
    except Exception:
        stored_to_s3 = False
    if not stored_to_s3:
        s.video_storage, s.video_data = "db", raw

    db.commit()
    db.refresh(s)
    _audit(db, tenant_id, user, "upload_video", s.id, {"filename": fname, "bytes": len(raw), "storage": s.video_storage})
    return _to_response(s)


@router.delete("/steps/{step_id}/video", response_model=TrainingStepResponse)
def remove_step_video(
    step_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    s = _own(db, tenant_id, step_id)
    _clear_upload(s)
    s.video_kind = "none"
    s.video_url = None
    db.commit()
    db.refresh(s)
    _audit(db, tenant_id, user, "remove_video", s.id, {})
    return _to_response(s)


def _user_from_header_or_query(
    request: Request,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> User:
    # <video src> can't send an Authorization header, so playback also accepts
    # the access token as ?token=… (still the same JWT, same expiry).
    auth = request.headers.get("authorization") or ""
    raw = auth[7:] if auth.lower().startswith("bearer ") else (token or "")
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(raw)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = db.query(User).filter(User.id == payload.get("sub"), User.deleted_at.is_(None)).first()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.get("/steps/{step_id}/video")
def stream_step_video(
    step_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user_from_header_or_query),
):
    s = _own(db, str(user.tenant_id), step_id)
    if s.video_kind != "upload":
        raise HTTPException(status_code=404, detail="This step has no uploaded video")
    if s.video_storage == "s3" and s.video_s3_key:
        from app.calls.s3_storage import s3_storage
        url = s3_storage.signed_url(s.video_s3_key)
        if not url:
            raise HTTPException(status_code=404, detail="Video is unavailable")
        return RedirectResponse(url)

    data = bytes(s.video_data or b"")
    total = len(data)
    ctype = s.video_content_type or "video/mp4"
    base_headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}

    # Byte ranges so the player can seek (and Safari, which insists on 206).
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes=") and total:
        start_s, _, end_s = rng[6:].partition("-")
        try:
            start = int(start_s) if start_s else max(total - int(end_s), 0)
            end = min(int(end_s), total - 1) if (end_s and start_s) else total - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid range")
        if start > end or start >= total:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        chunk = data[start:end + 1]
        return StreamingResponse(
            iter([chunk]), status_code=206, media_type=ctype,
            headers={**base_headers, "Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(chunk))},
        )
    return Response(content=data, media_type=ctype, headers={**base_headers, "Content-Length": str(total)})
