"""Thin Telnyx client for the auto dialer — TeXML calls, WebRTC agents, webhooks.

Three translations live here:

* Call scripts: the dialer builds carrier-neutral lists of steps (talk, stream,
  input, conversation, record, connect). to_texml() turns one into TeXML — a
  conversation is a <Conference>, input is a <Gather>, connect is a <Dial>.
  Swapping a live call's script (transfer) is a TeXML call update.
* Webhooks: Telnyx posts form fields (CallSid, CallStatus, AnsweredBy, Digits,
  RecordingUrl …). normalize() rewrites them into the dict webhooks.py reads
  (uuid, status, dtmf, recording_url, duration).
* Agents: a browser can't start a TeXML call, so we RING the agent's browser
  instead (Telnyx WebRTC, one telephony credential per agent) and it answers by
  itself — dial_browser().

SIMULATION: with no keys, outside production and VOICE_SIMULATE on, every REST
call is faked (no network) so the whole flow can be exercised locally through
the /voice/dialer/sim/* endpoints. Production never simulates.
"""
from __future__ import annotations

import base64
import hmac
import io
import logging
import re
import time
import uuid
import wave
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

SLUG = "telnyx"  # our webhook path segment: /api/v1/voice/telnyx/...
TIMEOUT = httpx.Timeout(8.0, connect=3.0)
SIP_DOMAIN = "sip.telnyx.com"
REPLAY_WINDOW_SECONDS = 300

_creds: dict[str, tuple[str, str]] = {}  # agent username -> (credential id, sip username)
_account_sid: str | None = None


class TelnyxError(RuntimeError):
    pass


# ---- Identity ----------------------------------------------------------------

def agent_username(user_id) -> str:
    """Stable credential name for a portal user (no PII)."""
    return "iag_" + str(user_id).replace("-", "")


def configured() -> bool:
    return bool((settings.TELNYX_API_KEY or "").strip() and (settings.TELNYX_TEXML_APP_ID or "").strip())


def simulating() -> bool:
    return (
        not configured()
        and bool(settings.VOICE_SIMULATE)
        and (settings.APP_ENV or "").lower() != "production"
    )


def available() -> bool:
    return configured() or simulating()


def webhook_token() -> str:
    return (settings.VOICE_WEBHOOK_TOKEN or "").strip()


def e164(number: str | None) -> str:
    """+15615550100 (our code keeps digits only)."""
    raw = str(number or "").strip()
    if raw.startswith("sip:"):
        return raw
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if digits else ""


# ---- HTTP --------------------------------------------------------------------------

_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=(settings.TELNYX_API_BASE_URL or "https://api.telnyx.com/v2").rstrip("/"),
            timeout=TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120),
        )
    return _client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.TELNYX_API_KEY.strip()}"}


def _request(method: str, path: str, *, json: dict | None = None, form: dict | None = None,
             params: dict | None = None, raw: bool = False):
    try:
        r = _http().request(method, path, json=json, data=form, params=params, headers=_auth())
    except httpx.HTTPError as exc:
        raise TelnyxError(f"{method} {path}: {exc}") from exc
    if r.status_code >= 400:
        raise TelnyxError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
    if raw:
        return r.text
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def warm() -> None:
    """Open (or refresh) the pooled connection in the background."""
    if not configured():
        return
    import threading

    def go():
        try:
            _http().get("/balance", headers=_auth())
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


def learn_account(data: dict) -> None:
    """Every TeXML webhook names the account; remember it for REST paths."""
    global _account_sid
    sid = data.get("AccountSid")
    if sid and not _account_sid:
        _account_sid = str(sid)


def account_sid() -> str:
    global _account_sid
    sid = (settings.TELNYX_ACCOUNT_SID or "").strip() or _account_sid
    if sid:
        return sid
    # Any telephony credential carries the account (user) id.
    data = _request("GET", "/telephony_credentials", params={"page[size]": 1}).get("data") or []
    if data and data[0].get("user_id"):
        _account_sid = str(data[0]["user_id"])
        return _account_sid
    raise TelnyxError("Telnyx account id unknown — set TELNYX_ACCOUNT_SID")


# ---- Agents (WebRTC) ---------------------------------------------------------------

