"""YES-reply auto dialer — the pure rules (no DB, no network)."""
import hashlib
import time
from datetime import datetime, timezone

import jwt
import pytest

from app.voice_dialer import replies, rules, vonage


@pytest.mark.parametrize("text", [
    "YES", "yes", "Yes!", "Y", "Sure", "OK", "okay", "Ok 👍", "yesss", "Yes please", "yeah", "Yes, okay", "ok sure",
    "Yes call me", "yes but call after 5", "Sure, what's this about", "Call me now", "please call", "can you call me",
    "I'm interested", "interested", "sounds good", "go ahead", "of course", "yeah why not", "👍", "alright",
    "Yes I need insurance", "I want a quote", "absolutely", "No, call me after 5",
])
def test_clear_yes(text):
    assert replies.classify(text) == replies.YES


# The owner's rule: ANY reply that isn't negative is called.
@pytest.mark.parametrize("text", [
    "who is this?", "Who are you", "maybe", "how much?", "what is this about", "not now", "busy", "later",
    "not sure", "I don't know", "hello", "hi", "?", "", None, "Thank you", "No problem", "huh",
    "I already have insurance", "assistant", "what's the policy",
])
def test_any_other_reply_is_called(text):
    assert replies.classify(text) == replies.REPLY


@pytest.mark.parametrize("text,kind", [
    ("STOP", "STOP"), ("stop", "STOP"), ("Unsubscribe", "STOP"), ("yes stop texting me", "STOP"), ("cancel", "STOP"),
    ("END", "STOP"), ("don't call me", "STOP"), ("remove me", "STOP"), ("leave me alone", "STOP"),
    ("no", "NEGATIVE"), ("No thanks", "NEGATIVE"), ("not interested", "NEGATIVE"), ("nope", "NEGATIVE"), ("👎", "NEGATIVE"),
    ("wrong number", "WRONG"), ("You have the wrong person", "WRONG"),
    ("fuck off", "ABUSE"), ("f*ck you", "ABUSE"), ("asshole", "ABUSE"), ("this is a scam", "ABUSE"), ("fake", "ABUSE"),
])
def test_negatives_never_called(text, kind):
    assert replies.classify(text) == kind
    assert kind in replies.NEGATIVE_KINDS and replies.DNC_REASON[kind]


def test_phone_normalisation():
    assert replies.normalize_phone("(678) 988-7287") == "+16789887287"
    assert replies.normalize_phone("16789887287") == "+16789887287"
    assert replies.normalize_phone("+1 678 988 7287") == "+16789887287"
    assert replies.normalize_phone("98872") is None
    assert replies.vonage_number("+16789887287") == "16789887287"


def _utc(h, m=0):
    return datetime(2026, 10, 14, h, m, tzinfo=timezone.utc)  # EDT = UTC-4, PDT = UTC-7


def test_calling_hours_follow_the_clients_zone():
    # 12:30 UTC = 8:30 ET, 5:30 PT
    assert rules.in_call_window(_utc(12, 30), "GA")
    assert not rules.in_call_window(_utc(12, 30), "CA")
    # 23:30 UTC = 7:30 pm ET (ok), 00:30 UTC next day = 8:30 pm ET (not ok)
    assert rules.in_call_window(_utc(23, 30), "GA")
    assert not rules.in_call_window(datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc), "GA")


def test_split_and_unknown_states_take_the_strict_side():
    # 12:30 UTC = 8:30 ET but 7:30 CT: Florida's panhandle is Central -> not yet.
    assert not rules.in_call_window(_utc(12, 30), "Florida")
    # Unknown state must be inside the window in every contiguous zone (11 am ET+).
    assert not rules.in_call_window(_utc(14, 30), None)   # 10:30 ET / 7:30 PT
    assert rules.in_call_window(_utc(15, 30), None)       # 11:30 ET / 8:30 PT


def test_agent_username_round_trip():
    uid = "5d60f98a-fe0f-4a86-a11f-054049a5b315"
    assert vonage.user_id_from_username(vonage.agent_username(uid)) == uid
    assert vonage.user_id_from_username("15615550100") is None


