"""YES-reply auto dialer — HTTP surface.

Two groups, both under /api/v1:

* /voice/vonage/*  PUBLIC webhooks Vonage calls (no portal login). Each one is
  checked with vonage.webhook_trusted() — Vonage's signed JWT or our ?k= token.
    answer      customer leg answered (hold while detection listens) | agent
                headset line opened | a client calling one of our numbers
    event       call status + machine-detection verdicts
    inbound-sms Messages API inbound text (the YES / STOP trigger)
    sms-status  delivery receipts (acknowledged only)
    recording   a connected call's recording is ready
    optout      "press 9" on the all-agents-busy message
* /voice/dialer/*  the portal: agent line + state, manager overview/settings,
  and the dev-only simulator.
* /voice/manual/*  the Dial drawer: anyone signed in calls any number
  (manual.py). Its own log, apart from the dialer's.
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id, require_role
from app.models.user import User
from app.realtime.websocket import emit_to_agent, emit_to_tenant, emit_to_user
from app.voice_dialer import inbox, manual, optouts, service, simulator, vonage, webhooks

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-dialer"])

_require_manager = require_role("manager", "head", "tenant_admin", "admin", "super_admin")
_require_admin = require_role("head", "tenant_admin", "admin", "super_admin")


# The dialer's service/webhook code is synchronous SQLAlchemy. Inside an
# `async def` route it must run on the threadpool (`_run`), never on the event
# loop: a blocking DB call there froze EVERY request — and once the connection
# pool was busy, the loop sat waiting (pool_timeout) for a connection only the
# loop itself could release, so the whole app hung.
async def _run(fn, *args):
    return await run_in_threadpool(fn, *args)


async def _flush(events: list[dict]) -> None:
    for e in events or []:
        to, _id, event, data = e.get("to"), e.get("id"), e.get("event"), e.get("data")
        if not _id:
            continue
        try:
            if to == "agent":
                await emit_to_agent(_id, event, data)
            elif to == "user":
                await emit_to_user(_id, event, data)
            elif to == "tenant":
                await emit_to_tenant(_id, event, data)
        except Exception as exc:  # realtime must never fail a webhook
            logger.warning("voice dialer emit %s failed: %s", event, exc)


async def _payload(request: Request) -> tuple[dict, bytes]:
    """Vonage sends JSON (POST) or query params (GET); merge both."""
    body = await request.body()
    data: dict = {}
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "ignore")).items()}
    for k, v in request.query_params.items():
        data.setdefault(k, v)
    return data, body


async def _trusted(request: Request) -> tuple[dict, bytes]:
    data, body = await _payload(request)
    if not vonage.webhook_trusted(request.headers, body, request.query_params.get("k")):
        raise HTTPException(status_code=401, detail="untrusted webhook")
    return data, body


def _trace(kind: str, request: Request, data: dict, reply) -> None:
    """TEMP (local debugging of live calls): what Vonage sent and what we said."""
    if not settings_trace():
        return
    q = {k: v for k, v in request.query_params.items() if k != "k"}
    keep = {k: data.get(k) for k in ("uuid", "call_uuid", "status", "sub_state", "direction", "from_user", "reason") if data.get(k) is not None}
    logger.warning("VONAGE %s %s %s -> %s", kind, q, keep or data, json.dumps(reply)[:300] if reply is not None else "")


def settings_trace() -> bool:
    from app.core.config import settings as _s

    return (_s.APP_ENV or "").lower() != "production"


# =========================== Vonage webhooks ===========================
# Thin HTTP shells: check trust, parse, hand to webhooks.py (shared with the
# simulator so it runs the very same code), flush socket events.

@router.api_route("/voice/vonage/answer", methods=["GET", "POST"])
async def vonage_answer(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    ncco, events = await _run(webhooks.answer, db, data, dict(request.query_params))
    await _flush(events)
    _trace("answer", request, data, ncco)
    return ncco


@router.api_route("/voice/vonage/event", methods=["GET", "POST"])
async def vonage_event(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    _trace("event", request, data, None)
    await _flush(await _run(webhooks.event, db, data, dict(request.query_params)))
    return {"ok": True}


@router.api_route("/voice/vonage/inbound-sms", methods=["GET", "POST"])
async def vonage_inbound_sms(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    result, events = await _run(webhooks.inbound_sms, db, data)
    await _flush(events)
    return result


@router.api_route("/voice/vonage/sms-status", methods=["GET", "POST"])
async def vonage_sms_status(request: Request):
    await _trusted(request)
    return {"ok": True}


@router.api_route("/voice/vonage/recording", methods=["GET", "POST"])
async def vonage_recording(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    return await _run(webhooks.recording, db, data, dict(request.query_params))


@router.api_route("/voice/vonage/hold", methods=["GET", "POST"])
async def vonage_hold(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    ncco, events = await _run(webhooks.hold, db, data, dict(request.query_params))
    await _flush(events)
    return ncco


@router.get("/voice/vonage/hold-music.wav")
def vonage_hold_music():
    """Hold music Vonage streams to callers in the queue (public audio)."""
    from app.voice_dialer import inbound

    return Response(content=inbound.hold_music(), media_type="audio/wav",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.api_route("/voice/vonage/optout", methods=["GET", "POST"])
async def vonage_optout(request: Request, db: Session = Depends(get_db)):
    data, _ = await _trusted(request)
    return await _run(webhooks.optout, db, data, dict(request.query_params))


# =========================== Portal ===========================

_last_tick: dict[str, float] = {}


@router.get("/voice/dialer/state")
async def dialer_state(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """The agent's line + live call (polled by the SPA). Also nudges the dialer
    at most every 2 s per tenant, so it keeps moving even without Celery beat."""
    now = time.monotonic()
    if now - _last_tick.get(tenant_id, 0.0) >= 2.0:
        _last_tick[tenant_id] = now
        await _flush(await _run(service.tick, db, tenant_id))
    return await _run(service.agent_state, db, tenant_id, str(user.id))


@router.post("/voice/dialer/token")
def dialer_token(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Vonage Client SDK login for this agent's headset line."""
    if not vonage.available():
        raise HTTPException(status_code=503, detail="Vonage is not configured")
    name = vonage.agent_username(user.id)
    try:
        vonage.ensure_user(name, f"{user.first_name or ''} {user.last_name or ''}".strip() or None)
        token = vonage.client_token(name)
    except Exception as exc:
        logger.warning("vonage token failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach Vonage")
    return {"token": token, "username": name, "simulate": vonage.simulating()}


