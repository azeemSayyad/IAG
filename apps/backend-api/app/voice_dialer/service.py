"""YES-reply auto dialer — the engine.

Flow (spec: "Automation Spec: YES-Reply Auto Dialer with Screen Pop"):

  1. A client texts YES  -> on_inbound_sms(): the lead (matched by PHONE) becomes
     READY_TO_CALL. READY_TO_CALL is not QUEUED, so the regular pool / offer
     popup never sees it.
  2. tick()  -> claims a ready lead (READY_TO_CALL -> DIALING is the lock: one
     lead, one call, one agent) and dials it from the number the client texted,
     with machine detection on. Pacing is fixed at ONE call per free agent on
     the line: YES replies trickle in, so dialing ahead of agents would only
     risk abandoned calls for no gain.
  3. A live person answers -> connect_human(): claim the longest-idle agent whose
     headset line is open, assign the lead to them (IN_PROGRESS — no Accept/Pass,
     they are already on the phone), transfer the customer into the agent's
     conversation (an instant bridge, no ring), THEN pop the lead card on that
     agent's screen. Voicemail / busy / no answer never reach an agent: hang up,
     log the attempt, requeue with a retry time.
  4. Live person but no free agent -> short message with our name, a callback
     number and "press 9 to opt out", then requeue (an abandoned call).

Every rule is checked again right before each dial: DNC, calling hours in the
client's local time, at most N attempts per number per 24 h, and never two
calls to the same number at once.

The module is synchronous and returns socket events as a list (same contract as
sms_queue.queue_service) — callers flush them with flush_events().
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.sms import (
    SmsAgentAction,
    SmsLead,
    SmsMessage,
    SmsPoolBatch,
    SmsQueueAgent,
    VoiceCallAttempt,
    VoiceDialerSettings,
)
from app.sms_queue.services import queue_service as qs
from app.voice_dialer import replies, rules, vonage

logger = logging.getLogger(__name__)

IN_FLIGHT = ("DIALING", "RINGING", "ANSWERED")
FINAL = ("COMPLETED", "MACHINE", "NO_ANSWER", "BUSY", "FAILED", "ABANDONED", "CANCELLED", "MISSED")
# Vonage's default limit is 3 new calls per second (CPS). A tick never starts
# more than this many calls, and ticks are seconds apart, so a backlog (e.g. the
# overnight YES replies all due at 8 am) can't trip it. Raise both together.
MAX_CALLS_PER_TICK = 3
DETECT_GRACE_SECONDS = 6      # answered but no machine verdict yet -> treat as human (speed wins)
LOST_EVENT_SECONDS = 60       # in-flight this long past the ring timer -> assume the final event was lost
DEFAULT_BUSINESS = "Insurance Alliance Group"

END_STATUS = {
    "busy": "BUSY", "rejected": "BUSY",
    "timeout": "NO_ANSWER", "unanswered": "NO_ANSWER", "completed": "NO_ANSWER",
    "failed": "FAILED", "cancelled": "CANCELLED",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evt(to: str, id_, event: str, data: dict) -> dict:
    return {"to": to, "id": str(id_) if id_ else None, "event": event, "data": data}


def flush_events(events: list[dict]) -> None:
    """Publish events through the Redis-backed write-only emitter, so they reach
    browsers from the web server, a webhook or the Celery tick alike."""
    from app.sms_queue.services.inbound_sync import _emit

    for e in events or []:
        to, _id = e.get("to"), e.get("id")
        if not _id:
            continue
        room = {"agent": "agent", "user": "user", "tenant": "tenant"}.get(to)
        if room:
            _emit(f"{room}:{_id}", e["event"], e["data"])


# ---- Settings ---------------------------------------------------------------

def get_settings(db: Session, tenant_id) -> VoiceDialerSettings:
    s = db.query(VoiceDialerSettings).filter(VoiceDialerSettings.tenant_id == tenant_id).first()
    if s is None:
        s = VoiceDialerSettings(tenant_id=tenant_id)
        db.add(s)
        db.flush()
    return s


def settings_dict(s: VoiceDialerSettings) -> dict:
    return {
        "enabled": bool(s.enabled),
        "ring_seconds": int(s.ring_seconds or 25),
        "max_attempts_24h": int(s.max_attempts_24h or 3),
        "max_total_attempts": int(s.max_total_attempts or 6),
        "retry_minutes": int(s.retry_minutes or 30),
        "reply_delay_seconds": int(s.reply_delay_seconds if s.reply_delay_seconds is not None else 3),
        "window_start_hour": int(s.window_start_hour if s.window_start_hour is not None else 8),
        "window_end_hour": int(s.window_end_hour if s.window_end_hour is not None else 20),
        "business_name": s.business_name or "",
        "callback_number": s.callback_number or "",
        **_inbound().settings_part(s),
    }


def _inbound():
    from app.voice_dialer import inbound

    return inbound


def _clamp(v, lo, hi, cast=int):
    try:
        return max(lo, min(hi, cast(v)))
    except (TypeError, ValueError):
        return None


def update_settings(db: Session, tenant_id, data: dict) -> dict:
    """Admin edit. Every limit is clamped to a compliant range, so the UI can't
    configure an illegal dialer (ring < 15 s, > 3 attempts / 24 h, 9 pm calls)."""
    s = get_settings(db, tenant_id)
    if "enabled" in data:
        s.enabled = bool(data["enabled"])
    bounds = {
        "ring_seconds": (15, 45, int),
        "max_attempts_24h": (1, 3, int),
        "max_total_attempts": (1, 20, int),
        "retry_minutes": (5, 24 * 60, int),
        "reply_delay_seconds": (0, 30, int),
        "window_start_hour": (8, 12, int),
        "window_end_hour": (12, 20, int),
    }
    for key, (lo, hi, cast) in bounds.items():
        if key in data:
            v = _clamp(data[key], lo, hi, cast)
            if v is not None:
                setattr(s, key, v)
    if "business_name" in data:
        s.business_name = (str(data["business_name"] or "").strip()[:120]) or None
    if "callback_number" in data:
        raw = str(data["callback_number"] or "").strip()
        s.callback_number = (replies.normalize_phone(raw) or raw[:32]) or None
    _inbound().update_settings_part(s, data)
    db.commit()
    return settings_dict(s)


def owner_tenant_id(db: Session) -> str | None:
    """The tenant our Vonage numbers belong to (webhooks carry no tenant)."""
    tid = (settings.VONAGE_TENANT_ID or "").strip()
    if tid:
        return tid
    from app.models.tenant import Tenant

    rows = db.query(Tenant.id).limit(2).all()
    return str(rows[0][0]) if len(rows) == 1 else None


def _url(path: str, **params) -> str:
    base = (settings.VOICE_CALLBACK_BASE_URL or settings.FRONTEND_URL or "").rstrip("/")
    q = {k: str(v) for k, v in params.items() if v is not None}
    if settings.VONAGE_WEBHOOK_TOKEN:
        q["k"] = settings.VONAGE_WEBHOOK_TOKEN
    return f"{base}/api/v1/voice/vonage/{path}" + (f"?{urlencode(q)}" if q else "")


def conversation_name(user_id) -> str:
    """The agent's private room. Their headset line sits in it all shift; a live
    customer is transferred in, which is why the bridge is instant."""
    return "iag-agent-" + str(user_id).replace("-", "")


# ---- Lead lookup / card ----------------------------------------------------------

def _phone_match(column, phone: str):
    digits = func.regexp_replace(column, r"\D", "", "g")
    return func.right(digits, 10) == replies.last10(phone)


def find_lead(db: Session, tenant_id, phone: str) -> SmsLead | None:
    """Match by PHONE, never by name (Vonage names are often missing or garbled).
    Prefer a lead that is still live over an old dispositioned one."""
    return (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, _phone_match(SmsLead.phone_number, phone))
        .order_by(case((SmsLead.status == "DISPOSITIONED", 1), else_=0), SmsLead.created_at.desc())
        .first()
    )


def _source_lead(db: Session, tenant_id, phone: str):
    from app.models.lead import Lead

    return (
        db.query(Lead)
        .filter(Lead.tenant_id == tenant_id, _phone_match(Lead.phone, phone))
        .order_by(Lead.created_at.desc())
        .first()
    )


def _detail(lead: SmsLead, pattern: str) -> str | None:
    rx = re.compile(pattern, re.I)
    for pair in (lead.details or {}).get("fields") or []:
        try:
            label, value = pair[0], pair[1]
        except (IndexError, TypeError):
            continue
        if value and rx.search(str(label)):
            return str(value)
    return None


def _src(db: Session, lead: SmsLead):
    if not lead.lead_id:
        return None
    from app.models.lead import Lead

    return db.query(Lead).filter(Lead.id == lead.lead_id).first()


def lead_state(db: Session, lead: SmsLead, src=None) -> str | None:
    src = src if src is not None else _src(db, lead)
    raw = (getattr(src, "state", None) if src else None) or _detail(lead, r"^(state|st|state_code)$")
    if not raw:
        m = re.search(r"\b([A-Z]{2})\b(?:\s+\d{5})?\s*$", (lead.details or {}).get("address") or "")
        raw = m.group(1) if m else None
    return rules.state_code(raw) or (raw.upper() if raw else None)


def lead_card(db: Session, lead: SmsLead, att: VoiceCallAttempt | None = None) -> dict:
    """Everything the screen pop shows: name, phone, state, DOB, the YES reply,
    the list it came from and any notes."""
    src = _src(db, lead)
    card = qs._lead_dict(lead)
    if not card.get("address"):
        card["address"] = qs._compose_address(src)
    meta = (getattr(src, "custom_fields", None) or {}) if src else {}
    dob = _detail(lead, r"(dob|birth)")
    if not dob and isinstance(meta, dict):
        dob = meta.get("dob") or meta.get("date_of_birth")
    list_name = None
    if lead.batch_id:
        b = db.query(SmsPoolBatch.name).filter(SmsPoolBatch.id == lead.batch_id).first()
        list_name = b[0] if b else None
    if not list_name and src is not None and getattr(src, "campaign_id", None):
        from app.models.campaign import Campaign

        c = db.query(Campaign.name).filter(Campaign.id == src.campaign_id).first()
        list_name = c[0] if c else None
    card.update({
        "state": lead_state(db, lead, src),
        "dob": dob,
        "list_name": list_name or ("SMS reply" if (lead.source or "REPLY") == "REPLY" else None),
        "notes": getattr(src, "notes", None) if src else None,
        "yes_reply": lead.yes_reply,
    })
    if att is not None:
        card["call"] = attempt_dict(att)
    return card


def attempt_dict(a: VoiceCallAttempt) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "id": str(a.id),
        "sms_lead_id": str(a.sms_lead_id) if a.sms_lead_id else None,
        "phone_number": a.phone_number,
        "from_number": a.from_number,
        "direction": a.direction,
        "status": a.status,
        "agent_user_id": str(a.agent_user_id) if a.agent_user_id else None,
        "disposition": a.disposition,
        "error": a.error,
        "created_at": iso(a.created_at),
        "answered_at": iso(a.answered_at),
        "human_at": iso(a.human_at),
        "connected_at": iso(a.connected_at),
        "ended_at": iso(a.ended_at),
        "duration_seconds": a.duration_seconds,
    }


# ---- Inbound text ------------------------------------------------------------------

def on_inbound_sms(
    db: Session, tenant_id, from_raw: str, to_raw: str | None, text: str | None,
    message_id: str | None = None,
) -> tuple[dict, list[dict]]:
    """One inbound text from Vonage (the owner's rule): every reply is called,
    reply_delay_seconds after it lands, EXCEPT a negative one — STOP, a plain
    no / not interested, abuse, wrong number — which goes on Do-Not-Call
    forever. A client an agent already closed is reopened only by a CLEAR yes;
    any other follow-up ("thank you") is just saved to their thread."""
    if message_id and db.query(SmsMessage.id).filter(
        SmsMessage.tenant_id == tenant_id, SmsMessage.provider_message_id == message_id
    ).first():
        return {"ok": True, "result": "duplicate"}, []
    phone = replies.normalize_phone(from_raw)
    if not phone:
        return _inbound_sms_international(db, tenant_id, from_raw, text, message_id)

    kind = replies.classify(text)
    now = _now()
    lead = find_lead(db, tenant_id, phone)
    created = lead is None
    if created:
        src = _source_lead(db, tenant_id, phone)
        name = f"{getattr(src, 'first_name', '') or ''} {getattr(src, 'last_name', '') or ''}".strip() if src else ""
        lead = SmsLead(
            tenant_id=tenant_id,
            lead_id=src.id if src else None,
            phone_number=phone,
            customer_name=name or (
                "Unknown, replied YES" if kind == replies.YES
                else "Unknown, replied" if kind == replies.REPLY else None),
            source="REPLY",
            priority="HOT" if kind in (replies.YES, replies.REPLY) else "WARM",
            status="QUEUED",
            message_count=0,
        )
        db.add(lead)
        db.flush()
    msg = SmsMessage(
        tenant_id=tenant_id, sms_lead_id=lead.id, phone_number=phone, direction="INBOUND",
        body=text or "", sender_type="CUSTOMER", status="RECEIVED", provider="vonage",
        provider_message_id=message_id,
    )
    db.add(msg)
    lead.last_message = text
    lead.message_count = (lead.message_count or 0) + 1
    lead.updated_at = now

    events: list[dict] = []
    result = kind.lower()
    s = get_settings(db, tenant_id)
    if kind in replies.NEGATIVE_KINDS:
        # Every negative blocks every future call AND text to this number —
        # until they clearly opt back in by text (_opt_back_in).
        qs.add_to_dnc(db, tenant_id, phone, replies.DNC_REASON[kind])
        msg.status = "OPT_OUT"
        if lead.status in ("QUEUED", "READY_TO_CALL", "DIALING", "ASSIGNED"):
            if lead.status == "DIALING":
                live = _live_attempt_for_lead(db, lead.id)
                if live is not None:
                    vonage.hangup(live.call_uuid)
            if lead.status == "ASSIGNED" and lead.assigned_agent_id:
                holder = qs._get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
                if str(holder.current_lead_id) == str(lead.id):
                    holder.current_lead_id = None
                events.append(_evt("agent", lead.assigned_agent_id, "sms:queue_updated", {"reason": "lead_opted_out"}))
                lead.assigned_agent_id = None
            lead.status = "DISPOSITIONED"
            lead.disposition = {
                replies.WRONG: "WRONG_NUMBER", replies.NEGATIVE: "NOT_INTERESTED",
            }.get(kind, "UNQUALIFIED")
            lead.dispositioned_at = now
            lead.next_dial_at = None
    elif kind == replies.REPLY and not created and lead.status == "DISPOSITIONED":
        # Already helped and closed by an agent: "thank you" / "ok" must not
        # ring them again. Saved to the thread; a clear YES still reopens.
        result = "reply_saved"
    elif kind in (replies.YES, replies.REPLY):
        lead.yes_reply = text
        lead.yes_at = now
        lead.priority = "HOT"
        to_e164 = replies.normalize_phone(to_raw)
        if to_e164:
            lead.dial_from = to_e164
        opted_out = qs.is_dnc(db, tenant_id, phone)
        if opted_out and kind == replies.YES:
            # They opted out before and now clearly ask for us again: back to a
            # normal lead — off the list, texts and calls allowed — and dialed.
            _opt_back_in(db, tenant_id, phone, msg, text, lead.id)
            opted_out = False
            if lead.status == "DISPOSITIONED":
                lead.disposition = None
                lead.dispositioned_at = None
        if opted_out:
            result = "reply_saved_opted_out"  # vague text from an opted-out number: saved only
        elif lead.status == "ASSIGNED" and s.enabled and vonage.available():
            # Sitting in someone's unaccepted "New lead" popup: nobody is working
            # it yet, so the YES wins — pull the offer back and dial them.
            if lead.assigned_agent_id:
                holder = qs._get_or_create_agent(db, tenant_id, str(lead.assigned_agent_id))
                if str(holder.current_lead_id) == str(lead.id):
                    holder.current_lead_id = None
                events.append(_evt("agent", lead.assigned_agent_id, "sms:queue_updated", {"reason": "offer_reclaimed_for_call"}))
            lead.status = "READY_TO_CALL"
            lead.next_dial_at = now + timedelta(seconds=_reply_delay(s))
            lead.dial_attempts = 0
            lead.assigned_agent_id = None
            result = "ready_to_call"
        elif lead.status in ("IN_PROGRESS", "ASSIGNED", "DIALING"):
            result = "yes_already_active"  # someone has them / a call is already in flight
        elif s.enabled and vonage.available():
            lead.status = "READY_TO_CALL"
            # The owner's "don't ring them the very second they reply" pause.
            lead.next_dial_at = now + timedelta(seconds=_reply_delay(s))
            lead.dial_attempts = 0         # a fresh YES restarts the retry budget (24h cap still applies)
            lead.assigned_agent_id = None
            lead.disposition = None
            lead.dispositioned_at = None
            result = "ready_to_call"
        else:
            # Dialer off: the YES still reaches a human through the regular pool.
            if lead.status == "DISPOSITIONED":
                lead.status = "QUEUED"
                lead.disposition = None
                lead.dispositioned_at = None
            result = "yes_to_pool"
    db.commit()

    payload = qs._msg_dict(msg)
    events.append(_evt("tenant", tenant_id, "sms:new_message", payload))
    if lead.assigned_agent_id:
        events.append(_evt("agent", lead.assigned_agent_id, "sms:new_message", payload))
    events.append(_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "vonage_inbound"}))
    if lead.status == "QUEUED" and kind not in replies.NEGATIVE_KINDS:
        _d, ev = qs.assign_next(db, tenant_id)
        events += ev
    if result == "ready_to_call":
        delay = _reply_delay(s)
        if delay:
            _tick_later(str(tenant_id), delay)
        else:
            events += tick(db, tenant_id)
    return {"ok": True, "result": result, "lead_id": str(lead.id), "kind": kind}, events


def _opt_back_in(db: Session, tenant_id, phone: str, msg: SmsMessage, text: str | None, lead_id=None) -> None:
    """Their own clear yes after a STOP: remove every Do-Not-Call entry for the
    number and keep the proof — the message is marked OPT_IN (shown on the
    Inbox thread) and the audit log records what they wrote."""
    from app.models.sms import SmsDoNotCall

    digits = qs._dnc_phone(phone)
    rows = db.query(SmsDoNotCall).filter(SmsDoNotCall.tenant_id == tenant_id, SmsDoNotCall.phone_number == digits).all()
    reasons = [r.reason for r in rows]
    for r in rows:
        db.delete(r)
    msg.status = "OPT_IN"
    try:
        from app.core.audit import log_ai_action

        log_ai_action(tenant_id=str(tenant_id), action="dnc_opt_in_by_text", resource_type="sms_lead",
                      resource_id=str(lead_id) if lead_id else None,
                      details={"phone": phone, "text": (text or "")[:160], "was": reasons})
    except Exception as exc:
        logger.warning("voice dialer: opt-in audit failed: %s", exc)


def _inbound_sms_international(db: Session, tenant_id, from_raw: str, text: str | None,
                               message_id: str | None) -> tuple[dict, list[dict]]:
    """A text from a NON-US number: saved and shown in the Auto Dialer Inbox,
    never dialed (the dialer, its calling hours and Do-Not-Call are US-only).
    Matched by the EXACT number — last-10-digit matching could collide with a
    US client. The lead is parked as DISPOSITIONED so the pool never offers it."""
    digits = re.sub(r"\D", "", str(from_raw or ""))
    if not 8 <= len(digits) <= 15:
        return {"ok": False, "reason": "bad_number"}, []
    phone = "+" + digits
    now = _now()
    lead = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.phone_number == phone)
        .order_by(SmsLead.created_at.desc())
        .first()
    )
    if lead is None:
        lead = SmsLead(
            tenant_id=tenant_id, phone_number=phone, customer_name="Unknown (not a US number)",
            source="REPLY", priority="WARM", status="DISPOSITIONED", message_count=0,
            dispositioned_at=now,
        )
        db.add(lead)
        db.flush()
    db.add(SmsMessage(
        tenant_id=tenant_id, sms_lead_id=lead.id, phone_number=phone, direction="INBOUND",
        body=text or "", sender_type="CUSTOMER", status="RECEIVED", provider="vonage",
        provider_message_id=message_id,
    ))
    lead.last_message = text
    lead.message_count = (lead.message_count or 0) + 1
    lead.updated_at = now
    db.commit()
    return {"ok": True, "result": "saved_not_us"}, [
        _evt("tenant", tenant_id, "voice:dialer_updated", {"reason": "inbound_sms"}),
    ]


def _reply_delay(s: VoiceDialerSettings) -> int:
    return max(0, min(30, int(s.reply_delay_seconds if s.reply_delay_seconds is not None else 3)))


def _tick_later(tenant_id: str, seconds: int) -> None:
    """Run the dialer once the reply delay is over, so the call goes out ~on
    time instead of on the next poll / Celery beat (which also cover it)."""
    import threading

    def go():
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            tick(db, tenant_id)
        except Exception:
            logger.exception("voice dialer: delayed tick failed")
        finally:
            db.close()

    t = threading.Timer(seconds + 0.2, go)
    t.daemon = True
    t.start()


# ---- Pacing / dialing ----------------------------------------------------------

def _ready_agents(db: Session, tenant_id) -> list[SmsQueueAgent]:
    """AVAILABLE, holding nothing, Ready for calls with the headset line open —
    longest-idle first."""
    return [a for a in qs._available_agents(db, tenant_id) if a.voice_leg_uuid and a.voice_ready]


def _in_flight(db: Session, tenant_id) -> list[VoiceCallAttempt]:
    return (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.direction == "outbound",
            VoiceCallAttempt.status.in_(IN_FLIGHT),
        )
        .all()
    )


def _today_start() -> datetime:
    from zoneinfo import ZoneInfo

    local = _now().astimezone(ZoneInfo("America/New_York"))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


PERIODS = ("today", "yesterday", "week", "month", "all", "custom")


def period_bounds(period: str = "today", date_from: str | None = None, date_to: str | None = None):
    """(start, end) in UTC for a stats period, days counted in Eastern time.
    Week = Monday to now (same weeks as agent pay); custom = whole days
    date_from..date_to inclusive. None means open-ended."""
    from datetime import date
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    today = _now().astimezone(et).date()

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=et).astimezone(timezone.utc)

    if period == "yesterday":
        return at(today - timedelta(days=1)), at(today)
    if period == "week":
        return at(today - timedelta(days=today.weekday())), None
    if period == "month":
        return at(today.replace(day=1)), None
    if period == "all":
        return None, None
    if period == "custom":
        def parse(v):
            try:
                return date.fromisoformat(v) if v else None
            except ValueError:
                return None
        a, b = parse(date_from), parse(date_to)
        if a and b and b < a:
            a, b = b, a
        return (at(a) if a else None), (at(b + timedelta(days=1)) if b else None)
    return at(today), None


def _in_period(q, start, end):
    if start is not None:
        q = q.filter(VoiceCallAttempt.created_at >= start)
    if end is not None:
        q = q.filter(VoiceCallAttempt.created_at < end)
    return q


def today_counts(db: Session, tenant_id, start=None, end=None) -> dict:
    """Call counts for a period (default: today, Eastern)."""
    if start is None and end is None:
        start = _today_start()
    rows = (
        _in_period(db.query(VoiceCallAttempt.status, func.count(VoiceCallAttempt.id)), start, end)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.direction == "outbound",
        )
        .group_by(VoiceCallAttempt.status)
        .all()
    )
    by = {s: int(n) for s, n in rows}
    humans = (
        _in_period(db.query(func.count(VoiceCallAttempt.id)), start, end)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.direction == "outbound",
            VoiceCallAttempt.human_at.isnot(None),
        )
        .scalar()
        or 0
    )
    abandoned = by.get("ABANDONED", 0)
    results = dict(
        _in_period(db.query(VoiceCallAttempt.disposition, func.count(VoiceCallAttempt.id)), start, end)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.disposition.isnot(None),
        )
        .group_by(VoiceCallAttempt.disposition)
        .all()
    )
    return {
        "sales": int(results.get("SALE", 0)),
        "appointments": int(results.get("APPOINTMENT_SET", 0)),
        "dialed": sum(by.values()),
        "live_answers": int(humans),
        "connected": by.get("CONNECTED", 0) + by.get("COMPLETED", 0),
        "machine": by.get("MACHINE", 0),
        "no_answer": by.get("NO_ANSWER", 0) + by.get("BUSY", 0),
        "failed": by.get("FAILED", 0) + by.get("CANCELLED", 0),
        "abandoned": abandoned,
        "abandon_rate_pct": round(abandoned / humans * 100, 1) if humans else 0.0,
        "by_status": by,
    }


def today_counts_all(db: Session, tenant_id) -> dict:
    """Counts over every call ever made."""
    return today_counts(db, tenant_id, datetime(1970, 1, 1, tzinfo=timezone.utc), None)


def _attempts_24h(db: Session, tenant_id, phone: str) -> list[datetime]:
    since = _now() - timedelta(hours=24)
    rows = (
        db.query(VoiceCallAttempt.created_at)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.direction == "outbound",
            VoiceCallAttempt.phone_number == phone,
            VoiceCallAttempt.created_at >= since,
        )
        .order_by(VoiceCallAttempt.created_at.asc())
        .all()
    )
    return [r[0] for r in rows]


def fleet_numbers() -> list[str]:
    out = []
    for raw in (settings.VONAGE_FROM_NUMBERS or "").split(","):
        n = replies.normalize_phone(raw)
        if n:
            out.append(n)
    return out


def _caller_id(db: Session, tenant_id, lead: SmsLead) -> str | None:
    """The number the client texted (they recognise it); else the least-used
    fleet number today, so the pool rotates and no number gets flagged."""
    if lead.dial_from:
        return lead.dial_from
    fleet = fleet_numbers()
    if not fleet:
        return "+15550000000" if vonage.simulating() else None
    used = dict(
        db.query(VoiceCallAttempt.from_number, func.count(VoiceCallAttempt.id))
        .filter(VoiceCallAttempt.tenant_id == tenant_id, VoiceCallAttempt.created_at >= _today_start())
        .group_by(VoiceCallAttempt.from_number)
        .all()
    )
    return min(fleet, key=lambda n: used.get(n, 0))


def _tick_lock(tenant_id) -> bool:
    """Serialise ticks per tenant (beat + webhooks + agent polls all call tick),
    so two of them can't both see spare capacity and over-dial. Redis down ->
    run anyway; the lead claim itself is still atomic."""
    try:
        from app.core.redis import redis_service

        return bool(redis_service.client.set(f"voice:tick:{tenant_id}", "1", nx=True, px=4000))
    except Exception:
        return True


def _cps_slot(tenant_id) -> bool:
    """At most one dialing batch (<= MAX_CALLS_PER_TICK calls) per second per
    tenant, however many ticks fire — keeps us under Vonage's CPS limit."""
    try:
        from app.core.redis import redis_service

        return bool(redis_service.client.set(f"voice:cps:{tenant_id}", "1", nx=True, px=1000))
    except Exception:
        return True


def _tick_unlock(tenant_id) -> None:
    try:
        from app.core.redis import redis_service

        redis_service.client.delete(f"voice:tick:{tenant_id}")
    except Exception:
        pass


def tick(db: Session, tenant_id) -> list[dict]:
    """Housekeeping + start as many calls as pacing allows. Safe to call often."""
    if not _tick_lock(tenant_id):
        return []
    try:
        return _tick(db, tenant_id)
    except Exception:
        db.rollback()
        logger.exception("voice dialer tick failed for tenant %s", tenant_id)
        return []
    finally:
        _tick_unlock(tenant_id)


def _tick(db: Session, tenant_id) -> list[dict]:
    from app.voice_dialer import inbound

    s = get_settings(db, tenant_id)
    events = _housekeeping(db, tenant_id, s)
    # Callers on hold come first: they get the free agents before any new
    # outbound call is started.
    try:
        events += inbound.serve_holding(db, tenant_id, s)
    except Exception as exc:
        db.rollback()
        logger.warning("voice dialer: serving held callers failed: %s", exc)
    if not s.enabled or not vonage.available():
        db.commit()
        return events

    now = _now()
    candidates = (
        db.query(SmsLead)
        .filter(
            SmsLead.tenant_id == tenant_id,
            SmsLead.status == "READY_TO_CALL",
            (SmsLead.next_dial_at.is_(None)) | (SmsLead.next_dial_at <= now),
        )
        .order_by(SmsLead.yes_at.asc().nullsfirst(), SmsLead.created_at.asc())
        .limit(60)
        .all()
    )
    if not candidates:
        db.commit()
        return events
    flying = _in_flight(db, tenant_id)
    held = inbound.holding(db, tenant_id)
    if held:
        db.commit()
        return events  # nobody new is dialed while a caller waits on hold
    # One call per free agent on the line — never more calls than agents to take
    # them — and never more than Vonage's calls-per-second limit in one go.
    capacity = min(len(_ready_agents(db, tenant_id)) - len(flying), MAX_CALLS_PER_TICK)
    if capacity <= 0:
        due = sum(
            1 for l in candidates
            if _window_ok(now, lead_state(db, l), s, tenant_id)
        )
        if due:
            events += _reclaim_offers(db, tenant_id)
            capacity = min(len(_ready_agents(db, tenant_id)) - len(flying), MAX_CALLS_PER_TICK)
    if capacity <= 0 or not _cps_slot(tenant_id):
        db.commit()
        return events

    busy_phones = {a.phone_number for a in flying}
    started = 0
    for lead in candidates:
        if started >= capacity:
            break
        phone = replies.normalize_phone(lead.phone_number)
        if not phone:
            lead.status = "QUEUED"  # can't be dialed — a human works it from the pool
            continue
        if phone in busy_phones:
            continue  # never two calls to the same person at once
        if qs.is_dnc(db, tenant_id, phone):
            lead.status = "DISPOSITIONED"
            lead.disposition = lead.disposition or "UNQUALIFIED"
            lead.dispositioned_at = now
            continue
        if not replies.is_test_number(phone) and not _window_ok(now, lead_state(db, lead), s, tenant_id):
            continue  # stays Ready; picked up once their local window opens
        recent = _attempts_24h(db, tenant_id, phone)
        if len(recent) >= s.max_attempts_24h:
            lead.next_dial_at = recent[0] + timedelta(hours=24)
            continue
        from_ = _caller_id(db, tenant_id, lead)
        if not from_:
            logger.warning("voice dialer: no caller ID (set VONAGE_FROM_NUMBERS)")
            break
        # The lock: only one claimer can move READY_TO_CALL -> DIALING.
        claimed = (
            db.query(SmsLead)
            .filter(SmsLead.id == lead.id, SmsLead.status == "READY_TO_CALL")
            .update(
                {"status": "DIALING", "dial_attempts": SmsLead.dial_attempts + 1, "updated_at": now},
                synchronize_session=False,
            )
        )
        if claimed != 1:
            continue
        att = VoiceCallAttempt(
            tenant_id=tenant_id, sms_lead_id=lead.id, phone_number=phone, from_number=from_,
            direction="outbound", status="DIALING",
        )
        db.add(att)
        db.commit()
        db.refresh(lead)
        try:
            att.call_uuid = vonage.create_call(
                to=replies.vonage_number(phone),
                from_=replies.vonage_number(from_),
                answer_url=_url("answer", leg="customer", a=att.id),
                event_url=_url("event", a=att.id),
                ring_seconds=s.ring_seconds,
            )
            db.commit()
        except Exception as exc:
            att.status = "FAILED"
            att.error = str(exc)[:500]
            att.ended_at = _now()
            events += _requeue(db, lead, s)
            db.commit()
            logger.warning("voice dialer: create call failed: %s", exc)
            continue
        busy_phones.add(phone)
        started += 1
    db.commit()
    if started:
        events.append(_evt("tenant", tenant_id, "voice:dialer_updated", {"reason": "dialing", "started": started}))
    return events


def _reclaim_offers(db: Session, tenant_id) -> list[dict]:
    """A YES-responder outranks a list lead: pull back offers an agent on an open
    line has not accepted yet (the popup is still up), so that agent is free for
    the dialer. The offered lead goes back to the pool untouched — not a Pass."""
    events: list[dict] = []
    rows = (
        db.query(SmsQueueAgent, SmsLead)
        .join(SmsLead, SmsLead.id == SmsQueueAgent.current_lead_id)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.status == "AVAILABLE",
            SmsQueueAgent.voice_leg_uuid.isnot(None),
            SmsLead.status == "ASSIGNED",
        )
        .all()
    )
    for agent, lead in rows:
        lead.status = "QUEUED"
        lead.assigned_agent_id = None
        agent.current_lead_id = None
        events.append(_evt("agent", agent.user_id, "sms:queue_updated", {"reason": "offer_reclaimed_for_call"}))
    if rows:
        db.commit()
        events.append(_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "offer_reclaimed_for_call"}))
    return events