def test_webhook_trust(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "VONAGE_WEBHOOK_TOKEN", "tok123")
    monkeypatch.setattr(settings, "VONAGE_SIGNATURE_SECRET", "sigsecret")
    body = b'{"status":"answered"}'
    assert vonage.webhook_trusted({}, body, "tok123")
    assert not vonage.webhook_trusted({}, body, "wrong")
    good = jwt.encode({"iat": int(time.time()), "payload_hash": hashlib.sha256(body).hexdigest()}, "sigsecret", algorithm="HS256")
    assert vonage.webhook_trusted({"authorization": f"Bearer {good}"}, body, None)
    bad = jwt.encode({"iat": int(time.time()), "payload_hash": "x"}, "sigsecret", algorithm="HS256")
    assert not vonage.webhook_trusted({"authorization": f"Bearer {bad}"}, body, None)
    forged = jwt.encode({"iat": int(time.time())}, "not-the-secret", algorithm="HS256")
    assert not vonage.webhook_trusted({"authorization": f"Bearer {forged}"}, body, None)


def test_production_never_simulates(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "VONAGE_APPLICATION_ID", "")
    monkeypatch.setattr(settings, "VONAGE_PRIVATE_KEY", "")
    monkeypatch.setattr(settings, "VONAGE_PRIVATE_KEY_PATH", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert not vonage.simulating()
    assert not vonage.available()
    # …and with nothing configured, production trusts no webhook.
    monkeypatch.setattr(settings, "VONAGE_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(settings, "VONAGE_SIGNATURE_SECRET", "")
    assert not vonage.webhook_trusted({}, b"{}", None)


# ---- Manual dialing (Dial drawer) ----

def test_manual_number_normalisation():
    from app.voice_dialer import manual

    assert manual.normalize("678-988-7287") == "+16789887287"
    assert manual.normalize("+1 (678) 988-7287") == "+16789887287"
    assert manual.normalize("+44 7700 900123") is None  # US only
    assert manual.normalize("911") is None
    assert manual.normalize("") is None


def test_manual_answer_records_then_connects(monkeypatch):
    import uuid as _uuid
    from types import SimpleNamespace

    from app.voice_dialer import manual

    uid = _uuid.uuid4()
    call = SimpleNamespace(id=_uuid.uuid4(), user_id=uid, status="DIALING", app_leg_uuid=None,
                           phone_number="+16789887287", from_number="+15615550100")
    monkeypatch.setattr(manual, "find", lambda db, cid: call)
    db = SimpleNamespace(commit=lambda: None)
    ncco = manual.on_answer(db, {"uuid": "leg-1", "from_user": vonage.agent_username(uid)}, {"mc": str(call.id)})
    assert call.app_leg_uuid == "leg-1"
    actions = [n["action"] for n in ncco]
    assert actions[-1] == "connect" and ncco[-1]["endpoint"][0]["number"] == "16789887287"
    assert ncco[-1]["from"] == "15615550100"
    # Someone else's browser can't place this call.
    call.app_leg_uuid = None
    assert manual.on_answer(db, {"uuid": "x", "from_user": vonage.agent_username(_uuid.uuid4())},
                            {"mc": str(call.id)})[0]["action"] == "talk"


def test_custom_data_object_or_json_text():
    from app.voice_dialer import webhooks

    assert webhooks._custom({"custom_data": {"purpose": "manual"}}) == {"purpose": "manual"}
    assert webhooks._custom({"custom_data": '{"purpose": "manual", "mc": "1"}'})["mc"] == "1"
    assert webhooks._custom({"custom_data": "not json"}) == {}
    assert webhooks._custom({}) == {}


# ---- Inbound: open hours ----

def _hours(**kw):
    from types import SimpleNamespace

    base = dict(hours_enabled=True, open_days="0,1,2,3,4", open_start_minute=540, open_end_minute=1080,
                hours_tz="America/New_York", max_hold_seconds=300)
    base.update(kw)
    return SimpleNamespace(**base)


def test_open_hours_follow_the_office_zone():
    from app.voice_dialer import inbound

    s = _hours()
    assert inbound.is_open(s, _utc(13, 0))          # Wed 9:00 am ET
    assert not inbound.is_open(s, _utc(12, 59))     # 8:59 am ET
    assert not inbound.is_open(s, _utc(22, 0))      # 6:00 pm ET (end is exclusive)
    assert not inbound.is_open(s, datetime(2026, 10, 17, 15, tzinfo=timezone.utc))  # Saturday
    assert inbound.is_open(_hours(hours_enabled=False), _utc(3, 0))  # hours off = always open


def test_hours_label_reads_naturally():
    from app.voice_dialer import inbound

    assert inbound.hours_label(_hours()) == "Monday to Friday, 9 AM to 6 PM Eastern time"
    assert inbound.hours_label(_hours(open_days="0,2", open_start_minute=570, hours_tz="America/Chicago")) == \
        "Monday and Wednesday, 9:30 AM to 6 PM Central time"


def test_hold_and_hours_settings_are_clamped():
    from app.voice_dialer import inbound

    s = _hours()
    inbound.update_settings_part(s, {"max_hold_seconds": 5000, "open_days": [6, 0, 9, "x"],
                                     "open_start_minute": 600, "open_end_minute": 500, "hours_tz": "Mars/Base"})
    assert s.max_hold_seconds == 600
    assert s.open_days == "0,6"
    assert s.open_end_minute == 660  # end before start -> start + 1 h
    assert s.hours_tz == "America/New_York"


def test_non_us_text_is_kept_not_dialed(monkeypatch):
    """A text from a non-US number is saved for the Inbox; the lead is parked
    (DISPOSITIONED) so neither the pool nor the dialer ever picks it up."""
    from types import SimpleNamespace

    from app.voice_dialer import service

    added = []

    class Q:
        def filter(self, *a, **k): return self
        def order_by(self, *a): return self
        def first(self): return None

    db = SimpleNamespace(query=lambda *a: Q(), add=added.append, flush=lambda: None, commit=lambda: None)
    out, events = service.on_inbound_sms(db, "t1", "447700900123", None, "Yes", None)
    lead = next(x for x in added if type(x).__name__ == "SmsLead")
    msg = next(x for x in added if type(x).__name__ == "SmsMessage")
    assert out["result"] == "saved_not_us" and lead.phone_number == "+447700900123"
    assert lead.status == "DISPOSITIONED" and msg.body == "Yes"
    assert service.on_inbound_sms(db, "t1", "12345", None, "Yes", None)[0]["reason"] == "bad_number"


def test_listed_test_number_counts_as_dialable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "VONAGE_TEST_NUMBERS", "+91 96763 41652")
    assert replies.normalize_phone("919676341652") == "+919676341652"
    assert replies.is_test_number("+919676341652")
    assert replies.normalize_phone("+447700900123") is None  # not listed
    monkeypatch.setattr(settings, "VONAGE_TEST_NUMBERS", "")
    assert replies.normalize_phone("919676341652") is None   # list emptied = off


# The owner's rule, in their own examples: anything is called except a clear
# no / "not interested", block, do-not-disturb, opt-out, abuse, wrong number.
@pytest.mark.parametrize("text", [
    "Call me", "Where are you?", "Who are you?", "I'm fine", "Did you eat?", "I'm good", "I'm ok",
    "Hello", "Not now", "I'm busy", "Thanks", "Actually yes", "Everything", "lol", "Good morning",
])
def test_owner_examples_are_called(text):
    assert replies.classify(text) in (replies.YES, replies.REPLY)


@pytest.mark.parametrize("text,kind", [
    ("Not interested", "NEGATIVE"), ("No, I'm good", "NEGATIVE"), ("Block", "STOP"), ("Block me", "STOP"),
    ("I blocked you", "STOP"), ("Do not disturb", "STOP"), ("DND", "STOP"), ("Dont disturb me", "STOP"),
    ("Fuck off", "ABUSE"), ("fuuuck you", "ABUSE"),
])
def test_owner_examples_are_removed(text, kind):
    assert replies.classify(text) == kind


@pytest.mark.parametrize("text", ["Start", "START", "Unstop", "unstop please", "Yes", "Ok", "Call me",
                                  "I'm interested", "I want a quote", "Resubscribe"])
def test_clear_yes_words_that_opt_back_in(text):
    assert replies.classify(text) == replies.YES


@pytest.mark.parametrize("text", ["Cool", "hi", "who is this?", "thanks", "lol"])
def test_vague_texts_do_not_opt_back_in(text):
    assert replies.classify(text) == replies.REPLY
