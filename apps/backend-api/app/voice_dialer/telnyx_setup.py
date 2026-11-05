"""Create (or refresh) everything the dialer needs in a Telnyx account.

    venv/bin/python -m app.voice_dialer.telnyx_setup            # create / update
    venv/bin/python -m app.voice_dialer.telnyx_setup --dry-run  # show what it would do

Idempotent — found by name, so a rerun only points the webhooks at the
current VOICE_CALLBACK_BASE_URL (e.g. a new ngrok address):
  * Outbound voice profile "IAG Dialer"  — US destinations only
  * TeXML application "IAG Dialer"       — customer calls, agent lines, inbound calls
  * Credential connection "IAG Agents"   — agents' browsers (SIP URI calls from
                                           our own account only)
  * Messaging profile "IAG Dialer"       — inbound texts -> /voice/telnyx/inbound-sms
  * Every number on the account is attached to the TeXML app + messaging profile.
Prints the .env lines to set.
"""
from __future__ import annotations

import secrets
import sys
from urllib.parse import urlencode

from app.core.config import settings
from app.voice_dialer import telnyx

NAME = "IAG Dialer"
AGENTS = "IAG Agents"


def _url(path: str) -> str:
    base = (settings.VOICE_CALLBACK_BASE_URL or "").rstrip("/")
    if not base:
        sys.exit("Set VOICE_CALLBACK_BASE_URL (the public https address of this API) first.")
    q = {"k": settings.VOICE_WEBHOOK_TOKEN} if settings.VOICE_WEBHOOK_TOKEN else {}
    return f"{base}/api/v1/voice/telnyx/{path}" + (f"?{urlencode(q)}" if q else "")


def _find(path: str, key: str, name: str) -> dict | None:
    rows = telnyx._request("GET", path, params={"page[size]": 250}).get("data") or []
    return next((r for r in rows if r.get(key) == name), None)


def _upsert(path: str, key: str, name: str, body: dict, dry: bool, create_only: dict | None = None) -> str:
    row = _find(path, key, name)
    if dry:
        print(f"  {'update' if row else 'create'} {path} {name!r}")
        return (row or {}).get("id") or "<new>"
    if row:
        telnyx._request("PATCH", f"{path}/{row['id']}", json=body)
        print(f"  updated {path} {name!r} ({row['id']})")
        return str(row["id"])
    row = telnyx._request("POST", path, json={key: name, **body, **(create_only or {})}).get("data") or {}
    print(f"  created {path} {name!r} ({row.get('id')})")
    return str(row["id"])


def main(dry: bool = False) -> None:
    if not (settings.TELNYX_API_KEY or "").strip():
        sys.exit("Set TELNYX_API_KEY first.")
    print("Telnyx setup — webhooks to", _url("answer").split("?")[0].rsplit("/", 1)[0])

    ovp = _upsert("/outbound_voice_profiles", "name", NAME,
                  {"traffic_type": "conversational", "whitelisted_destinations": ["US"]}, dry)
    app = _upsert("/texml_applications", "friendly_name", NAME, {
        "voice_url": _url("answer"), "voice_method": "post",
        "status_callback": _url("event"), "status_callback_method": "post",
        "outbound": {"outbound_voice_profile_id": ovp},
    }, dry)
    conn = _upsert("/credential_connections", "connection_name", AGENTS, {
        "sip_uri_calling_preference": "internal",  # only our own TeXML app may ring an agent
        "outbound": {"outbound_voice_profile_id": ovp},
    }, dry, create_only={"user_name": "iagagents" + secrets.token_hex(4), "password": secrets.token_urlsafe(24)})
    msg = _upsert("/messaging_profiles", "name", NAME, {
        "webhook_url": _url("inbound-sms"), "webhook_api_version": "2",
        "whitelisted_destinations": ["US"],
    }, dry)

    numbers = telnyx._request("GET", "/phone_numbers", params={"page[size]": 250}).get("data") or []
    for n in numbers:
        if dry:
            print(f"  attach {n.get('phone_number')}")
            continue
        telnyx._request("PATCH", f"/phone_numbers/{n['id']}", json={"connection_id": app})
        try:
            telnyx._request("PATCH", f"/phone_numbers/{n['id']}/messaging", json={"messaging_profile_id": msg})
        except telnyx.TelnyxError as exc:
            print(f"  ! {n.get('phone_number')}: messaging not attached ({exc})")
        print(f"  attached {n.get('phone_number')}")
    if not numbers:
        print("  (no phone numbers on the account yet — rerun after buying one)")

    print("\nPut these in apps/backend-api/.env:")
    print(f"TELNYX_TEXML_APP_ID={app}")
    print(f"TELNYX_CREDENTIAL_CONNECTION_ID={conn}")
    if numbers:
        print("TELNYX_FROM_NUMBERS=" + ",".join(str(n.get("phone_number")) for n in numbers))


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv)