def _housekeeping(db: Session, tenant_id, s: VoiceDialerSettings) -> list[dict]:
    events: list[dict] = []
    # Agents whose browser stopped checking in (closed tab / laptop) go OFFLINE
    # and release what they held — the same reaper the Celery queue job runs,
    # here too so the dialer never counts a gone agent (and local dev, which
    # has no Celery, behaves like production).
    try:
        _d, ev = qs.reap_stale_agents(db, str(tenant_id))
        events += ev
    except Exception as exc:
        db.rollback()
        logger.warning("voice dialer: reap_stale_agents failed: %s", exc)
    # Commit what the reaper changed before anything below can wait on the
    # network: an open transaction holding agent rows while we talk to Vonage
    # made every agent's status poll queue up behind it (pool exhaustion).
    db.commit()
    if vonage.simulating():
        from app.voice_dialer import simulator

        events += simulator.housekeeping(db, tenant_id)
    now = _now()
    hangups: list[str | None] = []  # sent after the commit below
    # An answered call with no machine verdict yet: connect it — dead air while
    # we wait is what makes people hang up (the spec: speed wins).
    for att in (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status == "ANSWERED",
            VoiceCallAttempt.answered_at < now - timedelta(seconds=DETECT_GRACE_SECONDS),
        )
        .all()
    ):
        events += connect_human(db, att)
    # Lost final event: treat as unanswered and requeue.
    cutoff = now - timedelta(seconds=int(s.ring_seconds or 25) + LOST_EVENT_SECONDS)
    for att in (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status.in_(("DIALING", "RINGING")),
            VoiceCallAttempt.created_at < cutoff,
        )
        .all()
    ):
        hangups.append(att.call_uuid)
        att.status = "NO_ANSWER"
        att.error = "no final event from Vonage"
        att.ended_at = now
        events += _requeue(db, _lead_of(db, att), s)
    # A call still "connected" whose lead is no longer being worked (result set
    # some other way, lead moved or deleted): hang it up and close it.
    for att in (
        db.query(VoiceCallAttempt)
        .outerjoin(SmsLead, SmsLead.id == VoiceCallAttempt.sms_lead_id)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status == "CONNECTED",
            (SmsLead.id.is_(None)) | (SmsLead.status != "IN_PROGRESS")
            | (SmsLead.assigned_agent_id != VoiceCallAttempt.agent_user_id),
        )
        .all()
    ):
        hangups.append(att.call_uuid)
        att.status = "COMPLETED"
        att.ended_at = att.ended_at or now
        if att.connected_at and att.duration_seconds is None:
            att.duration_seconds = int((now - att.connected_at).total_seconds())
    # A bridged call whose end event never arrived: close the record so it
    # stops showing as live (the agent's own lead flow is unaffected).
    for att in (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status == "CONNECTED",
            VoiceCallAttempt.connected_at < now - timedelta(hours=3),
        )
        .all()
    ):
        att.status = "COMPLETED"
        att.ended_at = att.ended_at or now
        att.error = att.error or "no end event from Vonage"
    # A line left on an agent who is no longer working is not a line.
    for a in (
        db.query(SmsQueueAgent)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.voice_leg_uuid.isnot(None),
            SmsQueueAgent.status == "OFFLINE",
        )
        .all()
    ):
        hangups.append(a.voice_leg_uuid)
        a.voice_leg_uuid = None
        a.voice_line_at = None
    db.commit()
    for uuid_ in hangups:
        vonage.hangup(uuid_)
    return events


