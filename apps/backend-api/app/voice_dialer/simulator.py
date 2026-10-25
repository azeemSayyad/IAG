"""Dev-only Vonage simulator.

Stands in for Vonage when there are no API keys (never in production — see
vonage.simulating()). It does NOT poke the dialer's internals: it builds the
exact payloads Vonage sends (Messages API inbound text, Voice answer/event
webhooks, advanced-machine-detection verdicts, record events) and feeds them to
webhooks.py — the same functions the real HTTP routes call. So a simulated
YES → call → live answer → hang up → recording runs the production code path
end to end; only the network hop from Vonage is skipped.

What a simulated call does on its own is set per tenant ("sim mode"): a live
person answers after SIM_ANSWER_AFTER_SECONDS (default), voicemail, no answer,
busy — or "manual" (wait for a click on the Auto Dialer page).
"""
from __future__ import annotations

import io
import math
import struct
import uuid
import wave
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.sms import VoiceCallAttempt
from app.voice_dialer import replies

MODES = ("human", "machine", "no_answer", "busy", "manual")
RESULTS = ("human", "machine", "no_answer", "busy", "hangup")
SIM_ANSWER_AFTER_SECONDS = 2
SIM_TALK_SECONDS = 4          # length of the fake recording audio
_mode_mem: dict[str, str] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime | None = None) -> str:
    return (d or _now()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---- mode ---------------------------------------------------------------------

def get_mode(tenant_id) -> str:
    try:
        from app.core.redis import redis_service

        v = redis_service.client.get(f"voice:sim_mode:{tenant_id}")
        if v:
            v = v.decode() if isinstance(v, bytes) else v
            return v if v in MODES else "human"
    except Exception:
        pass
    return _mode_mem.get(str(tenant_id), "human")


def set_mode(tenant_id, mode: str) -> str:
    mode = mode if mode in MODES else "human"
    _mode_mem[str(tenant_id)] = mode
    try:
        from app.core.redis import redis_service

        redis_service.client.set(f"voice:sim_mode:{tenant_id}", mode)
    except Exception:
        pass
    return mode


_hours_mem: dict[str, bool] = {}


def get_ignore_hours(tenant_id) -> bool:
    """Waive calling hours while testing (default ON — testing happens outside
    US hours). Only ever consulted while simulating."""
    try:
        from app.core.redis import redis_service

        v = redis_service.client.get(f"voice:sim_ignore_hours:{tenant_id}")
        if v is not None:
            v = v.decode() if isinstance(v, bytes) else v
            return v == "1"
    except Exception:
        pass
    return _hours_mem.get(str(tenant_id), True)


def set_ignore_hours(tenant_id, on: bool) -> bool:
    _hours_mem[str(tenant_id)] = bool(on)
    try:
        from app.core.redis import redis_service

        redis_service.client.set(f"voice:sim_ignore_hours:{tenant_id}", "1" if on else "0")
    except Exception:
        pass
    return bool(on)


# ---- Vonage-shaped payloads ---------------------------------------------------------

def _leg(att: VoiceCallAttempt) -> dict:
    """Fields Vonage puts on every Voice webhook for this call leg."""
    return {
        "uuid": att.call_uuid,
        "conversation_uuid": f"CON-{att.id}",
        "from": replies.vonage_number(att.from_number),
        "to": replies.vonage_number(att.phone_number),
        "direction": "outbound",
        "timestamp": _iso(),
    }


def text_in(db: Session, phone: str, text: str, to: str | None) -> tuple[dict, list[dict]]:
    """A client texts one of our numbers — Messages API inbound webhook."""
    from app.voice_dialer import webhooks

    payload = {
        "channel": "sms",
        "message_type": "text",
        "message_uuid": str(uuid.uuid4()),
        "from": replies.vonage_number(replies.normalize_phone(phone) or phone),
        "to": replies.vonage_number(to or "+15615550100"),
        "text": text,
        "timestamp": _iso(),
        "sms": {"num_messages": "1"},
    }
    return webhooks.inbound_sms(db, payload)


def play(db: Session, att: VoiceCallAttempt, result: str) -> list[dict]:
    """Play one outcome on a dialed call through the Voice webhooks."""
    from app.voice_dialer import webhooks

    params = {"a": str(att.id)}
    events: list[dict] = []

    def ev(status: str, **extra) -> None:
        events.extend(webhooks.event(db, {**_leg(att), "status": status, **extra}, params))

    if result == "human":
        ev("ringing")
        webhooks.answer(db, _leg(att), {**params, "leg": "customer"})
        ev("answered", start_time=_iso())
        # Advanced machine detection verdict.
        ev("human", call_uuid=att.call_uuid)
    elif result == "machine":
        ev("ringing")
        webhooks.answer(db, _leg(att), {**params, "leg": "customer"})
        ev("answered", start_time=_iso())
        ev("machine", call_uuid=att.call_uuid, sub_state="beep_start")
        ev("completed", duration="9", start_time=_iso(), end_time=_iso())
    elif result == "no_answer":
        ev("ringing")
        ev("timeout")
    elif result == "busy":
        ev("busy")
    elif result == "hangup":
        db.refresh(att)
        talk = int((_now() - att.connected_at).total_seconds()) if att.connected_at else 0
        ev("completed", duration=str(max(talk, 1)), start_time=_iso(att.connected_at), end_time=_iso())
    return events


def hangup(db: Session, att: VoiceCallAttempt) -> list[dict]:
    """Our side hung the customer up (agent's Hang up): Vonage reports completed."""
    return play(db, att, "hangup")


# ---- recordings -----------------------------------------------------------------

def fake_audio(seconds: int = SIM_TALK_SECONDS) -> bytes:
    """A short two-channel WAV (customer tone left, agent tone right), standing
    in for the call audio Vonage would record."""
    rate = 8000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(rate * seconds):
            t = i / rate
            # Alternate who "speaks" each half second, so the two channels differ.
            left = 0.3 * math.sin(2 * math.pi * 440 * t) if int(t * 2) % 2 == 0 else 0.0
            right = 0.3 * math.sin(2 * math.pi * 660 * t) if int(t * 2) % 2 == 1 else 0.0
            frames += struct.pack("<hh", int(left * 32767), int(right * 32767))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _send_recordings(db: Session, tenant_id) -> None:
    """A connected call that has ended gets its record event, like Vonage sends
    a moment after hang-up."""
    from app.voice_dialer import webhooks

    cutoff = _now() - timedelta(seconds=1)
    for att in (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status == "COMPLETED",
            VoiceCallAttempt.connected_at.isnot(None),
            VoiceCallAttempt.recording_id.is_(None),
            VoiceCallAttempt.ended_at < cutoff,
        )
        .all()
    ):
        rec = str(uuid.uuid4())
        webhooks.recording(db, {
            "start_time": _iso(att.connected_at),
            "end_time": _iso(att.ended_at),
            "recording_url": f"sim://recording/{rec}",
            "recording_uuid": rec,
            "size": len(fake_audio()),
            "conversation_uuid": f"CON-{att.id}",
            "timestamp": _iso(),
        }, {"a": str(att.id)})


# ---- the heartbeat ----------------------------------------------------------------

def housekeeping(db: Session, tenant_id) -> list[dict]:
    """Called from the dialer tick while simulating: answer pending calls per
    the sim mode, and deliver recordings for calls that ended."""
    events: list[dict] = []
    mode = get_mode(tenant_id)
    if mode != "manual":
        cutoff = _now() - timedelta(seconds=SIM_ANSWER_AFTER_SECONDS)
        for att in (
            db.query(VoiceCallAttempt)
            .filter(
                VoiceCallAttempt.tenant_id == tenant_id,
                VoiceCallAttempt.direction == "outbound",
                VoiceCallAttempt.status.in_(("DIALING", "RINGING")),
                VoiceCallAttempt.created_at < cutoff,
            )
            .all()
        ):
            events += play(db, att, mode)
    _send_recordings(db, tenant_id)
    return events