def _credential(username: str) -> tuple[str, str]:
    if username in _creds:
        return _creds[username]
    rows = _request("GET", "/telephony_credentials", params={"filter[name]": username}).get("data") or []
    row = next((r for r in rows if r.get("name") == username and not r.get("expired")), None)
    if row is None:
        conn = (settings.TELNYX_CREDENTIAL_CONNECTION_ID or "").strip()
        if not conn:
            raise TelnyxError("TELNYX_CREDENTIAL_CONNECTION_ID is not set")
        row = _request("POST", "/telephony_credentials",
                       json={"connection_id": conn, "name": username, "tag": "iag-agent"}).get("data") or {}
    if not row.get("id") or not row.get("sip_username"):
        raise TelnyxError(f"telephony credential for {username}: {row}")
    _creds[username] = (str(row["id"]), str(row["sip_username"]))
    return _creds[username]


def ensure_user(username: str, display_name: str | None = None) -> None:
    """One telephony credential per agent (created on first use)."""
    if simulating():
        return
    _credential(username)


def client_token(username: str, ttl_seconds: int = 0) -> str:
    """Login JWT for the browser's Telnyx WebRTC SDK (Telnyx: valid 24 h)."""
    if simulating():
        return "sim-token"
    cred_id, _sip = _credential(username)
    return _request("POST", f"/telephony_credentials/{cred_id}/token", raw=True).strip().strip('"')


def sip_uri(user_id) -> str:
    _id, sip = _credential(agent_username(user_id))
    return f"sip:{sip}@{SIP_DOMAIN}"


# ---- Calls -------------------------------------------------------------------------

def create_call(*, to: str, from_: str, answer_url: str, event_url: str, ring_seconds: int,
                detect: bool | None = None) -> str:
    """Place a TeXML call: Telnyx fetches answer_url when it is answered.
    Returns the CallSid."""
    if simulating():
        return "sim-" + uuid.uuid4().hex
    body: dict = {
        "ApplicationSid": settings.TELNYX_TEXML_APP_ID.strip(),
        "To": e164(to),
        "From": e164(from_),
        "Url": answer_url,
        "UrlMethod": "POST",
        "StatusCallback": event_url,
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": "initiated ringing answered completed",
        "Timeout": max(5, min(int(ring_seconds), 120)),
    }
    if settings.VOICE_MACHINE_DETECTION if detect is None else detect:
        # In the background: the answer URL bridges on pickup and the verdict
        # arrives as an event (human / machine).
        body.update({"MachineDetection": "Enable", "AsyncAmd": True,
                     "AsyncAmdStatusCallback": event_url, "AsyncAmdStatusCallbackMethod": "POST"})
    data = _request("POST", f"/texml/Accounts/{account_sid()}/Calls", json=body)
    data = data.get("data", data) if isinstance(data, dict) else {}
    sid = data.get("call_sid") or data.get("sid") or data.get("CallSid")
    if not sid:
        raise TelnyxError(f"create call: no call_sid in {data}")
    return str(sid)


def dial_browser(user_id, *, answer_url: str, event_url: str, from_: str | None = None) -> str:
    """Ring an agent's browser (it answers by itself). Returns the CallSid."""
    if simulating():
        return "sim-" + uuid.uuid4().hex
    frm = from_ or next(iter(_fleet()), "")
    if not frm:
        raise TelnyxError("no caller ID — set TELNYX_FROM_NUMBERS")
    return create_call(to=sip_uri(user_id), from_=frm, answer_url=answer_url, event_url=event_url,
                       ring_seconds=30, detect=False)


def _fleet() -> list[str]:
    return [e164(n) for n in (settings.TELNYX_FROM_NUMBERS or "").split(",") if re.sub(r"\D", "", n)]


def _update(call_sid: str, form: dict) -> None:
    _request("POST", f"/texml/Accounts/{account_sid()}/Calls/{call_sid}", form=form)


def transfer(call_sid: str, script: list[dict]) -> None:
    """Replace a live call's script."""
    if simulating() or str(call_sid).startswith("sim-"):
        return
    _update(call_sid, {"Texml": to_texml(script)})


def hangup(call_sid: str | None) -> None:
    if not call_sid or simulating() or str(call_sid).startswith("sim-"):
        return
    try:
        _update(call_sid, {"Status": "completed"})
    except TelnyxError as exc:
        logger.info("telnyx hangup %s: %s", call_sid, exc)


def talk(call_sid: str, text: str) -> None:
    if simulating() or str(call_sid).startswith("sim-"):
        return
    try:
        _update(call_sid, {"Texml": to_texml([{"action": "talk", "text": text}])})
    except TelnyxError as exc:
        logger.info("telnyx talk %s: %s", call_sid, exc)