def _lead_of(db: Session, att: VoiceCallAttempt) -> SmsLead | None:
    if not att.sms_lead_id:
        return None
    return db.query(SmsLead).filter(SmsLead.id == att.sms_lead_id).first()


def _live_attempt_for_lead(db: Session, lead_id) -> VoiceCallAttempt | None:
    return (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.sms_lead_id == lead_id, VoiceCallAttempt.status.in_(IN_FLIGHT + ("CONNECTED",)))
        .order_by(VoiceCallAttempt.created_at.desc())
        .first()
    )


def _requeue(db: Session, lead: SmsLead | None, s: VoiceDialerSettings) -> list[dict]:
    """After a miss: back to Ready with a retry time, respecting the 24 h cap.
    Out of attempts -> the regular pool, where an agent can still call by hand."""
    if lead is None or lead.status != "DIALING":
        return []  # STOP / a manager action changed it meanwhile — leave it alone
    now = _now()
    if (lead.dial_attempts or 0) >= int(s.max_total_attempts or 6):
        lead.status = "QUEUED"
        lead.next_dial_at = None
        db.flush()
        _d, ev = qs.assign_next(db, str(lead.tenant_id))
        return [_evt("tenant", lead.tenant_id, "sms:queue_updated", {"reason": "dial_exhausted"})] + ev
    nxt = now + timedelta(minutes=int(s.retry_minutes or 30))
    phone = replies.normalize_phone(lead.phone_number)
    recent = _attempts_24h(db, lead.tenant_id, phone) if phone else []
    if len(recent) >= int(s.max_attempts_24h or 3):
        nxt = max(nxt, recent[0] + timedelta(hours=24))
    lead.status = "READY_TO_CALL"
    lead.next_dial_at = nxt
    return [_evt("tenant", lead.tenant_id, "voice:dialer_updated", {"reason": "requeued"})]


