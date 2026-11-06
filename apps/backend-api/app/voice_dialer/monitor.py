"""Call monitoring — a manager listens in on a live dialer call, or whispers.

Every bridged call already has a room of its own (service.call_room), so
listening is just one more person in that room:

  1. POST /voice/dialer/monitor -> start(): logs a `voice_monitors` row and
     RINGS the manager's browser (telnyx.dial_browser, ?leg=monitor&sv=),
     which answers by itself — the same way the Dial drawer works.
  2. Telnyx asks our answer URL -> on_answer(): join the call's room MUTED.
     Customer and agent hear nothing (no beep, no announcement).
  3. Whisper (set_mode): TeXML's <Conference> has no coach attribute, so the
     manager's participant is updated over REST — Coaching on, pointed at the
     agent's leg, and only once Telnyx confirms coaching is the manager
     unmuted. If it doesn't confirm, they stay muted: a whisper must never
     reach the customer.
  4. The call ending (customer hangs up, agent marks a result) hangs up every
     manager in it — otherwise the room would never empty, never end, and its
     recording would never land. The dialer tick sweeps anything missed.

While monitoring, a manager is busy like on a manual call: no pool offer, no
dialer bridge, and their own headset line stays closed.
Auto-dialed and inbound calls only — a manual (Dial drawer) call is a direct
dial with no room, so there is nothing to join.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.sms import SmsQueueAgent, VoiceCallAttempt, VoiceMonitor
from app.voice_dialer import manual, telnyx

logger = logging.getLogger(__name__)

MODES = ("listen", "whisper")
LIVE = ("RINGING", "LIVE")
# The browser never picked up this long after start() -> the session is dead.
RING_SECONDS = 60
_ENDED = ("completed", "failed", "rejected", "cancelled", "timeout", "unanswered", "busy")


class MonitorError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evt(user_id, data: dict) -> dict:
    return {"to": "user", "id": str(user_id), "event": "voice:monitor", "data": data}


# ---- Who is monitoring ---------------------------------------------------------

def live_clause(now: datetime | None = None):
    now = now or _now()
    return and_(
        VoiceMonitor.status.in_(LIVE),
        or_(VoiceMonitor.status == "LIVE", VoiceMonitor.created_at > now - timedelta(seconds=RING_SECONDS)),
    )


def busy_user_ids(db: Session, tenant_id):
    """Subquery of managers listening in right now (for NOT IN filters)."""
    return db.query(VoiceMonitor.user_id).filter(VoiceMonitor.tenant_id == tenant_id, live_clause()).subquery()


def live_for_user(db: Session, user_id) -> VoiceMonitor | None:
    return (
        db.query(VoiceMonitor)
        .filter(VoiceMonitor.user_id == user_id, live_clause())
        .order_by(VoiceMonitor.created_at.desc())
        .first()
    )


def find(db: Session, monitor_id) -> VoiceMonitor | None:
    try:
        return db.query(VoiceMonitor).filter(VoiceMonitor.id == uuid.UUID(str(monitor_id))).first()
    except (ValueError, TypeError):
        return None


def _attempt(db: Session, m: VoiceMonitor) -> VoiceCallAttempt | None:
    return db.query(VoiceCallAttempt).filter(VoiceCallAttempt.id == m.attempt_id).first()


def _name(u) -> str | None:
    return (f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email) if u else None


def listeners(db: Session, attempt_ids) -> dict:
    """attempt id -> the managers in it right now: [{name, mode}] (Live calls card)."""
    from app.models.user import User

    ids = [i for i in attempt_ids if i]
    if not ids:
        return {}
    out: dict = {}
    rows = (
        db.query(VoiceMonitor, User)
        .join(User, User.id == VoiceMonitor.user_id)
        .filter(VoiceMonitor.attempt_id.in_(ids), VoiceMonitor.status == "LIVE")
        .all()
    )
    for m, u in rows:
        out.setdefault(m.attempt_id, []).append({"user_id": str(u.id), "name": _name(u), "mode": m.mode})
    return out


def monitor_dict(db: Session, m: VoiceMonitor) -> dict:
    from app.models.user import User

    att = _attempt(db, m)
    agent = db.query(User).filter(User.id == att.agent_user_id).first() if att and att.agent_user_id else None
    return {
        "id": str(m.id),
        "user_id": str(m.user_id),
        "attempt_id": str(m.attempt_id),
        "mode": m.mode,
        "status": m.status,
        "agent_name": _name(agent),
        "customer_phone": att.phone_number if att else None,
        "call_status": att.status if att else None,
        "simulated": str(m.leg_uuid or "").startswith("sim-"),
    }


# ---- Start / join / end ----------------------------------------------------------

def start(db: Session, tenant_id, user, attempt_id) -> tuple[dict, list[dict]]:
    from app.voice_dialer import service

    if not manual.is_manager(user):
        raise MonitorError("Only managers can listen in", 403)
    if not telnyx.available():
        raise MonitorError("Calling is not set up (Telnyx is not configured)", 503)
    att = service.find_attempt(db, attempt_id, None)
    if att is None or str(att.tenant_id) != str(tenant_id):
        raise MonitorError("Call not found", 404)
    if att.status != "CONNECTED" or not att.agent_user_id:
        raise MonitorError("That call is not connected to an agent", 409)
    if str(att.agent_user_id) == str(user.id):
        raise MonitorError("This is your own call", 409)
    own_call = (
        db.query(VoiceCallAttempt.id)
        .filter(VoiceCallAttempt.agent_user_id == user.id, VoiceCallAttempt.status == "CONNECTED")
        .first()
    )
    if own_call or manual.live_call(db, user.id) is not None:
        raise MonitorError("Finish your own call first", 409)

    events: list[dict] = []
    for old in db.query(VoiceMonitor).filter(VoiceMonitor.user_id == user.id, live_clause()).all():
        events += _end(db, old)  # one call at a time: the new one replaces it
    m = VoiceMonitor(tenant_id=tenant_id, user_id=user.id, attempt_id=att.id, mode="listen", status="RINGING")
    db.add(m)
    db.commit()
    # Busy from here on: their own headset line would share this browser's audio.
    has_line = (
        db.query(SmsQueueAgent.id)
        .filter(SmsQueueAgent.user_id == user.id, SmsQueueAgent.voice_leg_uuid.isnot(None))
        .first()
    )
    if has_line:
        events += service.drop_line(db, tenant_id, str(user.id))

    if telnyx.simulating():
        m.leg_uuid = "sim-" + uuid.uuid4().hex
        on_answer(db, {"uuid": m.leg_uuid}, m.id)
    else:
        try:
            m.leg_uuid = telnyx.dial_browser(
                user.id,
                answer_url=service._url("answer", leg="monitor", sv=m.id),
                event_url=service._url("event", sv=m.id),
            )
            db.commit()
        except Exception as exc:
            logger.warning("monitor: ringing the browser failed: %s", exc)
            _end(db, m)
            db.commit()
            raise MonitorError("Could not reach your browser — try again", 502)
    db.refresh(m)
    events.append(_evt(user.id, {"id": str(m.id), "status": m.status}))
    return monitor_dict(db, m), events


def on_answer(db: Session, data: dict, monitor_id) -> list[dict]:
    """The manager's browser picked up: into the call's room, muted."""
    from app.voice_dialer import service

    m = find(db, monitor_id)
    if m is None or m.status == "ENDED":
        return [{"action": "talk", "text": "Goodbye."}]
    att = _attempt(db, m)
    if att is None or att.status != "CONNECTED":
        _end(db, m, hangup=False)
        db.commit()
        return [{"action": "talk", "text": "That call has ended."}]
    m.leg_uuid = data.get("uuid") or m.leg_uuid
    m.status = "LIVE"
    m.joined_at = _now()
    db.commit()
    return [{
        "action": "conversation", "name": service.call_room(att),
        # Never the one who starts or ends the call's room.
        "startOnEnter": False, "endOnExit": False,
        "muted": True, "label": f"sv-{m.id.hex}",
    }]


def set_mode(db: Session, m: VoiceMonitor, mode: str) -> dict:
    from app.voice_dialer import service

    if mode not in MODES:
        raise MonitorError("Unknown mode")
    if m.status != "LIVE":
        raise MonitorError("Not connected to the call yet", 409)
    if mode == m.mode:
        return monitor_dict(db, m)
    att = _attempt(db, m)
    if att is None or att.status != "CONNECTED":
        raise MonitorError("That call has ended", 409)
    room = service.call_room(att)
    db.commit()  # no carrier round trip while holding row locks
    try:
        if mode == "listen":
            telnyx.update_participant(room, m.leg_uuid, {"Muted": "true", "Coaching": "false"})
        else:
            agent = (
                db.query(SmsQueueAgent)
                .filter(SmsQueueAgent.tenant_id == att.tenant_id, SmsQueueAgent.user_id == att.agent_user_id)
                .first()
            )
            if agent is None or not agent.voice_leg_uuid:
                raise MonitorError("The agent's line is not open", 409)
            # Coaching first, still muted; unmute only once Telnyx says it is on.
            out = telnyx.update_participant(room, m.leg_uuid, {"Coaching": "true", "CallSidToCoach": agent.voice_leg_uuid})
            if not telnyx.simulating() and str((out or {}).get("coaching")).lower() != "true":
                logger.warning("monitor: coaching not confirmed, staying muted: %s", out)
                telnyx.update_participant(room, m.leg_uuid, {"Coaching": "false"})
                raise MonitorError("Whisper could not be switched on — you are still listening only", 502)
            telnyx.update_participant(room, m.leg_uuid, {"Muted": "false"})
    except telnyx.TelnyxError as exc:
        logger.warning("monitor: mode %s failed: %s", mode, exc)
        try:  # whatever happened, never leave them audible to the customer
            telnyx.update_participant(room, m.leg_uuid, {"Muted": "true", "Coaching": "false"})
        except telnyx.TelnyxError:
            pass
        m.mode = "listen"
        db.commit()
        raise MonitorError("Could not switch — you are listening only", 502)
    m.mode = mode
    db.commit()
    return monitor_dict(db, m)


def _end(db: Session, m: VoiceMonitor, hangup: bool = True) -> list[dict]:
    """Close a session (the caller commits). Hangs the leg up after commit."""
    from app.voice_dialer import service

    if m.status == "ENDED":
        return []
    m.status = "ENDED"
    m.ended_at = _now()
    if hangup:
        service._hangup_after_commit(db, [m.leg_uuid])
    return [_evt(m.user_id, {"id": str(m.id), "status": "ENDED"})]


def end(db: Session, m: VoiceMonitor) -> list[dict]:
    events = _end(db, m)
    db.commit()
    return events


def end_for_attempts(db: Session, attempt_ids) -> list[dict]:
    """The call is over: everyone listening leaves (the caller commits)."""
    ids = [i for i in attempt_ids if i]
    if not ids:
        return []
    events: list[dict] = []
    for m in db.query(VoiceMonitor).filter(VoiceMonitor.attempt_id.in_(ids), VoiceMonitor.status.in_(LIVE)).all():
        events += _end(db, m)
    return events


def on_event(db: Session, m: VoiceMonitor, data: dict) -> list[dict]:
    """The manager's leg ended (they left, closed the tab, the call ended)."""
    if str(data.get("status") or "").lower() not in _ENDED:
        return []
    events = _end(db, m, hangup=False)
    db.commit()
    return events


def sweep(db: Session, tenant_id) -> list[dict]:
    """Dialer tick: close sessions whose call ended or whose browser never answered."""
    now = _now()
    rows = (
        db.query(VoiceMonitor)
        .join(VoiceCallAttempt, VoiceCallAttempt.id == VoiceMonitor.attempt_id)
        .filter(
            VoiceMonitor.tenant_id == tenant_id,
            VoiceMonitor.status.in_(LIVE),
            or_(
                VoiceCallAttempt.status != "CONNECTED",
                and_(VoiceMonitor.status == "RINGING", VoiceMonitor.created_at <= now - timedelta(seconds=RING_SECONDS)),
            ),
        )
        .all()
    )
    events: list[dict] = []
    for m in rows:
        events += _end(db, m)
    if rows:
        db.commit()
    return events
