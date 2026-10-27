"""Thin Vonage Voice API client (REST + JWTs) for the auto dialer.

* Server JWT (RS256, application private key) authorises REST calls:
  create call, transfer, hang up, talk, recording download, users.
* Client JWT (same key, with `sub` + `acl`) lets an agent's browser log in
  with the Vonage Client SDK and hold their open headset line.
* Webhooks are trusted when they carry a valid signed JWT (signature secret)
  or our own ?k= token.

SIMULATION: with no credentials, outside production and VONAGE_SIMULATE on,
every REST call is faked (no network) so the whole flow can be exercised
locally through the /voice/dialer/sim/* endpoints. Production never simulates.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import httpx
import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(6.0, connect=3.0)
_known_users: set[str] = set()


def agent_username(user_id) -> str:
    """Stable Vonage user name for a portal user (no PII)."""
    return "iag_" + str(user_id).replace("-", "")


def user_id_from_username(name: str | None) -> str | None:
    n = (name or "").strip()
    if not n.startswith("iag_") or len(n) != 36:
        return None
    h = n[4:]
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _private_key() -> str:
    pem = (settings.VONAGE_PRIVATE_KEY or "").strip()
    if pem:
        return pem.replace("\\n", "\n")
    path = (settings.VONAGE_PRIVATE_KEY_PATH or "").strip()
    if path:
        try:
            with open(path) as fh:
                return fh.read()
        except OSError as exc:
            logger.warning("VONAGE_PRIVATE_KEY_PATH unreadable: %s", exc)
    return ""


def configured() -> bool:
    return bool((settings.VONAGE_APPLICATION_ID or "").strip() and _private_key())


def simulating() -> bool:
    return (
        not configured()
        and bool(settings.VONAGE_SIMULATE)
        and (settings.APP_ENV or "").lower() != "production"
    )


def available() -> bool:
    """Can the dialer place (real or simulated) calls at all?"""
    return configured() or simulating()


def _base() -> str:
    return (settings.VONAGE_API_BASE_URL or "https://api.nexmo.com").rstrip("/")


def _jwt(extra: dict[str, Any] | None = None, ttl: int = 300) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "application_id": settings.VONAGE_APPLICATION_ID.strip(),
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    claims.update(extra or {})
    token = jwt.encode(claims, _private_key(), algorithm="RS256")
    return token.decode() if isinstance(token, bytes) else token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt()}", "Content-Type": "application/json"}


class VonageError(RuntimeError):
    pass


# One pooled, keep-alive client: a fresh TLS handshake per request cost ~0.8 s
# each (3 requests per bridge), straight onto the customer's wait.
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT, limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120))
    return _client


def warm() -> None:
    """Open (or refresh) the pooled connection in the background."""
    if simulating() or not configured():
        return
    import threading

    def go():
        try:
            _http().get(_base() + "/v1/calls?page_size=1", headers=_headers())
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = _base() + path
    try:
        r = _http().request(method, url, json=body, headers=_headers())
    except httpx.HTTPError as exc:
        raise VonageError(f"{method} {path}: {exc}") from exc
    if r.status_code >= 400:
        raise VonageError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


# ---- Agent (in-app) identity ---------------------------------------------

ACL = {
    "paths": {
        "/*/users/**": {}, "/*/conversations/**": {}, "/*/sessions/**": {},
        "/*/devices/**": {}, "/*/image/**": {}, "/*/media/**": {},
        "/*/applications/**": {}, "/*/push/**": {}, "/*/knocking/**": {},
        "/*/legs/**": {},
    }
}


def ensure_user(username: str, display_name: str | None = None) -> None:
    """Create the Vonage user once (a duplicate name is fine)."""
    if simulating() or username in _known_users:
        return
    try:
        _request("POST", "/v1/users", {"name": username, "display_name": display_name or username})
    except VonageError as exc:
        msg = str(exc).lower()
        if "409" not in msg and "duplicate" not in msg and "already" not in msg:
            raise
    _known_users.add(username)


def client_token(username: str, ttl_seconds: int = 8 * 3600) -> str:
    """JWT the browser's Vonage Client SDK logs in with."""
    if simulating():
        return "sim-token"
    return _jwt({"sub": username, "acl": ACL}, ttl=ttl_seconds)


# ---- Calls -----------------------------------------------------------------

def create_call(*, to: str, from_: str, answer_url: str, event_url: str, ring_seconds: int) -> str:
    """Dial a customer with machine detection on. Returns the call leg uuid."""
    if simulating():
        return "sim-" + uuid.uuid4().hex
    body = {
        "to": [{"type": "phone", "number": to}],
        "from": {"type": "phone", "number": from_},
        "answer_url": [answer_url],
        "answer_method": "POST",
        "event_url": [event_url],
        "event_method": "POST",
        "ringing_timer": int(ring_seconds),
    }
    if settings.VONAGE_MACHINE_DETECTION:
        # Fast detection: speed beats accuracy (a slow, "accurate" setting puts
        # dead air on the line and the customer hangs up). `continue` lets us
        # log the machine result and hang up ourselves.
        body["advanced_machine_detection"] = {"behavior": "continue", "mode": "detect", "beep_timeout": 45}
    data = _request("POST", "/v1/calls", body)
    leg = data.get("uuid")
    if not leg:
        raise VonageError(f"create call: no uuid in {data}")
    return leg


def transfer(call_uuid: str, ncco: list[dict]) -> None:
    if simulating() or str(call_uuid).startswith("sim-"):
        return
    _request("PUT", f"/v1/calls/{call_uuid}",
             {"action": "transfer", "destination": {"type": "ncco", "ncco": ncco}})


def hangup(call_uuid: str | None) -> None:
    if not call_uuid or simulating() or str(call_uuid).startswith("sim-"):
        return
    try:
        _request("PUT", f"/v1/calls/{call_uuid}", {"action": "hangup"})
    except VonageError as exc:
        logger.info("vonage hangup %s: %s", call_uuid, exc)


def talk(call_uuid: str, text: str) -> None:
    if simulating() or str(call_uuid).startswith("sim-"):
        return
    try:
        _request("PUT", f"/v1/calls/{call_uuid}/talk", {"text": text, "language": "en-US"})
    except VonageError as exc:
        logger.info("vonage talk %s: %s", call_uuid, exc)


def download(url: str) -> bytes:
    if url.startswith("sim://"):
        from app.voice_dialer.simulator import fake_audio

        return fake_audio()
    r = httpx.get(url, headers={"Authorization": f"Bearer {_jwt()}"}, timeout=httpx.Timeout(60.0))
    r.raise_for_status()
    return r.content


# ---- Webhook trust -----------------------------------------------------------

def webhook_trusted(headers, body: bytes, k: str | None) -> bool:
    """A webhook is trusted if Vonage signed it, or it carries our ?k= token.

    With neither secret configured, only a simulating (non-production) server
    accepts webhooks at all."""
    token = (settings.VONAGE_WEBHOOK_TOKEN or "").strip()
    if token and k and hmac.compare_digest(token, k):
        return True
    secret = (settings.VONAGE_SIGNATURE_SECRET or "").strip()
    auth = headers.get("authorization") or ""
    if secret and auth.lower().startswith("bearer "):
        try:
            claims = jwt.decode(auth[7:].strip(), secret, algorithms=["HS256"],
                                options={"verify_aud": False})
            want = claims.get("payload_hash")
            if not want or not body:
                return True
            return hmac.compare_digest(want, hashlib.sha256(body).hexdigest())
        except jwt.PyJWTError:
            return False
    return not token and not secret and simulating()