# ---- Call events ---------------------------------------------------------------

def _speak_digits(e164: str | None) -> str:
    d = replies.last10(e164)
    if len(d) != 10:
        return ""
    return f"{' '.join(d[:3])}, {' '.join(d[3:6])}, {' '.join(d[6:])}"


def call_room(att: VoiceCallAttempt) -> str:
    """One room per bridged call. Vonage records a ROOM, not a person, and
    the agent's own room lasts their whole shift — so each call gets a room of
    its own: its recording starts when they meet and ends when the customer
    leaves. (Recording inside the agent's room produced 1-second files.)"""
    return f"iag-call-{att.id.hex if hasattr(att.id, 'hex') else att.id}"


def agent_room_ncco(agent_user_id) -> list[dict]:
    """The agent's parked line: their private room, all shift."""
    return [{
        "action": "conversation", "name": conversation_name(agent_user_id),
        "startOnEnter": True, "endOnExit": True,
    }]


def connect_ncco(agent_user_id, att: VoiceCallAttempt) -> list[dict]:
    """The customer's side of the bridge: into this call's own room, recorded.

    endOnExit stays FALSE: Vonage re-runs the customer's instructions when it
    finally asks the answer URL (after machine detection, ~4 s in), so the
    customer briefly leaves and rejoins this room — with endOnExit that ended
    the room and Vonage HUNG UP the agent (live test: silence both ways).
    When the customer really hangs up, _return_agent_line moves the agent back
    to their own room; the room is then empty, ends, and the recording lands."""
    room: dict = {
        "action": "conversation", "name": call_room(att),
        "startOnEnter": True, "endOnExit": False,
    }
    if not settings.CALL_RECORDING_ENABLED:
        return [room]
    room.update({"record": True, "eventUrl": [_url("recording", a=att.id)], "eventMethod": "POST"})
    # The notice is the customer's first second with us — one short line,
    # then straight in to the agent (who is already in the room).
    notice = {"action": "talk", "text": settings.VONAGE_RECORDING_NOTICE, "language": "en-US"}
    return [notice, room]