def conference_recording_url(name: str) -> str | None:
    """A finished room recording's download link, looked up by room name. Telnyx
    can skip the room's recordingStatusCallback (seen live: recorded, no webhook),
    and the link expires, so it is read fresh each time."""
    if simulating():
        return None
    base = f"/texml/Accounts/{account_sid()}/Conferences"
    for room in _request("GET", base, params={"FriendlyName": name}).get("conferences") or []:
        for rec in _request("GET", f"{base}/{room['sid']}/Recordings").get("recordings") or []:
            if rec.get("status") == "completed" and rec.get("media_url"):
                return rec["media_url"]
    return None


def download(url: str) -> bytes:
    if url.startswith("sim://"):
        from app.voice_dialer.simulator import fake_audio

        return fake_audio()
    t = httpx.Timeout(60.0)
    r = httpx.get(url, timeout=t, follow_redirects=True)
    if r.status_code in (401, 403):  # an API URL rather than a signed one
        r = httpx.get(url, headers=_auth(), timeout=t, follow_redirects=True)
    r.raise_for_status()
    return r.content


# ---- Script -> TeXML -----------------------------------------------------------------

def _attrs(**kw) -> str:
    return "".join(f" {k}={quoteattr(str(v))}" for k, v in kw.items() if v is not None)


def _url0(v) -> str | None:
    if isinstance(v, list):
        return v[0] if v else None
    return v or None


def _say(item: dict) -> str:
    return f"<Say{_attrs(language=item.get('language') or 'en-US')}>{escape(str(item.get('text') or ''))}</Say>"


def _play(item: dict) -> str:
    url = _url0(item.get("streamUrl"))
    return f"<Play{_attrs(loop=item.get('loop', 1))}>{escape(url or '')}</Play>"


