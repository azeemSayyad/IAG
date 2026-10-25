"""Inbound calls — who a client calling one of our numbers reaches.

The owner's rules (agreed with the team):

  * Every client has an OWNER: the agent who most recently called or talked to
    them (a manual call, an auto-dialed bridge, or an earlier callback). An
    admin can reassign it on the Auto Dialer page. Managers never own a client
    — their clients' callbacks go to any free agent.
  * Closed (open hours are on and it's outside them) -> the closed message.
  * Owner FREE on the line                -> straight to the owner.
  * Owner taking calls but BUSY           -> hold for the owner, up to the hold
                                             limit, then the next free agent.
  * Owner OFF (offline, on break, not
    taking calls) or no owner             -> the first free agent.
  * Nobody free -> hold with music; any free agent takes the longest-waiting
    caller first. Pressing 1 at any time = "call me back when it's my turn".
  * Hold limit reached (5 min default) and still nobody -> "we'll call you
    back"; they keep their place on the dial list.
  * Nobody taking calls at all -> no point holding: "we'll call you back".

Callers on hold come BEFORE outbound dialing: while anyone holds, the dialer
starts no new calls and the regular pool leaves agents on a line alone.

A held caller is an inbound `voice_call_attempts` row with status HOLDING
(hold_for_user_id = the owner they wait for, or NULL). Their hold is an NCCO
loop — music (barge-in) + a 1-digit input — that re-asks the `hold` webhook
every clip, so the hold limit and "press 1" are checked every ~45 s; the
dialer tick (every 2–3 s) bridges them the moment an agent frees up.
"""
from __future__ import annotations

import io
import logging
import math
import struct
import wave
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.sms import SmsLead, SmsQueueAgent, VoiceCallAttempt, VoiceClientOwner, VoiceDialerSettings
from app.voice_dialer import replies, vonage

logger = logging.getLogger(__name__)

MANAGER_ROLES = {"manager", "head", "tenant_admin", "admin", "super_admin", "dev"}
HOLD_GRACE_SECONDS = 5        # the hold webhook gets first go at the limit; the tick is the net
MUSIC_SECONDS = 45            # one hold clip; "press 1" is re-offered after each
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TZ_NAMES = {
    "America/New_York": "Eastern", "America/Chicago": "Central",
    "America/Denver": "Mountain", "America/Phoenix": "Arizona", "America/Los_Angeles": "Pacific",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evt(to: str, id_, event: str, data: dict) -> dict:
    return {"to": to, "id": str(id_), "event": event, "data": data}


# ---- Owners --------------------------------------------------------------------

def _can_own(db: Session, user_id) -> bool:
    from app.models.user import User

    role = db.query(User.role).filter(User.id == user_id).scalar()
    return bool(role) and role.lower() not in MANAGER_ROLES


def set_owner(db: Session, tenant_id, phone: str | None, user_id, *, source: str = "call",
              set_by=None) -> None:
    """The most recent agent wins. A manager's call never changes the owner."""
    phone = replies.normalize_phone(phone)
    if not phone or not user_id:
        return
    if source == "call" and not _can_own(db, user_id):
        return
    row = (
        db.query(VoiceClientOwner)
        .filter(VoiceClientOwner.tenant_id == tenant_id, VoiceClientOwner.phone_number == phone)
        .first()
    )
    if row is None:
        row = VoiceClientOwner(tenant_id=tenant_id, phone_number=phone, user_id=user_id)
        db.add(row)
    row.user_id = user_id
    row.source = source
    row.set_by_user_id = set_by
    row.updated_at = _now()
    db.commit()


def owner_for(db: Session, tenant_id, phone: str | None):
    phone = replies.normalize_phone(phone)
    if not phone:
        return None
    return (
        db.query(VoiceClientOwner.user_id)
        .filter(VoiceClientOwner.tenant_id == tenant_id, VoiceClientOwner.phone_number == phone)
        .scalar()
    )


def owner_state(db: Session, tenant_id, user_id) -> str:
    """free  = on the line and holding nothing (gets the call now)
       busy  = taking calls but on a call / lead (worth holding for)
       away  = offline, on break, not taking calls, disabled, or a manager"""
    from app.core.active_agents import user_is_active
    from app.models.user import User
    from app.voice_dialer import service

    if not user_id:
        return "away"
    u = db.query(User).filter(User.id == user_id).first()
    if not user_is_active(u) or (u.role or "").lower() in MANAGER_ROLES:
        return "away"
    a = (
        db.query(SmsQueueAgent)
        .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.user_id == user_id)
        .first()
    )
    if a is None or not a.voice_ready or a.status not in ("AVAILABLE", "ON_CALL"):
        return "away"
    if any(str(r.user_id) == str(user_id) for r in service._ready_agents(db, tenant_id)):
        return "free"
    return "busy"