def room_only_ncco(att: VoiceCallAttempt) -> list[dict]:
    """The customer's instructions when they are ALREADY in the call room (the
    answer URL can be asked after we bridged them): the same room, no second
    notice. Any other reply would pull them out of the call."""
    # No "record" here: the room's recording began when they first met and
    # runs until the room ends — asking again could split it in two.
    return [{"action": "conversation", "name": call_room(att), "startOnEnter": True, "endOnExit": False}]


def agent_call_ncco(agent_user_id, att: VoiceCallAttempt) -> list[dict]:
    """The agent's side: join the call's room; when it closes, carry on to the
    next action — straight back to their own parked room, ready for the next."""
    return [{
        "action": "conversation", "name": call_room(att),
        "startOnEnter": True, "endOnExit": False,
    }] + agent_room_ncco(agent_user_id)


def abandon_ncco(s: VoiceDialerSettings, att: VoiceCallAttempt) -> list[dict]:
    biz = s.business_name or DEFAULT_BUSINESS
    text = f"Hi, this is {biz}, returning your text. All of our agents are helping other customers right now."
    spoken = _speak_digits(s.callback_number)
    if spoken:
        text += f" Please call us back at {spoken}."
    else:
        text += " We will try you again shortly."
    text += " To stop receiving calls from us, press 9."
    return [
        {"action": "talk", "text": text, "bargeIn": True, "language": "en-US"},
        {"action": "input", "type": ["dtmf"], "dtmf": {"maxDigits": 1, "timeOut": 6},
         "eventUrl": [_url("optout", a=att.id)], "eventMethod": "POST"},
    ]


def hold_ncco(att: VoiceCallAttempt) -> list[dict]:
    """While machine detection listens: a silent room of their own."""
    return [{"action": "conversation", "name": f"iag-hold-{att.id.hex if hasattr(att.id, 'hex') else att.id}"}]


def _end_live_calls(db: Session, *, lead_id=None, agent_user_id=None) -> int:
    """Hang up and close every still-connected call for a lead or an agent.

    A customer left connected would still be in the agent's room when the NEXT
    customer is transferred in — two strangers on one call. So a result set on
    the lead ends its call, and a new bridge first clears the agent's room."""
    q = db.query(VoiceCallAttempt).filter(VoiceCallAttempt.status == "CONNECTED")
    if lead_id is not None:
        q = q.filter(VoiceCallAttempt.sms_lead_id == lead_id)
    if agent_user_id is not None:
        q = q.filter(VoiceCallAttempt.agent_user_id == agent_user_id)
    rows = q.all()
    now = _now()
    _hangup_after_commit(db, [att.call_uuid for att in rows])
    for att in rows:
        att.status = "COMPLETED"
        att.ended_at = att.ended_at or now
        if att.connected_at and att.duration_seconds is None:
            att.duration_seconds = int((now - att.connected_at).total_seconds())
    return len(rows)


def _hangup_after_commit(db: Session, legs: list) -> None:
    """Hang these legs up once this session's transaction commits — never while
    it still holds row locks (a Vonage round trip inside a transaction made
    every agent's status poll queue behind it and drained the DB pool)."""
    legs = [l for l in legs if l]
    if not legs:
        return
    from sqlalchemy import event

    def go(_session):
        for leg in legs:
            vonage.hangup(leg)

    event.listen(db, "after_commit", go, once=True)


def end_call_for_lead(db: Session, lead_id, disposition: str | None = None) -> None:
    """The agent set a result (Mark… / Voicemail): the call with them is over,
    and that result becomes the outcome of their latest connected call."""
    _end_live_calls(db, lead_id=lead_id)
    if disposition:
        att = (
            db.query(VoiceCallAttempt)
            .filter(VoiceCallAttempt.sms_lead_id == lead_id, VoiceCallAttempt.connected_at.isnot(None))
            .order_by(VoiceCallAttempt.connected_at.desc())
            .first()
        )
        if att is not None and not att.disposition:
            att.disposition = disposition


def _claim_agent(db: Session, tenant_id, lead: SmsLead, prefer_user_id=None,
                 only_user_id=None) -> SmsQueueAgent | None:
    """Longest-idle free agent on the line — the client's own agent first
    (prefer), or ONLY them (only: a caller holding for their agent)."""
    agents = _ready_agents(db, tenant_id)
    if only_user_id:
        agents = [a for a in agents if str(a.user_id) == str(only_user_id)]
    elif prefer_user_id:
        agents.sort(key=lambda a: str(a.user_id) != str(prefer_user_id))
    for a in agents:
        n = (
            db.query(SmsQueueAgent)
            .filter(
                SmsQueueAgent.id == a.id,
                SmsQueueAgent.status == "AVAILABLE",
                SmsQueueAgent.current_lead_id.is_(None),
                SmsQueueAgent.voice_leg_uuid.isnot(None),
            )
            .update(
                {"current_lead_id": lead.id, "status": "ON_CALL", "consecutive_misses": 0, "updated_at": _now()},
                synchronize_session=False,
            )
        )
        if n == 1:
            db.refresh(a)
            return a
    return None


def connect_human(db: Session, att: VoiceCallAttempt, early: bool = False,
                  ncco_out: list | None = None) -> list[dict]:
    """A live person answered: claim an agent, bridge, then pop the card.

    early=True (connect on answer): bridged before machine detection has
    decided, so human_at stays empty until its "human" verdict — and a
    "machine" verdict pulls the agent back out (_drop_early_voicemail).

    ncco_out: called from the customer's ANSWER webhook. The customer's
    instructions go back in that response (filled into ncco_out) instead of a
    transfer — a transfer issued then raced the answer webhook's own reply,
    which put the customer back in the hold room: both sides heard silence."""
    if att.status not in IN_FLIGHT:
        return []  # already handled (duplicate / late event)
    now = _now()
    att.answered_at = att.answered_at or now
    if not early:
        att.human_at = now
    tenant_id = str(att.tenant_id)
    s = get_settings(db, tenant_id)
    lead = _lead_of(db, att)
    if lead is None or lead.status != "DIALING":
        att.status = "CANCELLED"
        att.ended_at = now
        db.commit()
        if ncco_out is not None:
            ncco_out.append({"action": "talk", "text": "Goodbye."})
        else:
            vonage.hangup(att.call_uuid)
        return []

    from app.voice_dialer import inbound

    agent = _claim_agent(db, tenant_id, lead, prefer_user_id=inbound.owner_for(db, tenant_id, att.phone_number))
    if agent is None:
        att.status = "ABANDONED"
        db.commit()
        if ncco_out is not None:
            ncco_out.extend(abandon_ncco(s, att))
            events = _requeue(db, lead, s)
            db.commit()
            return events
        try:
            vonage.transfer(att.call_uuid, abandon_ncco(s, att))
        except Exception as exc:
            logger.warning("voice dialer: abandon message failed: %s", exc)
            vonage.hangup(att.call_uuid)
        events = _requeue(db, lead, s)
        db.commit()
        return events

    # Never two customers in one room: clear anything left on this agent's line.
    _end_live_calls(db, agent_user_id=agent.user_id)
    lead.status = "IN_PROGRESS"
    lead.assigned_agent_id = agent.user_id
    lead.accepted_at = now
    lead.disposition = None
    lead.next_dial_at = None
    att.status = "CONNECTED"
    att.agent_user_id = agent.user_id
    att.connected_at = now
    db.add(SmsAgentAction(tenant_id=tenant_id, user_id=agent.user_id, sms_lead_id=lead.id, action="KEEP"))
    db.commit()
    agent_leg = agent.voice_leg_uuid
    moved_agent = False
    try:
        # Both moves at once (they were one after the other: ~1 s each on the
        # customer's clock). The customer hears the short notice first, so the
        # agent is in the room before they arrive.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(vonage.transfer, agent_leg, agent_call_ncco(agent.user_id, att)) if agent_leg else None
            c = None
            if ncco_out is None:
                c = pool.submit(vonage.transfer, att.call_uuid, connect_ncco(agent.user_id, att))
            errors = []
            for f in (a, c):
                if f is None:
                    continue
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
            moved_agent = a is not None and a.exception() is None
            if errors:
                raise errors[0]
        if ncco_out is not None:
            ncco_out.extend(connect_ncco(agent.user_id, att))
    except Exception as exc:
        logger.warning("voice dialer: bridge failed: %s", exc)
        if moved_agent:
            try:
                vonage.transfer(agent_leg, agent_room_ncco(agent.user_id))
            except Exception:
                pass  # their browser reopens the line by itself
        if ncco_out is not None:
            ncco_out.clear()
            ncco_out.append({"action": "talk", "text": "Sorry, please hold on. We will call you right back."})
        else:
            vonage.hangup(att.call_uuid)
        att.status = "FAILED"
        att.error = f"bridge failed: {exc}"[:500]
        att.ended_at = _now()
        agent.current_lead_id = None
        agent.status = "AVAILABLE"
        lead.status = "DIALING"
        lead.assigned_agent_id = None
        lead.accepted_at = None
        events = _requeue(db, lead, s)
        db.commit()
        return events
    # This agent talked to them: their callbacks now come to this agent.
    inbound.set_owner(db, tenant_id, att.phone_number, agent.user_id)
    # Only now — human detected AND agent bridged — does the lead pop.
    card = lead_card(db, lead, att)
    return [
        _evt("user", agent.user_id, "voice:connected", card),
        _evt("agent", agent.user_id, "sms:lead_accepted", card),
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "auto_dial_connected"}),
        _evt("tenant", tenant_id, "voice:dialer_updated", {"reason": "connected"}),
    ]


