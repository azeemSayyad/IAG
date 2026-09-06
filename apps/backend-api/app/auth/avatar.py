"""
Profile photos — stored in S3 through the shared `app.calls.s3_storage` client
(the same bucket as training videos, deal recordings and consent forms).

The frontend still sends a small base64 data URL to PATCH /auth/me; this module
decodes it, uploads the bytes to `avatars/<tenant>/<user>/<uuid>.<ext>` and keeps
only the object key on the user row. Reads resolve the key to a time-limited
signed URL, so every consumer (settings page, topbar, SPA shell, admin user
list) keeps using `avatar_url` as a plain <img src>.

When S3 isn't configured the data URL is kept inline in `avatar_url` exactly as
before, so nothing breaks on a deploy without bucket credentials.
"""
from __future__ import annotations

import base64
import logging
from uuid import uuid4

from fastapi import HTTPException

from app.calls.s3_storage import s3_storage
from app.models.user import User
from app.schemas.auth import UserResponse

logger = logging.getLogger(__name__)

# Signed URLs get cached in the browser (localStorage.ebAvatar) between page
# loads; every page refetches /auth/me on load, so a long expiry just avoids a
# broken image for someone returning after a while. 7 days is the SigV4 max.
SIGNED_URL_TTL = 7 * 24 * 3600

MAX_DATA_URL_LEN = 1_500_000
_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def _decode_data_url(av: str) -> tuple[bytes, str, str]:
    """'data:image/jpeg;base64,....' -> (bytes, content_type, ext)."""
    if not av.startswith("data:image/"):
        raise HTTPException(status_code=422, detail="avatar_url must be an image data URL")
    if len(av) > MAX_DATA_URL_LEN:
        raise HTTPException(status_code=413, detail="Image too large — please use a smaller photo")
    header, sep, payload = av.partition(",")
    if not sep or ";base64" not in header:
        raise HTTPException(status_code=422, detail="avatar_url must be a base64 image data URL")
    content_type = header[5:].split(";", 1)[0].strip().lower() or "image/jpeg"
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="avatar_url is not valid base64")
    if not raw:
        raise HTTPException(status_code=422, detail="Empty image")
    return raw, content_type, _EXT.get(content_type, "jpg")


def _delete_object(bucket: str | None, key: str | None) -> None:
    """Best-effort removal of a previous S3 object (never blocks the request)."""
    if not key:
        return
    try:
        if s3_storage.configured():
            s3_storage._client().delete_object(Bucket=bucket or s3_storage.bucket, Key=key)
    except Exception:
        logger.warning("Could not delete old avatar s3://%s/%s", bucket, key)


def store_avatar(user: User, data_url: str) -> str:
    """Persist a new photo for `user`. Returns 's3' or 'db' (where it went).

    Mutates the row; the caller commits.
    """
    raw, content_type, ext = _decode_data_url(data_url.strip())
    old_bucket, old_key = user.avatar_s3_bucket, user.avatar_s3_key
    if s3_storage.configured():
        try:
            key = f"avatars/{user.tenant_id}/{user.id}/{uuid4()}.{ext}"
            out = s3_storage.upload_bytes(raw, key, content_type=content_type)
            user.avatar_s3_bucket, user.avatar_s3_key = out["bucket"], out["key"]
            user.avatar_url = None
            _delete_object(old_bucket, old_key)
            return "s3"
        except Exception:
            logger.exception("Avatar S3 upload failed (bucket=%s endpoint=%s); storing in DB",
                             s3_storage.bucket, s3_storage.endpoint_url)
    else:
        logger.warning("Avatar stored in DB: S3 is not configured (S3_BUCKET/AWS creds missing)")
    _delete_object(old_bucket, old_key)
    user.avatar_s3_bucket = user.avatar_s3_key = None
    user.avatar_url = data_url.strip()
    return "db"


def clear_avatar(user: User) -> None:
    _delete_object(user.avatar_s3_bucket, user.avatar_s3_key)
    user.avatar_s3_bucket = user.avatar_s3_key = None
    user.avatar_url = None


def migrate_inline_avatar(user: User) -> bool:
    """Move a legacy inline data-URL photo to S3. Returns True if the row changed."""
    av = user.avatar_url or ""
    if user.avatar_s3_key or not av.startswith("data:image/") or not s3_storage.configured():
        return False
    try:
        return store_avatar(user, av) == "s3"
    except HTTPException:
        return False


def resolve_avatar_url(user: User) -> str | None:
    """What the browser should put in <img src>: a signed S3 URL, or the inline fallback."""
    if user.avatar_s3_key:
        try:
            url = s3_storage.signed_url(user.avatar_s3_key, expires_seconds=SIGNED_URL_TTL)
            if url:
                return url
        except Exception:
            logger.warning("Could not sign avatar URL for user %s", user.id)
    return user.avatar_url


def user_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user).model_copy(update={"avatar_url": resolve_avatar_url(user)})