def anyone_taking_calls(db: Session, tenant_id) -> bool:
    from app.core.active_agents import active_user_clause
    from app.models.user import User

    return (
        db.query(SmsQueueAgent.id)
        .join(User, User.id == SmsQueueAgent.user_id)
        .filter(
            SmsQueueAgent.tenant_id == tenant_id,
            SmsQueueAgent.voice_ready.is_(True),
            SmsQueueAgent.status.in_(("AVAILABLE", "ON_CALL")),
            active_user_clause(),
        )
        .first()
        is not None
    )


# ---- Open hours ------------------------------------------------------------------

def _days(s: VoiceDialerSettings) -> list[int]:
    out = []
    for p in str(s.open_days or "").split(","):
        p = p.strip()
        if p.isdigit() and 0 <= int(p) <= 6:
            out.append(int(p))
    return sorted(set(out))


def _tz(s: VoiceDialerSettings) -> ZoneInfo:
    try:
        return ZoneInfo(s.hours_tz or "America/New_York")
    except Exception:
        return ZoneInfo("America/New_York")


def is_open(s: VoiceDialerSettings, now: datetime | None = None) -> bool:
    if not s.hours_enabled:
        return True
    local = (now or _now()).astimezone(_tz(s))
    minute = local.hour * 60 + local.minute
    return local.weekday() in _days(s) and int(s.open_start_minute) <= minute < int(s.open_end_minute)


def _clock(minute: int) -> str:
    h, m = divmod(int(minute), 60)
    suffix = "AM" if h < 12 or h == 24 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def days_label(days: list[int]) -> str:
    if not days:
        return "no days"
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{DAYS[days[0]]} to {DAYS[days[-1]]}"
    names = [DAYS[d] for d in days]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def hours_label(s: VoiceDialerSettings) -> str:
    tz = TZ_NAMES.get(s.hours_tz or "", s.hours_tz or "Eastern")
    return (f"{days_label(_days(s))}, {_clock(s.open_start_minute)} to "
            f"{_clock(s.open_end_minute)} {tz} time")


def _biz(s: VoiceDialerSettings) -> str:
    from app.voice_dialer.service import DEFAULT_BUSINESS

    return s.business_name or DEFAULT_BUSINESS


def closed_ncco(s: VoiceDialerSettings) -> list[dict]:
    return [{"action": "talk", "language": "en-US", "text": (
        f"Thank you for calling {_biz(s)}. Unfortunately, we are closed right now. "
        f"Our normal hours are {hours_label(s)}. Please call us back then. Goodbye.")}]


# ---- Hold --------------------------------------------------------------------------

_music: bytes | None = None