def attempt_for_recording(db: Session, payload: dict) -> VoiceCallAttempt | None:
    """The bridged call a room recording belongs to: connected within a minute
    of the recording's start and not yet holding a recording."""
    try:
        start = datetime.fromisoformat(str(payload.get("start_time")).replace("Z", "+00:00"))
    except ValueError:
        return None
    rows = (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.recording_id.is_(None),
            VoiceCallAttempt.connected_at.isnot(None),
            VoiceCallAttempt.connected_at >= start - timedelta(seconds=60),
            VoiceCallAttempt.connected_at <= start + timedelta(seconds=60),
        )
        .all()
    )
    return min(rows, key=lambda a: abs((a.connected_at - start).total_seconds()), default=None)


def find_attempt(db: Session, attempt_id: str | None, call_uuid: str | None) -> VoiceCallAttempt | None:
    att = None
    if attempt_id:
        try:
            att = db.query(VoiceCallAttempt).filter(VoiceCallAttempt.id == attempt_id).first()
        except Exception:
            db.rollback()
    if att is None and call_uuid:
        att = db.query(VoiceCallAttempt).filter(VoiceCallAttempt.call_uuid == call_uuid).first()
    return att


def on_call_event(db: Session, att: VoiceCallAttempt, payload: dict) -> list[dict]:
    """Vonage call-leg event for a customer call (status / machine detection)."""
    status = str(payload.get("status") or "").lower()
    leg = payload.get("uuid") or payload.get("call_uuid")
    if leg and not att.call_uuid:
        att.call_uuid = leg
    now = _now()
    s = get_settings(db, str(att.tenant_id))

    if status in ("started",):
        db.commit()
        return []
    if status == "ringing":
        if att.status == "DIALING":
            att.status = "RINGING"
        db.commit()
        return []
    if status == "answered":
        if att.status in ("DIALING", "RINGING"):
            att.status = "ANSWERED"
            att.answered_at = now
        db.commit()
        if not settings.VONAGE_MACHINE_DETECTION:
            # No detection: the answer webhook (asked at pickup) bridges them in
            # its own reply. A transfer from here would race it.
            return []
        if settings.VONAGE_CONNECT_ON_ANSWER:
            # Put them through now (by transfer); detection keeps listening.
            # With detection on, Vonage only asks our answer URL AFTER it
            # decides — webhooks.answer then keeps them in this same room.
            return connect_human(db, att, early=True)
        # Detection listens for a few seconds — open the connection the
        # bridge will need, so it is warm when "human" lands.
        vonage.warm()
        return []
    if status == "human":
        if att.status in ("CONNECTED", "COMPLETED"):
            att.human_at = att.human_at or now  # confirms the early bridge
            db.commit()
            return []
        return connect_human(db, att)
    if status == "machine":
        if att.status == "CONNECTED" and att.human_at is None:
            return _drop_early_voicemail(db, att, s)
        if att.status not in IN_FLIGHT:
            db.commit()
            return []  # already bridged on the grace timer — the agent marks it Voicemail
        att.status = "MACHINE"
        att.answered_at = att.answered_at or now
        db.commit()
        vonage.hangup(att.call_uuid)
        events = _requeue(db, _lead_of(db, att), s)
        db.commit()
        return events

    final = END_STATUS.get(status)
    if final is None:
        db.commit()
        return []
    if att.status in ("HOLDING", "BRIDGING"):
        from app.voice_dialer import inbound

        att.ended_at = now
        return inbound.caller_hung_up(db, att)
    duration = payload.get("duration")
    try:
        att.duration_seconds = int(duration) if duration not in (None, "") else att.duration_seconds
    except (TypeError, ValueError):
        pass
    att.ended_at = att.ended_at or now
    if att.status == "CONNECTED":
        # The customer (or the agent's Hang up) ended a bridged call. The agent
        # keeps the lead until they set the result with Mark…
        att.status = "COMPLETED"
        db.commit()
        _return_agent_line(db, att)
        return [_evt("user", att.agent_user_id, "voice:ended", {"attempt_id": str(att.id), "sms_lead_id": str(att.sms_lead_id)})]
    if att.status in IN_FLIGHT:
        att.status = final
        events = _requeue(db, _lead_of(db, att), s)
        db.commit()
        return events
    db.commit()  # already final (MACHINE / ABANDONED …): just record the end
    return []


def _return_agent_line(db: Session, att: VoiceCallAttempt) -> None:
    """Safety net: the customer is gone, so put the agent's line back in their
    own room. Normally the call room closing does this by itself; if the
    customer never reached the room, the agent would otherwise sit in it alone."""
    if not att.agent_user_id:
        return
    agent = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == att.tenant_id, SmsQueueAgent.user_id == att.agent_user_id)
        .first()
    )
    if agent is None or not agent.voice_leg_uuid:
        return
    try:
        vonage.transfer(agent.voice_leg_uuid, agent_room_ncco(agent.user_id))
    except Exception as exc:  # the leg may already be gone — the browser reopens it
        logger.info("voice dialer: return agent line: %s", exc)


def _drop_early_voicemail(db: Session, att: VoiceCallAttempt, s: VoiceDialerSettings) -> list[dict]:
    """Connected on answer, then detection said voicemail: hang up the
    machine (the call room closes and the agent's line carries on into their
    own room), free the agent, and put the lead back for a retry — exactly as
    if it had been caught before the bridge."""
    now = _now()
    agent_id = att.agent_user_id
    att.status = "MACHINE"
    att.ended_at = att.ended_at or now
    lead = _lead_of(db, att)
    if lead is not None and lead.status == "IN_PROGRESS":
        lead.status = "DIALING"  # so _requeue treats it as a miss
        lead.assigned_agent_id = None
        lead.accepted_at = None
    if agent_id:
        agent = qs._get_or_create_agent(db, str(att.tenant_id), str(agent_id))
        if lead is not None and str(agent.current_lead_id) == str(lead.id):
            agent.current_lead_id = None
        if agent.status == "ON_CALL":
            agent.status = "AVAILABLE"
    db.commit()
    _return_agent_line(db, att)  # agent out first, so they stop hearing the greeting
    vonage.hangup(att.call_uuid)
    events = _requeue(db, lead, s)
    db.commit()
    if agent_id:
        events += [
            _evt("user", agent_id, "voice:ended", {"attempt_id": str(att.id), "reason": "voicemail"}),
            _evt("agent", agent_id, "sms:queue_updated", {"reason": "voicemail_dropped"}),
        ]
    return events  # webhooks.event ticks right after, so the freed agent is reused


def on_optout(db: Session, att: VoiceCallAttempt, digits: str | None) -> list[dict]:
    """'Press 9 to opt out' on the abandoned-call message."""
    if (digits or "").strip() != "9":
        return [{"action": "talk", "text": "Thank you. Goodbye."}]
    qs.add_to_dnc(db, str(att.tenant_id), att.phone_number, "OPTED_OUT")
    lead = _lead_of(db, att)
    if lead is not None and lead.status in ("READY_TO_CALL", "DIALING", "QUEUED"):
        lead.status = "DISPOSITIONED"
        lead.disposition = "UNQUALIFIED"
        lead.dispositioned_at = _now()
        lead.next_dial_at = None
    db.commit()
    return [{"action": "talk", "text": "You have been removed from our call list. Goodbye."}]


# ---- Agent headset line ---------------------------------------------------------

def on_agent_line(db: Session, user_id: str, leg_uuid: str) -> tuple[list[dict], list[dict]]:
    """The agent's browser opened its line. Returns (ncco, events)."""
    from app.models.user import User

    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return [{"action": "talk", "text": "Unknown user."}], []
    tenant_id = str(u.tenant_id)
    agent = qs._get_or_create_agent(db, tenant_id, user_id)
    if not agent.voice_ready:
        # Only an agent who switched on "Ready for calls" holds a line.
        return [{"action": "talk", "text": "Ready for calls is off."}], []
    from app.voice_dialer import manual

    if manual.live_call(db, user_id) is not None:
        # Busy on a call they dialed by hand; the line reopens when it ends.
        return [{"action": "talk", "text": "You are on a call."}], []
    old = agent.voice_leg_uuid
    agent.voice_leg_uuid = leg_uuid
    agent.voice_line_at = _now()
    db.commit()
    if old and old != leg_uuid:
        vonage.hangup(old)  # a second tab took over the line
    ncco = agent_room_ncco(user_id)
    events = [_evt("user", user_id, "voice:line", {"line": True})]
    events += tick(db, tenant_id)
    return ncco, events


def on_agent_line_ended(db: Session, leg_uuid: str) -> list[dict]:
    agent = db.query(SmsQueueAgent).filter(SmsQueueAgent.voice_leg_uuid == leg_uuid).first()
    if agent is None:
        return []
    agent.voice_leg_uuid = None
    agent.voice_line_at = None
    events = [_evt("user", agent.user_id, "voice:line", {"line": False})]
    # The agent's room closed (endOnExit) — a bridged customer is gone with it.
    live = (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.agent_user_id == agent.user_id, VoiceCallAttempt.status == "CONNECTED")
        .all()
    )
    for att in live:
        vonage.hangup(att.call_uuid)
    db.commit()
    return events