@router.post("/voice/dialer/line")
async def dialer_line(
    on: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Line off (any mode) or — simulator only — line on without a real SDK leg."""
    if on:
        if not vonage.simulating():
            raise HTTPException(status_code=400, detail="the line is opened by the Vonage SDK")
        _ncco, events = await _run(service.on_agent_line, db, str(user.id), f"sim-line-{user.id}")
    else:
        events = await _run(service.drop_line, db, tenant_id, str(user.id))
    await _flush(events)
    return await _run(service.agent_state, db, tenant_id, str(user.id))


@router.post("/voice/dialer/ready")
async def dialer_ready(
    on: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Agent's "Ready for calls" switch (separate from Join queue)."""
    await _flush(await _run(service.set_ready, db, tenant_id, str(user.id), on))
    return await _run(service.agent_state, db, tenant_id, str(user.id))


@router.post("/voice/dialer/hangup")
async def dialer_hangup(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Agent ends the call with the customer; their own line stays open."""
    return await _run(service.hangup_customer, db, tenant_id, str(user.id))


@router.get("/voice/dialer/overview")
async def dialer_overview(
    period: str = Query("today"),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    await _flush(await _run(service.tick, db, tenant_id))
    return await _run(service.overview, db, tenant_id, period, date_from, date_to)


@router.get("/voice/dialer/calls")
def dialer_calls(
    kind: str = Query("all"),
    before: str | None = Query(None),
    limit: int = Query(30),
    period: str = Query("all"),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """Called list, paged for load-on-scroll."""
    return service.call_history(db, tenant_id, kind, before, limit, period, date_from, date_to)


@router.put("/voice/dialer/settings")
async def dialer_settings(
    body: dict = Body(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    data = await _run(service.update_settings, db, tenant_id, body or {})
    try:
        from app.core.audit import log_ai_action

        log_ai_action(tenant_id=str(tenant_id), action="voice_dialer_settings", resource_type="tenant",
                      resource_id=str(tenant_id), details={"actor": str(user.id), **data})
    except Exception:
        pass
    await _flush(await _run(service.tick, db, tenant_id))
    await emit_to_tenant(tenant_id, "voice:dialer_updated", {"reason": "settings"})
    return data


# ---- Opted out (Do-Not-Call list) ----

@router.get("/voice/dialer/optouts")
def dialer_optouts(
    kind: str = Query("all"),
    q: str | None = Query(None),
    period: str = Query("all"),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    before: str | None = Query(None),
    limit: int = Query(30),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """Who is never called or texted again, and why — newest first, paged."""
    return optouts.listing(db, tenant_id, kind, q, period, date_from, date_to, before, limit)


@router.post("/voice/dialer/optouts")
def dialer_optout_add(
    phone: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    try:
        out = optouts.add(db, tenant_id, phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(tenant_id, user, "dnc_added", {"phone": phone})
    return out


@router.delete("/voice/dialer/optouts/{dnc_id}")
def dialer_optout_remove(
    dnc_id: str,
    confirm_stop: bool = Query(False),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    out = optouts.remove(db, tenant_id, dnc_id, confirm_stop)
    if out.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="not on the list")
    if out.get("reason") == "needs_confirm":
        raise HTTPException(status_code=409, detail="This client texted STOP — confirm before removing")
    _audit(tenant_id, user, "dnc_removed", out)
    return out


def _audit(tenant_id, user: User, action: str, details: dict, resource_type: str = "dnc") -> None:
    try:
        from app.core.audit import log_ai_action

        # resource_id is a UUID column: a phone number there made every one of
        # these inserts fail (silently). The number lives in details.
        log_ai_action(tenant_id=str(tenant_id), action=action, resource_type=resource_type,
                      resource_id=None, details={"actor": str(user.id), **details})
    except Exception as exc:
        logger.warning("voice dialer: audit %s failed: %s", action, exc)


# ---- Client owners (who a client's callbacks go to) ----

@router.get("/voice/dialer/owners")
def dialer_owners(
    q: str | None = Query(None),
    agent: str | None = Query(None),
    before: str | None = Query(None),
    limit: int = Query(30),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    from app.voice_dialer import inbound

    return inbound.owners_listing(db, tenant_id, q, agent, before, limit)


@router.put("/voice/dialer/owners")
def dialer_owner_set(
    phone: str = Body(..., embed=True),
    user_id: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    """Admin reassigns a client to another agent."""
    from app.voice_dialer import inbound

    try:
        out = inbound.reassign(db, tenant_id, phone, user_id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(tenant_id, user, "client_owner_set", {"phone": out["phone_number"], "owner": user_id}, "client_owner")
    return out


@router.delete("/voice/dialer/owners")
def dialer_owner_remove(
    phone: str = Query(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(_require_admin),
):
    from app.voice_dialer import inbound

    if not inbound.remove_owner(db, tenant_id, phone):
        raise HTTPException(status_code=404, detail="No owner for that number")
    _audit(tenant_id, user, "client_owner_removed", {"phone": phone}, "client_owner")
    return {"ok": True}


# ---- Inbox + recordings ----

@router.get("/voice/dialer/inbox")
def dialer_inbox(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """Everyone who texted our Vonage numbers, latest activity first."""
    return {"items": inbox.conversations(db, tenant_id)}


@router.get("/voice/dialer/inbox/{lead_id}")
def dialer_inbox_thread(
    lead_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    try:
        t = inbox.thread(db, tenant_id, lead_id)
    except Exception:
        t = None
    if t is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return t


def _user_from_header_or_query(request: Request, token: str | None = Query(None), db: Session = Depends(get_db)) -> User:
    """<audio src> can't send an Authorization header, so playback also accepts
    the access token as ?token= (same JWT, same expiry) — like training videos."""
    from app.core.security import decode_token

    auth = request.headers.get("authorization") or ""
    raw = auth[7:] if auth.lower().startswith("bearer ") else (token or "")
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = db.query(User).filter(User.id == payload.get("sub"), User.deleted_at.is_(None)).first()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found")
    return user


_MANAGER_ROLES = {"manager", "head", "tenant_admin", "admin", "super_admin", "dev"}


@router.get("/voice/dialer/recordings/{attempt_id}")
def dialer_recording(
    attempt_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user_from_header_or_query),
):
    """Play a call's recording. Managers/admins: any call; an agent: only
    calls they took."""
    att = service.find_attempt(db, attempt_id, None)
    if att is None or str(att.tenant_id) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    if (user.role or "").lower() not in _MANAGER_ROLES and str(att.agent_user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="You can only play your own calls")
    try:
        kind, payload, ctype = service.recording_audio(db, str(user.tenant_id), attempt_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if kind == "url":
        return RedirectResponse(payload)
    return _audio_response(request, payload, ctype, f"call-{attempt_id}")


def _audio_response(request: Request, data: bytes, ctype: str, name: str) -> Response:
    """Inline audio with Range support (seeking in <audio>)."""
    total = len(data)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600",
               "Content-Disposition": f'inline; filename="{name}.{"wav" if "wav" in ctype else "mp3"}"'}
    rng = request.headers.get("range")
    # "bytes=0-" asks for the whole file: answer a plain 200 — it plays through
    # any proxy, even one that drops Content-Range. Real seeks still get a 206.
    if rng in ("bytes=0-", "bytes=0-%d" % (total - 1)):
        rng = None
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
        return Response(content=chunk, status_code=206, media_type=ctype,
                        headers={**headers, "Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(chunk))})
    return Response(content=data, media_type=ctype, headers={**headers, "Content-Length": str(total)})


# ---- Manual dialing (Dial drawer) ----
# Open to every signed-in role. The call log is the caller's own for agents;
# manager-class sees everyone's and plays the recordings.

@router.get("/voice/manual/config")
def manual_config(user: User = Depends(get_current_active_user)):
    """Caller ID choices for the drawer (default = the first number)."""
    return manual.config()


@router.post("/voice/manual/calls")
async def manual_start(
    to: str = Body(..., embed=True),
    from_number: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Log the call and make the caller busy; the browser then places it."""
    try:
        data, events = await _run(manual.start, db, tenant_id, user, to, from_number)
    except manual.ManualCallError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    await _flush(events)
    return data


@router.get("/voice/manual/calls")
def manual_history(
    user_id: str | None = Query(None),
    before: str | None = Query(None),
    limit: int = Query(30),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return manual.history(db, tenant_id, user, user_filter=user_id, before=before, limit=limit)


def _own_call(db: Session, tenant_id: str, user: User, call_id: str):
    call = manual.find(db, call_id)
    if call is None or str(call.tenant_id) != str(tenant_id) or str(call.user_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.get("/voice/manual/calls/{call_id}")
def manual_get(
    call_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    return manual.call_dict(_own_call(db, tenant_id, user, call_id))


@router.post("/voice/manual/calls/{call_id}/end")
async def manual_end(
    call_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Hang up (also sent when the browser's call drops on its own)."""
    def go():
        call = _own_call(db, tenant_id, user, call_id)
        events = manual.end(db, call)
        db.refresh(call)
        return manual.call_dict(call), events

    data, events = await _run(go)
    await _flush(events)
    return data


@router.post("/voice/manual/calls/{call_id}/sim-answer")
async def manual_sim_answer(
    call_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    """Simulator only: the person picks up."""
    _sim_only()

    def go():
        call = _own_call(db, tenant_id, user, call_id)
        events = manual.sim_answer(db, call)
        db.refresh(call)
        return manual.call_dict(call), events

    data, events = await _run(go)
    await _flush(events)
    return data


@router.get("/voice/manual/calls/{call_id}/recording")
def manual_recording(
    call_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user_from_header_or_query),
):
    """Play a manual call's recording — manager-class only."""
    if (user.role or "").lower() not in _MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Recordings are for managers")
    call = manual.find(db, call_id)
    if call is None or str(call.tenant_id) != str(user.tenant_id) or not call.recording_id:
        raise HTTPException(status_code=404, detail="Recording not found")
    try:
        kind, payload, ctype = service.recording_payload(db, call.recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if kind == "url":
        return RedirectResponse(payload)
    return _audio_response(request, payload, ctype, f"manual-call-{call_id}")


# ---- Simulator (dev only: no Vonage keys, not production) ----
# Everything goes through simulator.py, which feeds Vonage-format payloads to
# the same webhook code the real Vonage requests hit.

def _sim_only() -> None:
    if not vonage.simulating():
        raise HTTPException(status_code=404, detail="simulator is off")


@router.post("/voice/dialer/sim/inbound")
async def sim_inbound(
    phone: str = Body(..., embed=True),
    text: str = Body("YES", embed=True),
    to: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """A client texts one of our numbers (Messages API inbound webhook)."""
    _sim_only()
    fleet = service.fleet_numbers()
    result, events = await _run(simulator.text_in, db, phone, text, to or (fleet[0] if fleet else None))
    await _flush(events)
    return result


@router.post("/voice/dialer/sim/call/{attempt_id}")
async def sim_call(
    attempt_id: str,
    result: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """The customer leg does something: human | machine | no_answer | busy |
    hangup (customer hangs up a connected call)."""
    _sim_only()
    if result not in simulator.RESULTS:
        raise HTTPException(status_code=400, detail="unknown result")

    def play():
        att = service.find_attempt(db, attempt_id, None)
        if att is None or str(att.tenant_id) != str(tenant_id):
            return None, []
        events = simulator.play(db, att, result) + service.tick(db, tenant_id)
        db.refresh(att)
        return service.attempt_dict(att), events

    data, events = await _run(play)
    if data is None:
        raise HTTPException(status_code=404, detail="attempt not found")
    await _flush(events)
    return data


@router.put("/voice/dialer/sim/mode")
async def sim_mode(
    mode: str = Body(..., embed=True),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """What simulated calls do on their own (default: a live person answers)."""
    _sim_only()
    return {"sim_mode": simulator.set_mode(tenant_id, mode)}


@router.put("/voice/dialer/sim/ignore-hours")
async def sim_ignore_hours(
    on: bool = Body(..., embed=True),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    _user: User = Depends(_require_manager),
):
    """Waive calling hours while testing outside US hours (simulator only)."""
    _sim_only()
    value = simulator.set_ignore_hours(tenant_id, on)
    await _flush(await _run(service.tick, db, tenant_id))
    return {"sim_ignore_hours": value}
