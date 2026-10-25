"""Manual dialing — anyone signed in calls any number from the Dial drawer.

Flow:
  1. POST /voice/manual/calls -> start(): validates the number + caller ID,
     logs a `manual_calls` row (DIALING) and, if the caller holds an auto
     dialer headset line, closes it.
  2. The browser places a Vonage Client SDK call with
     serverCall({purpose: "manual", mc: <row id>}). Vonage asks our answer URL
     -> on_answer(): record the conversation and `connect` to the phone,
     with the recording notice played to the callee as they pick up.
  3. The phone leg's events (connect eventUrl, ?mc=) and the browser leg's
     events (application event URL, matched by app_leg_uuid) -> on_event().
     The first final event ends the call; the recording lands via ?mc= too.

While a manual call is in flight its user is BUSY: queue_service's
_available_agents leaves them out, so no pool offer and no auto-dialer bridge
reaches them, and on_agent_line refuses to reopen their headset line. Nothing
is stored for that — "busy" is derived from the live row, so it ends with the
call (or with the staleness rules below if every event got lost). When it
ends the agent's own "Ready for calls" is untouched, so AgentLine simply
reopens the line and they are back in whatever state they were in.

Deliberately NOT applied here (owner's rule): Do-Not-Call, calling hours and
the per-number attempt cap — those belong to the auto dialer only. The log is
its own table; the dialer's stats and Called list never see these calls.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.sms import ManualCall, VoiceCallAttempt
from app.voice_dialer import replies, vonage

logger = logging.getLogger(__name__)

IN_FLIGHT = ("DIALING", "RINGING", "ANSWERED")
RING_SECONDS = 45
# The browser never reached Vonage this long after start() -> the call is dead.
NO_LEG_SECONDS = 90
# A call "in flight" longer than this lost its final event: stop counting it.
MAX_CALL_HOURS = 4

MANAGER_ROLES = {"manager", "head", "tenant_admin", "admin", "super_admin", "dev"}

# Vonage call status -> our status, for the PHONE leg.
_CALLEE_FINAL = {
    "busy": "BUSY",
    "timeout": "NO_ANSWER",
    "unanswered": "NO_ANSWER",
    "failed": "FAILED",
    "rejected": "FAILED",
    "cancelled": "CANCELLED",
}
_ENDED = ("completed", "failed", "rejected", "cancelled", "timeout", "unanswered", "busy")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evt(user_id, event: str, data: dict) -> dict:
    return {"to": "user", "id": str(user_id), "event": event, "data": data}


def is_manager(user) -> bool:
    return (getattr(user, "role", "") or "").lower() in MANAGER_ROLES


# ---- Who is busy --------------------------------------------------------------

def live_clause(now: datetime | None = None):
    """SQL filter: a manual call that is really still going."""
    now = now or _now()
    return and_(
        ManualCall.status.in_(IN_FLIGHT),
        ManualCall.created_at > now - timedelta(hours=MAX_CALL_HOURS),
        or_(ManualCall.app_leg_uuid.isnot(None), ManualCall.created_at > now - timedelta(seconds=NO_LEG_SECONDS)),
    )


def busy_user_ids(db: Session, tenant_id):
    """Subquery of users on a manual call right now (for NOT IN filters)."""
    return db.query(ManualCall.user_id).filter(ManualCall.tenant_id == tenant_id, live_clause()).subquery()


def live_call(db: Session, user_id) -> ManualCall | None:
    return (
        db.query(ManualCall)
        .filter(ManualCall.user_id == user_id, live_clause())
        .order_by(ManualCall.created_at.desc())
        .first()
    )


# ---- Numbers ------------------------------------------------------------------

def caller_ids() -> list[str]:
    from app.voice_dialer.service import fleet_numbers

    fleet = fleet_numbers()
    if not fleet and vonage.simulating():
        return ["+15550000000"]
    return fleet


def normalize(raw: str | None) -> str | None:
    """+1XXXXXXXXXX — US numbers only (the drawer formats them as typed)."""
    return replies.normalize_phone(raw)


def config() -> dict:
    nums = caller_ids()
    return {
        "available": vonage.available(),
        "simulate": vonage.simulating(),
        "numbers": nums,
        "default": nums[0] if nums else None,
    }


# ---- Start / end ----------------------------------------------------------------

class ManualCallError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def start(db: Session, tenant_id, user, to_raw: str, from_raw: str | None) -> tuple[dict, list[dict]]:
    from app.voice_dialer import service

    if not vonage.available():
        raise ManualCallError("Calling is not set up (Vonage is not configured)", 503)
    to = normalize(to_raw)
    if not to:
        raise ManualCallError("Enter a 10-digit US phone number")
    nums = caller_ids()
    if not nums:
        raise ManualCallError("No caller ID numbers are set up (VONAGE_FROM_NUMBERS)", 503)
    frm = replies.normalize_phone(from_raw) if from_raw else nums[0]
    if frm not in nums:
        raise ManualCallError("That caller ID is not one of our numbers")
    # A customer bridged by the auto dialer is on this browser's line: closing
    # the line would hang up on them.
    on_dialer = (
        db.query(VoiceCallAttempt.id)
        .filter(VoiceCallAttempt.agent_user_id == user.id, VoiceCallAttempt.status == "CONNECTED")
        .first()
    )
    if on_dialer:
        raise ManualCallError("Finish your current call first", 409)

    events: list[dict] = []
    for old in db.query(ManualCall).filter(ManualCall.user_id == user.id, live_clause()).all():
        # A call left over from another tab / a crash: one call per person.
        _hangup_legs(old)
        events += _finish(db, old, "CANCELLED", error="replaced by a new call")

    call = ManualCall(tenant_id=tenant_id, user_id=user.id, phone_number=to, from_number=frm, status="DIALING")
    db.add(call)
    db.commit()
    # The agent who calls a client owns their callbacks (managers never do).
    from app.voice_dialer import inbound

    inbound.set_owner(db, tenant_id, to, user.id)
    # Busy from here on: the headset line closes and nothing is routed to them.
    from app.models.sms import SmsQueueAgent

    line = (
        db.query(SmsQueueAgent.id)
        .filter(SmsQueueAgent.user_id == user.id, SmsQueueAgent.voice_leg_uuid.isnot(None))
        .first()
    )
    if line:
        events += service.drop_line(db, tenant_id, str(user.id))
    events.append(_evt(user.id, "voice:manual", {"id": str(call.id), "status": call.status}))

    if vonage.simulating():
        from app.voice_dialer import webhooks

        # Play Vonage: the browser's leg reaches the answer URL, the phone rings.
        webhooks.answer(db, {
            "uuid": "sim-app-" + uuid.uuid4().hex,
            "conversation_uuid": f"CON-{call.id}",
            "from_user": vonage.agent_username(user.id),
            "custom_data": {"purpose": "manual", "mc": str(call.id)},
        }, {})
        events += webhooks.event(db, {"uuid": "sim-" + uuid.uuid4().hex, "status": "ringing"},
                                 {"mc": str(call.id), "leg": "callee"})
        db.refresh(call)
    return call_dict(call), events


def find(db: Session, call_id) -> ManualCall | None:
    try:
        return db.query(ManualCall).filter(ManualCall.id == uuid.UUID(str(call_id))).first()
    except (ValueError, TypeError):
        return None


def find_by_leg(db: Session, leg: str | None) -> ManualCall | None:
    if not leg:
        return None
    return (
        db.query(ManualCall)
        .filter(or_(ManualCall.app_leg_uuid == leg, ManualCall.callee_leg_uuid == leg))
        .order_by(ManualCall.created_at.desc())
        .first()
    )


def _hangup_legs(call: ManualCall) -> None:
    vonage.hangup(call.callee_leg_uuid)
    vonage.hangup(call.app_leg_uuid)


def end(db: Session, call: ManualCall) -> list[dict]:
    """The caller pressed Hang up (or their browser's call dropped). Ends it
    now rather than waiting for Vonage's events: a lost event must never leave
    an agent "busy". Vonage's later events only fill in the exact duration."""
    if call.status not in IN_FLIGHT:
        return []
    if vonage.simulating():
        from app.voice_dialer import webhooks

        events: list[dict] = []
        if call.status == "ANSWERED":
            talk = int((_now() - call.answered_at).total_seconds()) if call.answered_at else 1
            events += webhooks.event(db, {"uuid": call.callee_leg_uuid, "status": "completed",
                                          "duration": str(max(talk, 1))}, {"mc": str(call.id), "leg": "callee"})
            db.refresh(call)
            rec = str(uuid.uuid4())
            webhooks.recording(db, {"recording_url": f"sim://recording/{rec}", "recording_uuid": rec},
                               {"mc": str(call.id)})
        else:
            events += webhooks.event(db, {"uuid": call.app_leg_uuid, "status": "completed"}, {})
        return events
    _hangup_legs(call)
    return _finish(db, call, "COMPLETED" if call.status == "ANSWERED" else "CANCELLED")


def sim_answer(db: Session, call: ManualCall) -> list[dict]:
    """Simulator: the person picks up."""
    from app.voice_dialer import webhooks

    if call.status not in ("DIALING", "RINGING"):
        return []
    return webhooks.event(db, {"uuid": call.callee_leg_uuid, "status": "answered"},
                          {"mc": str(call.id), "leg": "callee"})


# ---- Vonage webhooks (called from webhooks.py) ------------------------------------

def notice_ncco() -> list[dict]:
    """Played to the person we called the moment they pick up."""
    if not settings.CALL_RECORDING_ENABLED or not settings.VONAGE_RECORDING_NOTICE:
        return []
    return [{"action": "talk", "text": settings.VONAGE_RECORDING_NOTICE, "language": "en-US"}]


def on_answer(db: Session, data: dict, custom: dict) -> list[dict]:
    """The caller's browser reached Vonage: record, then ring the number."""
    from app.voice_dialer.service import _url

    call = find(db, custom.get("mc"))
    who = data.get("from_user") or data.get("from") or ""
    if call is None or vonage.user_id_from_username(str(who)) != str(call.user_id):
        return [{"action": "talk", "text": "This call could not be placed."}]
    if call.status not in IN_FLIGHT:
        return [{"action": "talk", "text": "This call has ended."}]
    call.app_leg_uuid = data.get("uuid") or call.app_leg_uuid
    db.commit()
    endpoint: dict = {"type": "phone", "number": replies.vonage_number(call.phone_number)}
    if notice_ncco():
        endpoint["onAnswer"] = {"url": _url("answer", leg="manual_notice", mc=call.id)}
    ncco: list[dict] = []
    if settings.CALL_RECORDING_ENABLED:
        ncco.append({
            "action": "record", "eventUrl": [_url("recording", mc=call.id)], "eventMethod": "POST",
            "split": "conversation", "channels": 2, "format": "mp3",
        })
    ncco.append({
        "action": "connect",
        "from": replies.vonage_number(call.from_number),
        "timeout": RING_SECONDS,
        "eventUrl": [_url("event", mc=call.id, leg="callee")],
        "eventMethod": "POST",
        "endpoint": [endpoint],
    })
    return ncco


def on_event(db: Session, call: ManualCall, data: dict, leg: str) -> list[dict]:
    status = str(data.get("status") or "").lower()
    uuid_ = data.get("uuid") or data.get("call_uuid")
    if leg == "callee" and uuid_ and not call.callee_leg_uuid:
        call.callee_leg_uuid = uuid_
    if call.status not in IN_FLIGHT:
        # Already ended by Hang up: only take Vonage's exact talk time.
        if leg == "callee" and status == "completed" and call.answered_at and data.get("duration"):
            try:
                call.duration_seconds = int(float(data["duration"]))
            except (TypeError, ValueError):
                pass
        db.commit()
        return []

    events: list[dict] = []
    if leg == "callee":
        if status == "ringing" and call.status == "DIALING":
            call.status = "RINGING"
        elif status == "answered":
            call.status = "ANSWERED"
            call.answered_at = call.answered_at or _now()
        elif status == "completed":
            dur = None
            try:
                dur = int(float(data.get("duration"))) if data.get("duration") else None
            except (TypeError, ValueError):
                pass
            return _finish(db, call, "COMPLETED" if call.answered_at else "NO_ANSWER", duration=dur)
        elif status in _CALLEE_FINAL:
            _hangup_legs(call)  # the phone leg is over: free the caller's browser too
            return _finish(db, call, _CALLEE_FINAL[status])
        db.commit()
        if call.status in ("RINGING", "ANSWERED"):
            events.append(_evt(call.user_id, "voice:manual", {"id": str(call.id), "status": call.status}))
        return events

    # The caller's browser leg ending ends the call (they hung up / closed the tab).
    if status in _ENDED:
        vonage.hangup(call.callee_leg_uuid)
        return _finish(db, call, "COMPLETED" if call.answered_at else "CANCELLED")
    return []


def _finish(db: Session, call: ManualCall, status: str, *, duration: int | None = None,
            error: str | None = None) -> list[dict]:
    from app.sms_queue.services import queue_service as qs
    from app.voice_dialer import service

    call.status = status
    call.ended_at = _now()
    if duration is not None:
        call.duration_seconds = duration
    elif call.answered_at:
        call.duration_seconds = max(0, int((call.ended_at - call.answered_at).total_seconds()))
    if error:
        call.error = error[:500]
    db.commit()
    events = [
        _evt(call.user_id, "voice:manual", {"id": str(call.id), "status": status}),
        # AgentLine re-reads its state: their line reopens if Ready for calls is on.
        _evt(call.user_id, "voice:line", {"manual": False}),
    ]
    tenant_id = str(call.tenant_id)
    try:
        events += service.tick(db, tenant_id)
        events += qs._try_assign(db, tenant_id)
    except Exception as exc:  # routing must never fail the hang-up
        logger.warning("manual call: re-route after call failed: %s", exc)
    return events


def store_recording(db: Session, call: ManualCall, payload: dict) -> dict:
    from app.voice_dialer.service import save_recording

    url = payload.get("recording_url")
    if not url:
        return {"ok": False}
    if call.recording_id:
        return {"ok": True, "duplicate": True}
    try:
        rec = save_recording(db, tenant_id=call.tenant_id, user_id=call.user_id,
                             phone=call.phone_number, when=call.answered_at or call.created_at, url=url)
    except Exception as exc:
        call.error = f"recording download failed: {exc}"[:500]
        db.commit()
        return {"ok": False, "error": "download failed"}
    call.recording_id = rec.id
    db.commit()
    return {"ok": True, "recording_id": str(rec.id), "storage": rec.storage}


# ---- Read models ----------------------------------------------------------------

def call_dict(c: ManualCall, names: dict | None = None, with_recording: bool = False) -> dict:
    out = {
        "id": str(c.id),
        "phone_number": c.phone_number,
        "from_number": c.from_number,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "answered_at": c.answered_at.isoformat() if c.answered_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "duration_seconds": c.duration_seconds,
    }
    if names is not None:
        out["user_id"] = str(c.user_id)
        out["user_name"] = names.get(str(c.user_id))
    if with_recording:
        if c.recording_id:
            out["recording"] = "stored"
        elif c.answered_at and c.status == "COMPLETED" and settings.CALL_RECORDING_ENABLED:
            # Vonage posts the file a few seconds after hang-up.
            recent = c.ended_at and c.ended_at > _now() - timedelta(minutes=5)
            out["recording"] = "pending" if recent and not c.error else "failed"
        else:
            out["recording"] = None
    return out


def history(db: Session, tenant_id, user, *, user_filter: str | None = None,
            before: str | None = None, limit: int = 30) -> dict:
    """Agents: their own calls. Managers/admins: everyone's, with who dialed
    and the recording. Newest first, paged by created_at."""
    from app.core.active_agents import labelled
    from app.models.user import User

    manager = is_manager(user)
    limit = max(1, min(int(limit or 30), 100))
    q = db.query(ManualCall).filter(ManualCall.tenant_id == tenant_id)
    if not manager:
        q = q.filter(ManualCall.user_id == user.id)
    elif user_filter:
        try:
            q = q.filter(ManualCall.user_id == uuid.UUID(user_filter))
        except ValueError:
            pass
    if before:
        try:
            q = q.filter(ManualCall.created_at < datetime.fromisoformat(before.replace("Z", "+00:00")))
        except ValueError:
            pass
    rows = q.order_by(ManualCall.created_at.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    out: dict = {"more": more, "manager": manager}
    if manager:
        callers = {
            str(r[0]) for r in db.query(ManualCall.user_id).filter(ManualCall.tenant_id == tenant_id).distinct()
        }
        users = db.query(User).filter(User.id.in_(callers)).all() if callers else []
        names = {
            str(u.id): labelled(f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                                u.status == "active" and u.deleted_at is None)
            for u in users
        }
        out["items"] = [call_dict(c, names, with_recording=True) for c in rows]
        out["users"] = sorted(({"id": k, "name": v} for k, v in names.items()), key=lambda u: (u["name"] or "").lower())
    else:
        out["items"] = [call_dict(c) for c in rows]
    return out