def set_ready(db: Session, tenant_id, user_id: str, on: bool) -> list[dict]:
    """The agent's "Ready for calls" switch. Off closes the line at once, so no
    Vonage minutes run while they work the regular pool."""
    agent = qs._get_or_create_agent(db, tenant_id, user_id)
    agent.voice_ready = bool(on)
    db.commit()
    events: list[dict] = [] if on else drop_line(db, tenant_id, user_id)
    events.append(_evt("user", user_id, "voice:line", {"ready": bool(on)}))
    return events + tick(db, tenant_id)


def drop_line(db: Session, tenant_id, user_id: str) -> list[dict]:
    agent = qs._get_or_create_agent(db, tenant_id, user_id)
    if not agent.voice_leg_uuid:
        return []
    leg = agent.voice_leg_uuid
    vonage.hangup(leg)
    return on_agent_line_ended(db, leg)


def hangup_customer(db: Session, tenant_id, user_id: str) -> dict:
    att = (
        db.query(VoiceCallAttempt)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.agent_user_id == user_id,
            VoiceCallAttempt.status == "CONNECTED",
        )
        .order_by(VoiceCallAttempt.connected_at.desc())
        .first()
    )
    if att is None:
        return {"ok": False, "reason": "no_live_call"}
    vonage.hangup(att.call_uuid)
    if vonage.simulating() or str(att.call_uuid or "").startswith("sim-"):
        from app.voice_dialer import simulator

        simulator.hangup(db, att)  # Vonage would now send the "completed" event
    return {"ok": True}


# ---- Inbound call (client calls our number back) -----------------------------------

def on_inbound_call(db: Session, tenant_id, from_raw: str, to_raw: str | None, leg_uuid: str) -> tuple[list[dict], list[dict]]:
    """A client calling one of our numbers — routed by inbound.py (their own
    agent first, hold queue, open hours)."""
    from app.voice_dialer import inbound

    phone = replies.normalize_phone(from_raw)
    if not phone:
        biz = get_settings(db, tenant_id).business_name or DEFAULT_BUSINESS
        return [{"action": "talk", "text": f"Thank you for calling {biz}. Goodbye."}], []
    return inbound.on_call(db, tenant_id, phone, to_raw, leg_uuid)


# ---- Recording ----------------------------------------------------------------------

def _audio_type(data: bytes) -> str:
    if data[:4] == b"RIFF":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    return "audio/mpeg"


def save_recording(db: Session, *, tenant_id, user_id, phone: str, when: datetime | None, url: str):
    """Download a Vonage recording and keep it in OUR storage — exactly where
    Log Sale keeps its call recordings: a `deal_recordings` row with the audio
    in the S3 bucket under deal-recordings/<tenant>/ (inline on the row only if
    S3 is unavailable, the same fallback Log Sale uses). Returns the flushed
    row; raises if the download fails. Shared by dialer and manual calls."""
    import uuid as _uuid

    from app.calls.s3_storage import s3_storage
    from app.models.compliance import DealRecording

    data = vonage.download(url)
    ctype = _audio_type(data)
    ext = {"audio/wav": "wav", "audio/ogg": "ogg"}.get(ctype, "mp3")
    agent_id = None
    if user_id:
        from app.models.agent import Agent

        a = db.query(Agent.id).filter(Agent.user_id == user_id).first()
        agent_id = a[0] if a else None
    stamp = (when or _now()).strftime("%Y-%m-%d_%H%M")
    rec = DealRecording(
        tenant_id=tenant_id,
        agent_id=agent_id,
        filename=f"call_{replies.last10(phone)}_{stamp}.{ext}",
        content_type=ctype,
        kind="recording",
        byte_size=len(data),
        storage="db",
    )
    try:
        if s3_storage.configured():
            key = f"deal-recordings/{tenant_id}/{_uuid.uuid4()}.{ext}"
            out = s3_storage.upload_bytes(data, key, content_type=ctype)
            rec.storage, rec.s3_bucket, rec.s3_key, rec.data = "s3", out["bucket"], out["key"], None
    except Exception as exc:
        logger.warning("voice: S3 upload failed, keeping recording inline: %s", exc)
    if rec.storage != "s3":
        rec.data = data
    db.add(rec)
    db.flush()
    return rec


def store_recording(db: Session, att: VoiceCallAttempt, payload: dict) -> dict:
    """Vonage finished recording a connected call. Vonage deletes recordings
    after 30 days, so the audio is copied to our storage right away
    (save_recording). Being a deal_recordings row, it can be attached to a
    sale like an uploaded one."""
    url = payload.get("recording_url")
    if not url:
        return {"ok": False}
    if att.recording_id:
        return {"ok": True, "duplicate": True}  # Vonage retried the webhook
    try:
        rec = save_recording(db, tenant_id=att.tenant_id, user_id=att.agent_user_id,
                             phone=att.phone_number, when=att.connected_at, url=url)
    except Exception as exc:
        att.error = f"recording download failed: {exc}"[:500]
        db.commit()
        return {"ok": False, "error": "download failed"}
    att.recording_id = rec.id
    db.commit()
    return {"ok": True, "recording_id": str(rec.id), "storage": rec.storage}


def recording_payload(db: Session, recording_id) -> tuple[str, bytes | str, str]:
    """('url', signed_url, '') for S3, ('bytes', data, content_type) when inline.
    Raises LookupError when there is nothing to play."""
    from app.calls.s3_storage import s3_storage
    from app.models.compliance import DealRecording

    rec = db.query(DealRecording).filter(DealRecording.id == recording_id).first() if recording_id else None
    if rec is None:
        raise LookupError("recording missing")
    if rec.storage == "s3" and rec.s3_key:
        url = s3_storage.signed_url(rec.s3_key)
        if not url:
            raise LookupError("storage unavailable")
        return "url", url, ""
    return "bytes", bytes(rec.data or b""), rec.content_type or "audio/mpeg"


def recording_audio(db: Session, tenant_id, attempt_id) -> tuple[str, bytes | str, str]:
    att = find_attempt(db, attempt_id, None)
    if att is None or str(att.tenant_id) != str(tenant_id) or not att.recording_id:
        raise LookupError("no recording")
    return recording_payload(db, att.recording_id)


# ---- Read models ------------------------------------------------------------------

def agent_state(db: Session, tenant_id, user_id: str) -> dict:
    s = get_settings(db, tenant_id)
    agent = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.user_id == user_id)
        .first()
    )
    call = None
    if agent is not None and agent.current_lead_id:
        att = (
            db.query(VoiceCallAttempt)
            .filter(
                VoiceCallAttempt.agent_user_id == user_id,
                VoiceCallAttempt.sms_lead_id == agent.current_lead_id,
                VoiceCallAttempt.status.in_(("CONNECTED", "COMPLETED")),
            )
            .order_by(VoiceCallAttempt.connected_at.desc())
            .first()
        )
        lead = db.query(SmsLead).filter(SmsLead.id == agent.current_lead_id).first()
        if att is not None and lead is not None and lead.status == "IN_PROGRESS":
            call = lead_card(db, lead, att)
    return {
        "enabled": bool(s.enabled),
        "available": vonage.available(),
        "configured": vonage.configured(),
        "simulate": vonage.simulating(),
        "agent": {
            "status": agent.status if agent else "OFFLINE",
            "ready": bool(agent and agent.voice_ready),
            "line": bool(agent and agent.voice_leg_uuid),
            "line_at": agent.voice_line_at.isoformat() if agent and agent.voice_line_at else None,
        },
        # On a manual (Dial drawer) call: the browser keeps its line closed.
        "manual": manual_live(db, user_id),
        "call": call,
    }


def manual_live(db: Session, user_id) -> bool:
    from app.voice_dialer import manual

    return manual.live_call(db, user_id) is not None