def silence_wav() -> bytes:
    """One minute of silence: what someone alone in a conference hears, instead
    of Telnyx's hold music (an agent's parked line is alone all shift)."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * 60)
    return out.getvalue()


def _wait_url() -> str | None:
    base = (settings.VOICE_CALLBACK_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/voice/{SLUG}/silence.wav" if base else None


def _conference(item: dict) -> str:
    attrs = {
        "startConferenceOnEnter": str(item.get("startOnEnter", True)).lower(),
        "endConferenceOnExit": str(item.get("endOnExit", False)).lower(),
        "beep": "false",
        "waitUrl": _wait_url(),
        "waitMethod": "GET",
    }
    rec = _url0(item.get("eventUrl")) if item.get("record") else None
    if rec:
        attrs.update({"record": "record-from-start", "recordBeep": "false",
                      "recordingStatusCallback": rec, "recordingStatusCallbackMethod": "POST",
                      "recordingStatusCallbackEvent": "completed"})
    return f"<Dial><Conference{_attrs(**attrs)}>{escape(str(item.get('name') or ''))}</Conference></Dial>"


def _dial(item: dict, record: dict | None) -> str:
    ep = (item.get("endpoint") or [{}])[0]
    number = e164(ep.get("number"))
    attrs: dict = {"callerId": e164(item.get("from")) or None,
                   "timeout": max(5, min(int(item.get("timeout") or 30), 120))}
    if record:
        attrs.update({"record": "record-from-answer-dual",
                      "recordingStatusCallback": _url0(record.get("eventUrl")),
                      "recordingStatusCallbackMethod": "POST",
                      "recordingStatusCallbackEvent": "completed"})
    num = {"statusCallbackEvent": "initiated ringing answered completed", "statusCallbackMethod": "POST",
           "statusCallback": _url0(item.get("eventUrl"))}
    whisper = (ep.get("onAnswer") or {}).get("url")
    if whisper:
        num.update({"url": whisper, "method": "POST"})
    return f"<Dial{_attrs(**attrs)}><Number{_attrs(**num)}>{escape(number)}</Number></Dial>"


def to_texml(script: list[dict]) -> str:
    """Call script -> TeXML. Prompts with bargeIn right before an `input` go
    inside its <Gather> (a key press cuts them off). A Gather that times out
    falls through, so a <Redirect> to the same URL follows it: the handler
    sees "no digits"."""
    out: list[str] = []
    barge: list[str] = []
    record: dict | None = None

    def flush():
        out.extend(barge)
        barge.clear()

    for item in script or []:
        action = item.get("action")
        if action in ("talk", "stream"):
            xml = _say(item) if action == "talk" else _play(item)
            if item.get("bargeIn"):
                barge.append(xml)
            else:
                flush()
                out.append(xml)
        elif action == "input":
            dtmf = item.get("dtmf") or {}
            url = _url0(item.get("eventUrl"))
            attrs = _attrs(action=url, method="POST", input="dtmf",
                           numDigits=dtmf.get("maxDigits", 1), timeout=max(1, int(dtmf.get("timeOut") or 3)))
            out.append(f"<Gather{attrs}>{''.join(barge)}</Gather>")
            barge.clear()
            if url:
                out.append(f"<Redirect{_attrs(method='POST')}>{escape(url)}</Redirect>")
        elif action == "conversation":
            flush()
            out.append(_conference(item))
        elif action == "record":
            flush()
            record = item
        elif action == "connect":
            flush()
            out.append(_dial(item, record))
            record = None
        else:
            flush()
            logger.info("telnyx: script action %r has no TeXML equivalent", action)
    flush()
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(out) + "</Response>"


# ---- Webhooks --------------------------------------------------------------------------

_STATUS = {
    "queued": "started", "initiated": "started", "ringing": "ringing", "in-progress": "answered",
    "answered": "answered", "completed": "completed", "busy": "busy", "no-answer": "timeout",
    "canceled": "cancelled", "cancelled": "cancelled", "failed": "failed",
}


def normalize(data: dict) -> dict:
    """Telnyx TeXML callback -> the payload webhooks.py reads.
    The original fields stay alongside."""
    out = dict(data)
    learn_account(data)
    if data.get("CallSid"):
        out["uuid"] = data["CallSid"]
    if data.get("From"):
        out["from"] = str(data["From"]).removeprefix("sip:")
    if data.get("To"):
        out["to"] = str(data["To"]).removeprefix("sip:")
    answered_by = str(data.get("AnsweredBy") or "").lower()
    if answered_by:
        out["status"] = "machine" if answered_by.startswith("machine") or answered_by == "fax" else "human"
    elif data.get("CallStatus"):
        out["status"] = _STATUS.get(str(data["CallStatus"]).lower(), str(data["CallStatus"]).lower())
    dur = data.get("CallDuration") or data.get("DialCallDuration")
    if dur not in (None, ""):
        out["duration"] = str(dur)
    if "Digits" in data:
        out["dtmf"] = {"digits": data.get("Digits") or None}
    if data.get("RecordingUrl"):
        out["recording_url"] = data["RecordingUrl"]
        out["recording_uuid"] = data.get("RecordingSid")
    return out


def inbound_message(data: dict) -> dict:
    """Messaging v2 `message.received` -> {from, to, text, message_uuid}."""
    ev = data.get("data") if isinstance(data.get("data"), dict) else data
    if ev.get("event_type") and ev.get("event_type") != "message.received":
        return {}
    p = ev.get("payload") or {}
    to = p.get("to") or []
    to0 = to[0] if isinstance(to, list) and to else to
    return {
        "from": (p.get("from") or {}).get("phone_number"),
        "to": to0.get("phone_number") if isinstance(to0, dict) else to0,
        "text": p.get("text"),
        "message_uuid": p.get("id"),
    }


def _signature_ok(headers, body: bytes) -> bool | None:
    """Ed25519 over "<timestamp>|<body>". None = no signature to check."""
    key = (settings.TELNYX_PUBLIC_KEY or "").strip()
    sig = headers.get("telnyx-signature-ed25519")
    ts = headers.get("telnyx-timestamp")
    if not key or not sig or not ts:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if abs(time.time() - int(ts)) > REPLAY_WINDOW_SECONDS:
            return False
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(
            base64.b64decode(sig), f"{ts}|".encode() + (body or b""))
        return True
    except Exception:
        return False


def webhook_trusted(headers, body: bytes, k: str | None) -> bool:
    """Trusted if Telnyx signed it, or it carries our ?k= token. With neither
    configured, only a simulating (non-production) server accepts webhooks."""
    token = webhook_token()
    if token and k and hmac.compare_digest(token, k):
        return True
    signed = _signature_ok(headers, body)
    if signed is not None:
        return signed
    return not token and not (settings.TELNYX_PUBLIC_KEY or "").strip() and simulating()
