"""Master/partner API-key authentication.

Generation, hashing, verification, and a combined JWT-or-API-key dependency that
plugs in beside the existing JWT auth. ADDITIVE: existing JWT callers are never
affected — the API-key path is only ever exercised when the caller presents an
``ek_``-prefixed token (Authorization: Bearer / X-API-Key). A JWT request never
touches the api_keys table.

Verification: read token -> hash (SHA-256) -> look up by prefix -> constant-time
compare full hash -> reject revoked/expired (401) -> scope check (403, master '*'
passes all) -> per-key rate limit (429) -> set tenant context from the key.
"""
import hashlib
import hmac
import logging
import secrets
import string
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db

logger = logging.getLogger("apikey")

_B62 = string.ascii_uppercase + string.ascii_lowercase + string.digits
# 'ek_live_' is a constant 8 chars, useless as a lookup discriminator, so the stored
# prefix is 12 chars ('ek_live_' + 4 random) — enough to narrow the lookup; the full
# SHA-256 hash is what actually authenticates.
PREFIX_LEN = 12


def _b62(nbytes: int) -> str:
    num = int.from_bytes(secrets.token_bytes(nbytes), "big")
    out = []
    while num:
        num, r = divmod(num, 62)
        out.append(_B62[r])
    return "".join(out) or "0"


def generate_api_key(env: str = "live") -> str:
    """``ek_<env>_<~43 base62 chars>`` from 32 random bytes (>=256 bits entropy)."""
    return f"ek_{env}_{_b62(32)}"


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def extract_token(request: Request):
    """Bearer token (Authorization header) or X-API-Key value, or None."""
    authz = request.headers.get("authorization", "")
    if authz[:7].lower() == "bearer ":
        tok = authz[7:].strip()
        if tok:
            return tok
    xkey = request.headers.get("x-api-key", "")
    return xkey.strip() or None


def resolve_api_key(token, db: Session):
    """If ``token`` is a valid active API key, return its ApiKey row.
    If it is NOT api-key-shaped (no ``ek_`` prefix), return None so the JWT path
    can handle it. If it IS api-key-shaped but invalid/revoked/expired, raise 401.
    """
    if not token or not token.startswith("ek_"):
        return None
    from app.models.api_key import ApiKey

    h = hash_key(token)
    rows = db.query(ApiKey).filter(ApiKey.key_prefix == token[:PREFIX_LEN]).all()
    match = next((r for r in rows if hmac.compare_digest(r.key_hash, h)), None)
    if match is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    if match.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key revoked")
    if match.expires_at and match.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")
    return match


def _key_has_scope(key, scope: str) -> bool:
    scopes = key.scopes or []
    return key.tier == "master" or "*" in scopes or scope in scopes


def _enforce_rate_limit(key) -> None:
    try:
        from app.security.rate_limiting import check_rate_limit

        ok, info = check_rate_limit(
            "api_general", f"apikey:{key.key_prefix}",
            max_requests=key.rate_limit_per_min, window_seconds=60,
        )
    except HTTPException:
        raise
    except Exception:
        return  # Redis hiccup -> don't fail auth on the limiter
    if not ok:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API key rate limit exceeded",
            headers={"Retry-After": "60", "X-RateLimit-Limit": str(info.get("limit"))},
        )


def _mark_used(db, key, request) -> None:
    try:
        key.last_used_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
    logger.info("apikey %s %s %s 200", key.key_prefix, request.method, request.url.path)


def api_key_principal(request: Request, db: Session, scope: str):
    """Return the ApiKey if a valid key carrying ``scope`` is present, else None.
    Raises 401/403/429 for a present-but-bad key. On success sets tenant context."""
    key = resolve_api_key(extract_token(request), db)
    if key is None:
        return None
    if not _key_has_scope(key, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"API key missing scope '{scope}'")
    _enforce_rate_limit(key)
    from app.core.tenant import set_current_tenant_id

    request.state.tenant_id = str(key.tenant_id)
    set_current_tenant_id(str(key.tenant_id))
    _mark_used(db, key, request)
    return key


def jwt_or_api_key(scope: str, jwt_validator):
    """Dependency factory -> yields the tenant_id (str). Accepts EITHER a valid
    API key carrying ``scope`` (master '*' passes all) OR the existing JWT,
    validated by ``jwt_validator(user)`` (raises 403 if the user isn't allowed).
    Existing JWT behavior is preserved exactly; the key is purely additive.
    """
    def dependency(request: Request, db: Session = Depends(get_db)) -> str:
        token = extract_token(request)
        key = api_key_principal(request, db, scope)
        if key is not None:
            return str(key.tenant_id)
        # --- JWT fallback (mirrors get_current_user + active + the route's gate) ---
        from app.core.security import decode_token
        from app.core.tenant import set_current_tenant_id
        from app.models.user import User

        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
        user = db.query(User).filter(User.id == payload.get("sub"), User.deleted_at.is_(None)).first()
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        if user.status != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Inactive user")
        jwt_validator(user)
        request.state.tenant_id = str(user.tenant_id)
        set_current_tenant_id(str(user.tenant_id))
        return str(user.tenant_id)

    return dependency


# --- JWT validators mirroring each wired route's existing gate ---
def _validate_lead_read(user) -> None:
    from app.core.permissions import Permission, require_permission

    require_permission(Permission.LEAD_READ)(user)


# Mirrors sms_queue manager._require_manager = require_role(...) (+ "dev" passes all).
_MANAGER_ROLES = {"manager", "head", "tenant_admin", "admin", "super_admin", "dev"}


def _validate_manager(user) -> None:
    if user.role not in _MANAGER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")


# Ready-made dependencies for the wired routes:
lead_read_auth = jwt_or_api_key("lead:read", _validate_lead_read)
sms_send_auth = jwt_or_api_key("sms:send", _validate_manager)