def overview(db: Session, tenant_id, period: str = "today", date_from: str | None = None,
             date_to: str | None = None) -> dict:
    """Live state plus the stats for one period (`today` unless the page picks
    another — yesterday / week / month / all / custom)."""
    period = period if period in PERIODS else "today"
    p_start, p_end = period_bounds(period, date_from, date_to)
    s = get_settings(db, tenant_id)
    now = _now()
    ready_rows = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status.in_(("READY_TO_CALL", "DIALING")))
        .order_by(SmsLead.yes_at.asc().nullsfirst())
        .limit(200)
        .all()
    )
    ready = []
    for l in ready_rows:
        # A fresh reply inside its short "wait before calling" pause is not a retry.
        due = l.next_dial_at is None or l.next_dial_at <= now + timedelta(seconds=31) and not (l.dial_attempts or 0)
        due = due or (l.next_dial_at is not None and l.next_dial_at <= now)
        state = lead_state(db, l)
        ready.append({
            "id": str(l.id), "customer_name": l.customer_name, "phone_number": l.phone_number,
            "status": l.status, "yes_reply": l.yes_reply,
            "yes_at": l.yes_at.isoformat() if l.yes_at else None,
            "next_dial_at": l.next_dial_at.isoformat() if l.next_dial_at else None,
            "dial_attempts": l.dial_attempts or 0, "state": state,
            "waiting_for": None if l.status == "DIALING" else (
                "retry" if not due else
                ("calling_hours" if not (replies.is_test_number(l.phone_number) or _window_ok(now, state, s, tenant_id)) else "agent")
            ),
        })
    recent = (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.tenant_id == tenant_id)
        .order_by(VoiceCallAttempt.created_at.desc())
        .limit(30)
        .all()
    )
    agents_with_line = (
        db.query(func.count(SmsQueueAgent.id))
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.voice_leg_uuid.isnot(None))
        .scalar()
        or 0
    )
    # Speed of the live-person path (spec target: agent on within 2 s of "hello").
    speed = (
        _in_period(db.query(VoiceCallAttempt.answered_at, VoiceCallAttempt.human_at, VoiceCallAttempt.connected_at), p_start, p_end)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.direction == "outbound",
            VoiceCallAttempt.connected_at.isnot(None),
        )
        .all()
    )
    detect = [(h - a).total_seconds() * 1000 for a, h, _c in speed if a and h]
    bridge = [(c - h).total_seconds() * 1000 for _a, h, c in speed if h and c]
    talk = [
        d for (d,) in _in_period(db.query(VoiceCallAttempt.duration_seconds), p_start, p_end)
        .filter(
            VoiceCallAttempt.tenant_id == tenant_id,
            VoiceCallAttempt.status == "COMPLETED",
            VoiceCallAttempt.duration_seconds.isnot(None),
        )
        .all()
    ]
    waiting = {"agent": 0, "retry": 0, "calling_hours": 0}
    for r in ready:
        if r["waiting_for"]:
            waiting[r["waiting_for"]] += 1
    return {
        "settings": settings_dict(s),
        "available": vonage.available(),
        "configured": vonage.configured(),
        "simulate": vonage.simulating(),
        "sim_mode": _sim_mode(tenant_id),
        "sim_ignore_hours": _sim_ignore_hours(tenant_id),
        "fleet_size": len(fleet_numbers()),
        "ready_agents": len(_ready_agents(db, tenant_id)),
        "agents_with_line": int(agents_with_line),
        "in_flight": len(_in_flight(db, tenant_id)),
        "period": {"key": period, "from": p_start.isoformat() if p_start else None,
                   "to": p_end.isoformat() if p_end else None},
        "today": {
            **(today_counts(db, tenant_id, p_start, p_end) if p_start or p_end else today_counts_all(db, tenant_id)),
            "avg_detect_ms": round(sum(detect) / len(detect)) if detect else None,
            "avg_bridge_ms": round(sum(bridge) / len(bridge)) if bridge else None,
            "avg_talk_seconds": round(sum(talk) / len(talk)) if talk else None,
            "opted_out": _opted_out(db, tenant_id, p_start, p_end),
        },
        "queue": {**waiting, "dialing": sum(1 for r in ready if r["status"] == "DIALING")},
        # Is the window open right now? Eastern (most leads) and the strict
        # unknown-state window (every contiguous zone).
        "hours_open": {
            "eastern": _window_ok(now, "NY", s, tenant_id),
            "all_zones": _window_ok(now, None, s, tenant_id),
        },
        "agents": _agents_board(db, tenant_id),
        "ready": ready,
        "recent": call_rows(db, recent),
    }


def call_rows(db: Session, attempts: list[VoiceCallAttempt]) -> list[dict]:
    """Attempts as the Called list shows them: with agent and client names."""
    from app.models.user import User

    ids = {a.agent_user_id for a in attempts if a.agent_user_id}
    ids |= {a.hold_for_user_id for a in attempts if a.hold_for_user_id}
    names = {
        u.id: (f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email)
        for u in db.query(User).filter(User.id.in_(ids)).all()
    } if ids else {}
    lead_ids = [a.sms_lead_id for a in attempts if a.sms_lead_id]
    lead_names = dict(
        db.query(SmsLead.id, SmsLead.customer_name).filter(SmsLead.id.in_(lead_ids)).all()
    ) if lead_ids else {}
    return [
        {**attempt_dict(a), "agent_name": names.get(a.agent_user_id),
         "hold_for_name": names.get(a.hold_for_user_id),
         "customer_name": lead_names.get(a.sms_lead_id),
         "recording": "stored" if a.recording_id else None}
        for a in attempts
    ]


CALL_FILTERS = ("all", "talked", "sales", "missed")
_LIVE_STATUSES = ("DIALING", "RINGING", "ANSWERED", "HOLDING", "BRIDGING")


def _call_filter(q, kind: str):
    if kind == "talked":
        return q.filter(VoiceCallAttempt.connected_at.isnot(None))
    if kind == "sales":
        return q.filter(VoiceCallAttempt.disposition == "SALE")
    if kind == "missed":
        return q.filter(VoiceCallAttempt.connected_at.is_(None), VoiceCallAttempt.status.notin_(_LIVE_STATUSES))
    return q


def call_history(db: Session, tenant_id, kind: str = "all", before: str | None = None, limit: int = 30,
                 period: str = "all", date_from: str | None = None, date_to: str | None = None) -> dict:
    """The Called list, newest first, one page at a time (`before` = the
    created_at of the last row already shown), plus the count per filter."""
    kind = kind if kind in CALL_FILTERS else "all"
    limit = max(1, min(int(limit or 30), 200))
    p_start, p_end = period_bounds(period if period in PERIODS else "all", date_from, date_to)
    base = _in_period(db.query(VoiceCallAttempt).filter(VoiceCallAttempt.tenant_id == tenant_id), p_start, p_end)
    q = _call_filter(base, kind)
    if before:
        try:
            cut = datetime.fromisoformat(before.replace("Z", "+00:00"))
            q = q.filter(VoiceCallAttempt.created_at < cut)
        except ValueError:
            pass
    rows = q.order_by(VoiceCallAttempt.created_at.desc()).limit(limit + 1).all()
    counts = {
        k: _call_filter(_in_period(db.query(func.count(VoiceCallAttempt.id)).filter(VoiceCallAttempt.tenant_id == tenant_id), p_start, p_end), k).scalar() or 0
        for k in CALL_FILTERS
    }
    return {"items": call_rows(db, rows[:limit]), "has_more": len(rows) > limit, "counts": counts}


def _opted_out(db: Session, tenant_id, start, end) -> int:
    from app.voice_dialer import optouts

    return optouts.count_in_period(db, tenant_id, start, end)


def _agents_board(db: Session, tenant_id) -> list[dict]:
    """Who is working right now, with their line and any live auto-dialed call."""
    from app.core.active_agents import active_user_clause
    from app.models.user import User

    rows = (
        db.query(SmsQueueAgent, User)
        .join(User, User.id == SmsQueueAgent.user_id)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.status != "OFFLINE",
            func.lower(func.coalesce(User.role, "")).notin_(qs.ADMIN_ROLES),
            active_user_clause(),
        )
        .all()
    )
    live = {
        a.agent_user_id: a
        for a in db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.tenant_id == tenant_id, VoiceCallAttempt.status == "CONNECTED")
        .all()
    }
    names = dict(
        db.query(SmsLead.id, SmsLead.customer_name)
        .filter(SmsLead.id.in_([a.sms_lead_id for a in live.values() if a.sms_lead_id] or [None]))
        .all()
    )
    out = []
    for agent, u in rows:
        call = live.get(agent.user_id)
        if call is not None and agent.status == "ON_CALL":
            state = "on_call"
        elif agent.status == "AWAY":
            state = "break"
        elif agent.status == "ON_CALL" or agent.current_lead_id:
            state = "busy"            # working another lead (offer / chat / wrap-up)
        elif not agent.voice_ready:
            state = "pool_only"       # working the regular pool, not taking calls
        elif agent.voice_leg_uuid:
            state = "waiting"         # on the line, next live customer goes to them
        else:
            state = "no_line"         # wants calls but the headset line isn't open
        out.append({
            "user_id": str(agent.user_id),
            "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
            "state": state,
            "line": bool(agent.voice_leg_uuid),
            "since": (agent.voice_line_at or agent.updated_at).isoformat() if (agent.voice_line_at or agent.updated_at) else None,
            "call": {
                "customer_name": names.get(call.sms_lead_id),
                "phone_number": call.phone_number,
                "connected_at": call.connected_at.isoformat() if call.connected_at else None,
            } if state == "on_call" else None,
        })
    order = {"on_call": 0, "waiting": 1, "busy": 2, "no_line": 3, "pool_only": 4, "break": 5}
    out.sort(key=lambda a: (order[a["state"]], a["name"].lower()))
    return out


def _window_ok(now: datetime, state: str | None, s: VoiceDialerSettings, tenant_id) -> bool:
    """Calling hours in the client's zone. The dev simulator can waive them
    (testing happens outside US hours); production never can."""
    if vonage.simulating():
        from app.voice_dialer import simulator

        if simulator.get_ignore_hours(tenant_id):
            return True
    return rules.in_call_window(now, state, s.window_start_hour, s.window_end_hour)


def _sim_mode(tenant_id) -> str | None:
    if not vonage.simulating():
        return None
    from app.voice_dialer import simulator

    return simulator.get_mode(tenant_id)


def _sim_ignore_hours(tenant_id) -> bool | None:
    if not vonage.simulating():
        return None
    from app.voice_dialer import simulator

    return simulator.get_ignore_hours(tenant_id)


def dialer_reserves_agents(db: Session, tenant_id) -> bool:
    """While the dialer has work, agents holding an open line are kept for it:
    the regular offer popup skips them, so a live customer always finds the
    agent the pacing counted on."""
    held = (
        db.query(VoiceCallAttempt.id)
        .filter(VoiceCallAttempt.tenant_id == tenant_id, VoiceCallAttempt.status == "HOLDING")
        .first()
    )
    if held is not None:
        return True  # a caller on hold gets the next agent on a line
    s = db.query(VoiceDialerSettings.enabled).filter(VoiceDialerSettings.tenant_id == tenant_id).first()
    if not s or not s[0]:
        return False
    return (
        db.query(SmsLead.id)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status.in_(("READY_TO_CALL", "DIALING")))
        .first()
        is not None
    )


def requeue_voicemail(db: Session, lead: SmsLead) -> bool:
    """Agent marked an auto-dialed call 'Voicemail' (detection let one through):
    back on the dial list instead of closed. Returns True if requeued."""
    if not lead.yes_at:
        return False
    s = get_settings(db, str(lead.tenant_id))
    if not s.enabled:
        return False
    lead.status = "DIALING"  # _requeue's precondition; it immediately moves it on
    lead.assigned_agent_id = None
    lead.accepted_at = None
    _requeue(db, lead, s)
    return True