def hold_music() -> bytes:
    """A soft, slow chord loop (generated, royalty-free) — used unless
    VONAGE_HOLD_MUSIC_URL points at a real track."""
    global _music
    if _music is not None:
        return _music
    rate = 8000
    chords = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    per = MUSIC_SECONDS / (len(chords) * 3)
    frames = bytearray()
    for _ in range(3):
        for chord in chords:
            n = int(rate * per)
            for i in range(n):
                t = i / rate
                env = min(1.0, t / 0.4) * min(1.0, (per - t) / 0.6)  # soft in, soft out
                v = sum(math.sin(2 * math.pi * f * t) for f in chord) / len(chord)
                frames += struct.pack("<h", int(0.18 * env * v * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    _music = buf.getvalue()
    return _music


def _music_url() -> str:
    from app.voice_dialer.service import _url

    return (settings.VONAGE_HOLD_MUSIC_URL or "").strip() or _url("hold-music.wav")


def hold_ncco(att: VoiceCallAttempt, first: bool) -> list[dict]:
    from app.voice_dialer.service import _url

    talk = ("Thank you for calling. All of our agents are helping other customers. "
            "Please stay on the line. Press 1 at any time and we will call you back when it is your turn."
            if first else
            "Thank you for holding. Press 1 and we will call you back when it is your turn.")
    return [
        {"action": "talk", "text": talk, "language": "en-US"},
        {"action": "stream", "streamUrl": [_music_url()], "bargeIn": True, "loop": 1},
        {"action": "input", "type": ["dtmf"], "dtmf": {"maxDigits": 1, "timeOut": 1},
         "eventUrl": [_url("hold", a=att.id)], "eventMethod": "POST"},
    ]


def callback_ncco(timed_out: bool) -> list[dict]:
    text = ("Sorry for the wait. All of our agents are still busy. "
            "We will call you back as soon as it is your turn. Goodbye."
            if timed_out else
            "Thank you. We will call you back as soon as an agent is free. Goodbye.")
    return [{"action": "talk", "text": text, "language": "en-US"}]


def nobody_ncco(s: VoiceDialerSettings) -> list[dict]:
    return [{"action": "talk", "language": "en-US", "text": (
        f"Thank you for calling {_biz(s)}. All of our agents are busy right now. "
        "We will call you back as soon as one is free. Goodbye.")}]


def _held_seconds(att: VoiceCallAttempt, now: datetime) -> float:
    return (now - (att.answered_at or att.created_at)).total_seconds()


def _max_hold(s: VoiceDialerSettings) -> int:
    return int(s.max_hold_seconds or 300)


def schedule_callback(db: Session, att: VoiceCallAttempt, s: VoiceDialerSettings, reason: str) -> list[dict]:
    """They wanted us and didn't reach anyone: call them back. With the dialer
    on they go on its list (their hold start keeps their place in line); with
    it off nothing would dial them, so they go to the regular pool as HOT."""
    now = _now()
    att.status = "MISSED"
    att.error = reason
    att.ended_at = att.ended_at or now
    lead = db.query(SmsLead).filter(SmsLead.id == att.sms_lead_id).first() if att.sms_lead_id else None
    if lead is not None and lead.status in ("QUEUED", "DISPOSITIONED", "READY_TO_CALL"):
        lead.disposition = None
        if s.enabled:
            held_from = att.answered_at or now
            lead.status = "READY_TO_CALL"
            lead.yes_at = min(lead.yes_at, held_from) if lead.yes_at else held_from
            lead.next_dial_at = now
            lead.dial_attempts = 0
        else:
            lead.status = "QUEUED"
            lead.priority = "HOT"
    db.commit()
    return [_evt("tenant", att.tenant_id, "voice:dialer_updated", {"reason": "callback_scheduled"}),
            _evt("tenant", att.tenant_id, "sms:queue_updated", {"reason": "callback_scheduled"})]


def holding(db: Session, tenant_id) -> list[VoiceCallAttempt]:
    return (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.tenant_id == tenant_id, VoiceCallAttempt.status == "HOLDING")
        .order_by(VoiceCallAttempt.answered_at.asc())
        .all()
    )


# ---- The answer: where does this caller go? -----------------------------------------

def route(db: Session, tenant_id, phone: str) -> tuple[str, object]:
    """('claim', owner to try first or None = anyone) | ('hold', owner to
    wait for). Claims nothing itself."""
    owner = owner_for(db, tenant_id, phone)
    state = owner_state(db, tenant_id, owner) if owner else "away"
    if state == "busy":
        return "hold", owner
    return "claim", (owner if state == "free" else None)


def connect(db: Session, att: VoiceCallAttempt, lead: SmsLead, agent: SmsQueueAgent,
            ncco_out: list | None) -> list[dict]:
    """Bridge an inbound caller to a claimed agent. ncco_out: we are inside the
    caller's own webhook (answer / hold) — their instructions go back in that
    reply; otherwise (the tick) the caller is transferred."""
    from concurrent.futures import ThreadPoolExecutor

    from app.voice_dialer import service

    now = _now()
    tenant_id = str(att.tenant_id)
    service._end_live_calls(db, agent_user_id=agent.user_id)
    lead.status = "IN_PROGRESS"
    lead.assigned_agent_id = agent.user_id
    lead.accepted_at = now
    lead.disposition = None
    lead.next_dial_at = None
    att.status = "CONNECTED"
    att.agent_user_id = agent.user_id
    att.connected_at = now
    db.commit()
    agent_leg = agent.voice_leg_uuid
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(vonage.transfer, agent_leg, service.agent_call_ncco(agent.user_id, att)) if agent_leg else None
            fc = None if ncco_out is not None else pool.submit(
                vonage.transfer, att.call_uuid, service.connect_ncco(agent.user_id, att))
            for f in (fa, fc):
                if f is not None:
                    f.result()
        if ncco_out is not None:
            ncco_out.extend(service.connect_ncco(agent.user_id, att))
    except Exception as exc:
        logger.warning("inbound: bridge failed: %s", exc)
        att.status = "FAILED"
        att.error = f"bridge failed: {exc}"[:500]
        att.ended_at = _now()
        agent.current_lead_id = None
        agent.status = "AVAILABLE"
        lead.status = "QUEUED"
        lead.assigned_agent_id = None
        db.commit()
        if ncco_out is not None:
            ncco_out.clear()
            ncco_out.append({"action": "talk", "text": "Sorry, please hold on. We will call you right back."})
        else:
            vonage.hangup(att.call_uuid)
        return []
    set_owner(db, tenant_id, att.phone_number, agent.user_id)
    card = service.lead_card(db, lead, att)
    return [
        _evt("user", agent.user_id, "voice:connected", card),
        _evt("agent", agent.user_id, "sms:lead_accepted", card),
        _evt("tenant", tenant_id, "sms:queue_updated", {"reason": "inbound_call"}),
        _evt("tenant", tenant_id, "voice:dialer_updated", {"reason": "inbound_connected"}),
    ]


def _lock(db: Session, att: VoiceCallAttempt) -> bool:
    """HOLDING -> BRIDGING: only one of (tick, hold webhook) handles a caller."""
    n = (
        db.query(VoiceCallAttempt)
        .filter(VoiceCallAttempt.id == att.id, VoiceCallAttempt.status == "HOLDING")
        .update({"status": "BRIDGING"}, synchronize_session=False)
    )
    db.commit()
    db.refresh(att)
    return n == 1


def _unlock(db: Session, att: VoiceCallAttempt) -> None:
    if att.status == "BRIDGING":
        att.status = "HOLDING"
        db.commit()


def _try_serve(db: Session, att: VoiceCallAttempt, s: VoiceDialerSettings, now: datetime,
               ncco_out: list | None) -> tuple[bool, list[dict]]:
    """Give a held caller to an agent if the rules allow one now."""
    from app.voice_dialer import service

    tenant_id = str(att.tenant_id)
    lead = db.query(SmsLead).filter(SmsLead.id == att.sms_lead_id).first() if att.sms_lead_id else None
    if lead is None:
        return False, []
    owner = att.hold_for_user_id
    expired = _held_seconds(att, now) >= _max_hold(s)
    only = None
    if owner and not expired and owner_state(db, tenant_id, owner) == "busy":
        only = owner  # still worth waiting for their own agent
    if not _lock(db, att):
        return False, []
    agent = service._claim_agent(db, tenant_id, lead, prefer_user_id=owner, only_user_id=only)
    if agent is None:
        _unlock(db, att)
        return False, []
    return True, connect(db, att, lead, agent, ncco_out)


def serve_holding(db: Session, tenant_id, s: VoiceDialerSettings) -> list[dict]:
    """Tick: bridge waiting callers (longest first) to free agents; end holds
    past the limit whose webhook never came back."""
    events: list[dict] = []
    now = _now()
    for att in holding(db, tenant_id):
        done, ev = _try_serve(db, att, s, now, None)
        events += ev
        if done:
            continue
        if _held_seconds(att, now) >= _max_hold(s) + HOLD_GRACE_SECONDS and _lock(db, att):
            try:
                vonage.transfer(att.call_uuid, callback_ncco(timed_out=True))
            except Exception as exc:
                logger.info("inbound: hold timeout message: %s", exc)
                vonage.hangup(att.call_uuid)
            events += schedule_callback(db, att, s, "hold time limit — call back")
    return events


def on_hold_input(db: Session, att: VoiceCallAttempt | None, digits: str | None) -> tuple[list[dict], list[dict]]:
    """Every hold clip ends here: pressed 1 / limit reached / play the next clip."""
    from app.voice_dialer import service

    if att is None or att.status != "HOLDING":
        if att is not None and att.status == "CONNECTED":
            return service.room_only_ncco(att), []
        return [{"action": "talk", "text": "Goodbye."}], []
    s = service.get_settings(db, str(att.tenant_id))
    if (digits or "").strip() == "1" and _lock(db, att):
        return callback_ncco(timed_out=False), schedule_callback(db, att, s, "asked for a call back")
    now = _now()
    if _held_seconds(att, now) >= _max_hold(s):
        ncco: list[dict] = []
        done, events = _try_serve(db, att, s, now, ncco)  # any free agent may take them now
        if done:
            return ncco, events
        if _lock(db, att):
            return callback_ncco(timed_out=True), schedule_callback(db, att, s, "hold time limit — call back")
    return hold_ncco(att, first=False), []


def on_call(db: Session, tenant_id, phone: str, to_raw: str | None, leg_uuid: str) -> tuple[list[dict], list[dict]]:
    """A client called one of our numbers (phone is already +1XXXXXXXXXX)."""
    from app.sms_queue.services import queue_service as qs
    from app.voice_dialer import service

    s = service.get_settings(db, tenant_id)
    now = _now()
    lead = service.find_lead(db, tenant_id, phone)
    if lead is None:
        src = service._source_lead(db, tenant_id, phone)
        name = f"{getattr(src, 'first_name', '') or ''} {getattr(src, 'last_name', '') or ''}".strip() if src else ""
        lead = SmsLead(
            tenant_id=tenant_id, lead_id=src.id if src else None, phone_number=phone,
            customer_name=name or "Unknown caller", source="REPLY", priority="HOT",
            status="QUEUED", message_count=0,
        )
        db.add(lead)
        db.flush()
    att = VoiceCallAttempt(
        tenant_id=tenant_id, sms_lead_id=lead.id, phone_number=phone,
        from_number=replies.normalize_phone(to_raw), direction="inbound", call_uuid=leg_uuid,
        status="ANSWERED", answered_at=now, human_at=now,
    )
    db.add(att)
    db.flush()

    if not is_open(s, now):
        att.status = "MISSED"
        att.error = "closed"
        att.ended_at = now
        db.commit()
        return closed_ncco(s), []
    if qs.is_dnc(db, tenant_id, phone) or lead.status in ("IN_PROGRESS", "ASSIGNED", "DIALING"):
        # On Do-Not-Call, or already being worked / dialed right now.
        att.status = "MISSED"
        att.ended_at = now
        db.commit()
        return nobody_ncco(s), []

    kind, who = route(db, tenant_id, phone)
    if kind == "claim":
        agent = service._claim_agent(db, tenant_id, lead, only_user_id=who) if who else None
        agent = agent or service._claim_agent(db, tenant_id, lead)
        if agent is not None:
            ncco: list[dict] = []
            events = connect(db, att, lead, agent, ncco)
            return ncco, events
        who = None
    if not anyone_taking_calls(db, tenant_id):
        return nobody_ncco(s), schedule_callback(db, att, s, "nobody taking calls — call back")
    att.status = "HOLDING"
    att.hold_for_user_id = who
    db.commit()
    events = [_evt("tenant", tenant_id, "voice:dialer_updated", {"reason": "caller_holding"})]
    if who:
        events.append(_evt("user", who, "voice:holding", {"phone_number": phone, "customer_name": lead.customer_name}))
    return hold_ncco(att, first=True), events


def caller_hung_up(db: Session, att: VoiceCallAttempt) -> list[dict]:
    """The caller gave up on hold: they still wanted us — call them back."""
    from app.voice_dialer import service

    s = service.get_settings(db, str(att.tenant_id))
    return schedule_callback(db, att, s, "hung up on hold — call back")


# ---- Owners list (Auto Dialer page) ---------------------------------------------------

def assignable_agents(db: Session, tenant_id) -> list[dict]:
    from sqlalchemy import func

    from app.core.active_agents import active_users_query
    from app.models.user import User

    rows = (
        active_users_query(db, tenant_id)
        .filter(func.lower(func.coalesce(User.role, "")).notin_(MANAGER_ROLES))
        .all()
    )
    out = [{"id": str(u.id), "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email} for u in rows]
    return sorted(out, key=lambda r: r["name"].lower())


def owners_listing(db: Session, tenant_id, q: str | None = None, agent: str | None = None,
                   before: str | None = None, limit: int = 30) -> dict:
    from app.core.active_agents import labelled
    from app.models.user import User
    from app.voice_dialer import service

    limit = max(1, min(int(limit or 30), 100))
    query = db.query(VoiceClientOwner).filter(VoiceClientOwner.tenant_id == tenant_id)
    if agent:
        query = query.filter(VoiceClientOwner.user_id == agent)
    digits = "".join(ch for ch in (q or "") if ch.isdigit())
    if q and q.strip():
        # Phone digits, or a client name (via their lead).
        name_phones = [
            replies.normalize_phone(p) for (p,) in db.query(SmsLead.phone_number)
            .filter(SmsLead.tenant_id == tenant_id, SmsLead.customer_name.ilike(f"%{q.strip()}%"))
            .limit(200).all()
        ]
        conds = [VoiceClientOwner.phone_number.in_([p for p in name_phones if p])]
        if len(digits) >= 3:
            conds.append(VoiceClientOwner.phone_number.like(f"%{digits[-10:]}%"))
        from sqlalchemy import or_

        query = query.filter(or_(*conds))
    if before:
        try:
            query = query.filter(VoiceClientOwner.updated_at < datetime.fromisoformat(before.replace("Z", "+00:00")))
        except ValueError:
            pass
    rows = query.order_by(VoiceClientOwner.updated_at.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    ids = {r.user_id for r in rows} | {r.set_by_user_id for r in rows if r.set_by_user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}

    def name(uid):
        u = users.get(uid)
        if u is None:
            return None
        return labelled(f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                        u.status == "active" and u.deleted_at is None)

    items = []
    for r in rows:
        lead = service.find_lead(db, tenant_id, r.phone_number)
        items.append({
            "phone_number": r.phone_number,
            "customer_name": lead.customer_name if lead else None,
            "owner_id": str(r.user_id),
            "owner_name": name(r.user_id),
            "source": r.source,
            "set_by_name": name(r.set_by_user_id) if r.set_by_user_id else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return {"items": items, "more": more, "agents": assignable_agents(db, tenant_id)}


def reassign(db: Session, tenant_id, phone: str, user_id: str, by) -> dict:
    phone_n = replies.normalize_phone(phone)
    if not phone_n:
        raise ValueError("Enter a 10-digit US phone number")
    if user_id not in {a["id"] for a in assignable_agents(db, tenant_id)}:
        raise ValueError("Pick an active agent")
    set_owner(db, tenant_id, phone_n, user_id, source="admin", set_by=by)
    return {"phone_number": phone_n, "owner_id": user_id}


def remove_owner(db: Session, tenant_id, phone: str) -> bool:
    phone_n = replies.normalize_phone(phone)
    n = (
        db.query(VoiceClientOwner)
        .filter(VoiceClientOwner.tenant_id == tenant_id, VoiceClientOwner.phone_number == phone_n)
        .delete(synchronize_session=False)
    )
    db.commit()
    return bool(n)


# ---- Settings ------------------------------------------------------------------------

def settings_part(s: VoiceDialerSettings) -> dict:
    return {
        "max_hold_seconds": _max_hold(s),
        "hours_enabled": bool(s.hours_enabled),
        "open_days": _days(s),
        "open_start_minute": int(s.open_start_minute if s.open_start_minute is not None else 540),
        "open_end_minute": int(s.open_end_minute if s.open_end_minute is not None else 1080),
        "hours_tz": s.hours_tz or "America/New_York",
        "hours_label": hours_label(s),
        # Read-only facts the notes drawer shows (not editable on the page).
        "hold_offer_seconds": MUSIC_SECONDS,
        "recording_notice": settings.VONAGE_RECORDING_NOTICE if settings.CALL_RECORDING_ENABLED else "",
    }


def update_settings_part(s: VoiceDialerSettings, data: dict) -> None:
    def clamp(v, lo, hi):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return None

    if "max_hold_seconds" in data:
        v = clamp(data["max_hold_seconds"], 60, 600)
        if v is not None:
            s.max_hold_seconds = v
    if "hours_enabled" in data:
        s.hours_enabled = bool(data["hours_enabled"])
    if "open_days" in data and isinstance(data["open_days"], list):
        days = sorted({int(d) for d in data["open_days"] if str(d).isdigit() and 0 <= int(d) <= 6})
        s.open_days = ",".join(str(d) for d in days)
    start = clamp(data.get("open_start_minute"), 0, 1439) if "open_start_minute" in data else None
    end = clamp(data.get("open_end_minute"), 1, 1440) if "open_end_minute" in data else None
    if start is not None:
        s.open_start_minute = start
    if end is not None:
        s.open_end_minute = end
    if int(s.open_end_minute) <= int(s.open_start_minute):
        s.open_end_minute = min(1440, int(s.open_start_minute) + 60)
    if "hours_tz" in data and data["hours_tz"] in TZ_NAMES:
        s.hours_tz = data["hours_tz"]
