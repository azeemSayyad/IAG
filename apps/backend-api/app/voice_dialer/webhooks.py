"""Vonage webhook handling — ONE place that reads Vonage's payloads.

The HTTP routes (router.py) and the dev simulator (simulator.py) both call
these, so the simulator exercises exactly the code a real Vonage request hits.
Each function takes the parsed payload (JSON body merged with query params)
plus our own query params (`a` = attempt id, `leg`), and returns what Vonage
expects back plus socket events for the caller to flush.

Payload shapes (Vonage docs):
  answer        {uuid, conversation_uuid, from, to, from_user?}        -> NCCO list
  event         {uuid, status, direction, timestamp, duration?, ...}   status: started |
                ringing | answered | human | machine | completed | busy | timeout |
                unanswered | failed | rejected | cancelled
  inbound-sms   Messages API {message_uuid, from, to, text, channel:"sms", message_type:"text"}
                (legacy SMS API {msisdn, to, text, messageId} also accepted)
  recording     {recording_url, recording_uuid, start_time, end_time, size, conversation_uuid}
  optout input  {dtmf: {digits, timed_out}, uuid}
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.config import settings
from app.voice_dialer import manual, service, vonage


def _custom(data: dict) -> dict:
    """The Client SDK's serverCall(context) arrives as `custom_data` — an
    object, or (depending on the SDK) its JSON text."""
    c = data.get("custom_data")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            return {}
    return c if isinstance(c, dict) else {}


def answer(db: Session, data: dict, params: dict) -> tuple[list[dict], list[dict]]:
    leg = data.get("uuid") or data.get("call_uuid") or ""

    # 0) Manual dialing (Dial drawer): the person we called picked up — the
    #    recording notice — or the caller's browser starting the call.
    if params.get("leg") == "manual_notice":
        return manual.notice_ncco(), []
    custom = _custom(data)
    if custom.get("purpose") == "manual":
        return manual.on_answer(db, data, custom), []

    # 1) Our outbound call to a customer was answered: hold them silently while
    #    machine detection decides (the "human" event bridges them).
    if params.get("leg") == "customer":
        att = service.find_attempt(db, params.get("a"), leg)
        if att is None:
            return [{"action": "talk", "text": "Goodbye."}], []
        att.call_uuid = att.call_uuid or leg
        # The answer URL may come BEFORE or AFTER we bridged (with machine
        # detection on, Vonage asks it only once detection decides). Whatever
        # the order, the reply must match where the call already is.
        if att.status == "CONNECTED":
            db.commit()
            return service.room_only_ncco(att), []
        if att.status == "ABANDONED":
            return service.abandon_ncco(service.get_settings(db, str(att.tenant_id)), att), []
        if att.status not in service.IN_FLIGHT:
            return [{"action": "talk", "text": "Goodbye."}], []  # voicemail / cancelled: we are hanging up
        if settings.VONAGE_CONNECT_ON_ANSWER or not settings.VONAGE_MACHINE_DETECTION:
            # Straight into the call room with an agent, in this very reply.
            # With detection on it keeps listening and may still pull them
            # apart; with it off a pickup counts as a person.
            ncco: list[dict] = []
            events = service.connect_human(db, att, early=settings.VONAGE_MACHINE_DETECTION, ncco_out=ncco)
            return (ncco or service.hold_ncco(att)), events
        if att.status in ("DIALING", "RINGING"):
            att.status = "ANSWERED"
            att.answered_at = service._now()
            att.call_uuid = att.call_uuid or leg
            db.commit()
        return service.hold_ncco(att), []

    # 2) An agent's browser opening its headset line (Vonage Client SDK).
    who = data.get("from_user") or data.get("from") or ""
    user_id = vonage.user_id_from_username(who if isinstance(who, str) else "")
    if user_id:
        return service.on_agent_line(db, user_id, leg)

    # 3) A client calling one of our numbers back.
    tenant_id = service.owner_tenant_id(db)
    if not tenant_id:
        return [{"action": "talk", "text": "Thank you for calling. Goodbye."}], []
    return service.on_inbound_call(db, tenant_id, str(data.get("from") or ""), data.get("to"), leg)


def event(db: Session, data: dict, params: dict) -> list[dict]:
    if params.get("mc"):
        call = manual.find(db, params.get("mc"))
        return manual.on_event(db, call, data, "callee") if call is not None else []
    if data.get("recording_url") and not params.get("a"):
        # A room recording delivered to the application's event URL instead
        # of the room's own eventUrl: match it to its call by time.
        att = service.attempt_for_recording(db, data)
        if att is not None:
            service.store_recording(db, att, data)
        return []
    leg = data.get("uuid") or data.get("call_uuid")
    att = service.find_attempt(db, params.get("a"), leg)
    if att is not None:
        events = service.on_call_event(db, att, data)
        events += service.tick(db, str(att.tenant_id))  # a slot may have freed up
        return events
    # A manual call's browser leg (the application event URL)?
    call = manual.find_by_leg(db, leg)
    if call is not None:
        return manual.on_event(db, call, data, "app")
    # Not a customer call: an agent's headset line ending?
    status = str(data.get("status") or "").lower()
    if leg and status in ("completed", "failed", "rejected", "cancelled", "timeout", "unanswered", "busy"):
        return service.on_agent_line_ended(db, leg)
    return []


def inbound_sms(db: Session, data: dict) -> tuple[dict, list[dict]]:
    frm = data.get("from") or data.get("msisdn")
    if isinstance(frm, dict):
        frm = frm.get("number")
    to = data.get("to")
    if isinstance(to, dict):
        to = to.get("number")
    text = data.get("text")
    if text is None and isinstance(data.get("message"), dict):
        text = (data["message"].get("content") or {}).get("text")
    msg_id = data.get("message_uuid") or data.get("messageId")
    tenant_id = service.owner_tenant_id(db)
    if not tenant_id or not frm:
        return {"ok": False, "reason": "no tenant or sender"}, []
    return service.on_inbound_sms(db, tenant_id, str(frm), to, text, msg_id)


def recording(db: Session, data: dict, params: dict) -> dict:
    if params.get("mc"):
        call = manual.find(db, params.get("mc"))
        return manual.store_recording(db, call, data) if call is not None else {"ok": False}
    att = service.find_attempt(db, params.get("a"), None)
    if att is None:
        return {"ok": False}
    return service.store_recording(db, att, data)


def hold(db: Session, data: dict, params: dict) -> tuple[list[dict], list[dict]]:
    """A caller on hold finished a music clip (or pressed a key)."""
    from app.voice_dialer import inbound

    att = service.find_attempt(db, params.get("a"), None)
    dtmf = data.get("dtmf")
    digits = dtmf.get("digits") if isinstance(dtmf, dict) else data.get("dtmf")
    return inbound.on_hold_input(db, att, digits)


def optout(db: Session, data: dict, params: dict) -> list[dict]:
    att = service.find_attempt(db, params.get("a"), None)
    if att is None:
        return [{"action": "talk", "text": "Goodbye."}]
    dtmf = data.get("dtmf")
    digits = dtmf.get("digits") if isinstance(dtmf, dict) else data.get("dtmf")
    return service.on_optout(db, att, digits)
