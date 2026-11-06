"""Telnyx carrier layer: NCCO -> TeXML, webhook payloads, trust."""
import base64
import io
import time
import wave
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.config import settings
from app.voice_dialer import telnyx


def _xml(script):
    return ET.fromstring(telnyx.to_texml(script))


def test_hold_loop_gathers_one_key_then_redirects():
    root = _xml([
        {"action": "talk", "text": "Please hold & stay"},
        {"action": "stream", "streamUrl": ["https://x/hold-music.wav?k=1"], "bargeIn": True, "loop": 1},
        {"action": "input", "type": ["dtmf"], "dtmf": {"maxDigits": 1, "timeOut": 1},
         "eventUrl": ["https://x/hold?a=7&k=1"]},
    ])
    say, gather, redirect = list(root)
    assert say.tag == "Say" and say.text == "Please hold & stay"
    assert gather.tag == "Gather" and gather.get("numDigits") == "1"
    assert gather.get("action") == "https://x/hold?a=7&k=1"
    assert [c.tag for c in gather] == ["Play"]  # bargeIn audio is interruptible
    # A timeout falls through to the same URL with no digits.
    assert redirect.tag == "Redirect" and redirect.text == "https://x/hold?a=7&k=1"


def test_call_room_is_a_recorded_conference():
    root = _xml([
        {"action": "talk", "text": "This call is recorded."},
        {"action": "conversation", "name": "iag-call-1", "startOnEnter": True, "endOnExit": False,
         "record": True, "eventUrl": ["https://x/recording?a=1"]},
    ])
    conf = root.find("Dial/Conference")
    assert conf.text == "iag-call-1"
    assert conf.get("endConferenceOnExit") == "false" and conf.get("beep") == "false"
    assert conf.get("record") == "record-from-start"
    assert conf.get("recordingStatusCallback") == "https://x/recording?a=1"


def test_agent_room_without_record_has_no_recording():
    conf = _xml([{"action": "conversation", "name": "iag-agent-1", "endOnExit": True}]).find("Dial/Conference")
    assert conf.get("endConferenceOnExit") == "true" and conf.get("record") is None


def test_conference_waits_in_silence(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_CALLBACK_BASE_URL", "https://h")
    conf = _xml([{"action": "conversation", "name": "iag-agent-1", "endOnExit": True}]).find("Dial/Conference")
    assert conf.get("waitUrl") == "https://h/api/v1/voice/telnyx/silence.wav"
    assert conf.get("waitMethod") == "GET"
    with wave.open(io.BytesIO(telnyx.silence_wav())) as w:
        assert w.getnframes() == 8000 * 60 and not any(w.readframes(8000))


def test_manual_connect_records_and_dials_the_number():
    root = _xml([
        {"action": "record", "eventUrl": ["https://x/recording?mc=1"]},
        {"action": "connect", "from": "15615550100", "timeout": 45,
         "eventUrl": ["https://x/event?mc=1&leg=callee"],
         "endpoint": [{"type": "phone", "number": "15615550199", "onAnswer": {"url": "https://x/answer?leg=manual_notice"}}]},
    ])
    dial = root.find("Dial")
    assert dial.get("callerId") == "+15615550100" and dial.get("record") == "record-from-answer-dual"
    num = dial.find("Number")
    assert num.text == "+15615550199"
    assert num.get("statusCallback") == "https://x/event?mc=1&leg=callee"
    assert num.get("url") == "https://x/answer?leg=manual_notice"


def test_empty_script_hangs_up():
    assert list(_xml([])) == []


def test_normalize_status_and_machine_detection():
    n = telnyx.normalize({"CallSid": "v3:a", "CallStatus": "in-progress", "From": "sip:gencred1@sip.telnyx.com"})
    assert n["uuid"] == "v3:a" and n["status"] == "answered" and n["from"] == "gencred1@sip.telnyx.com"
    assert telnyx.normalize({"CallStatus": "no-answer"})["status"] == "timeout"
    assert telnyx.normalize({"CallStatus": "canceled"})["status"] == "cancelled"
    assert telnyx.normalize({"CallStatus": "completed", "CallDuration": "42"})["duration"] == "42"
    assert telnyx.normalize({"AnsweredBy": "machine_end_beep", "CallStatus": "in-progress"})["status"] == "machine"
    assert telnyx.normalize({"AnsweredBy": "human"})["status"] == "human"
    assert telnyx.normalize({"Digits": "1"})["dtmf"] == {"digits": "1"}
    assert telnyx.normalize({"RecordingUrl": "https://r/1.wav", "RecordingSid": "r1"})["recording_url"] == "https://r/1.wav"


def test_inbound_message_reads_v2_envelope():
    body = {"data": {"event_type": "message.received", "payload": {
        "id": "m1", "text": "YES", "from": {"phone_number": "+15615550100"},
        "to": [{"phone_number": "+15615550199"}]}}}
    assert telnyx.inbound_message(body) == {
        "from": "+15615550100", "to": "+15615550199", "text": "YES", "message_uuid": "m1"}
    # Delivery receipts are not texts from a client.
    assert telnyx.inbound_message({"data": {"event_type": "message.finalized", "payload": {}}}) == {}


def test_webhook_signature_and_token(monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(settings, "TELNYX_PUBLIC_KEY", pub)
    monkeypatch.setattr(settings, "VOICE_WEBHOOK_TOKEN", "tok")
    body = b"CallSid=v3%3Aa&CallStatus=completed"
    ts = str(int(time.time()))
    sig = base64.b64encode(key.sign(f"{ts}|".encode() + body)).decode()
    good = {"telnyx-signature-ed25519": sig, "telnyx-timestamp": ts}
    assert telnyx.webhook_trusted(good, body, None)
    assert not telnyx.webhook_trusted(good, body + b"x", None)  # tampered
    old = str(int(time.time()) - 3600)
    stale = base64.b64encode(key.sign(f"{old}|".encode() + body)).decode()
    assert not telnyx.webhook_trusted({"telnyx-signature-ed25519": stale, "telnyx-timestamp": old}, body, None)
    assert telnyx.webhook_trusted({}, body, "tok")
    assert not telnyx.webhook_trusted({}, body, "nope")


def test_production_never_trusts_unsigned(monkeypatch):
    monkeypatch.setattr(settings, "TELNYX_API_KEY", "")
    monkeypatch.setattr(settings, "TELNYX_PUBLIC_KEY", "")
    monkeypatch.setattr(settings, "VOICE_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert not telnyx.simulating()
    assert not telnyx.webhook_trusted({}, b"{}", None)


def test_webhook_urls(monkeypatch):
    from app.voice_dialer import service

    monkeypatch.setattr(settings, "VOICE_CALLBACK_BASE_URL", "https://h")
    monkeypatch.setattr(settings, "VOICE_WEBHOOK_TOKEN", "")
    assert service._url("answer", leg="agent") == "https://h/api/v1/voice/telnyx/answer?leg=agent"


def test_agent_username_has_no_dashes():
    assert telnyx.agent_username("5d60f98a-fe0f-4a86-a11f-054049a5b315") == "iag_5d60f98afe0f4a86a11f054049a5b315"


def test_room_recording_lookup(monkeypatch):
    monkeypatch.setattr(telnyx, "simulating", lambda: False)
    monkeypatch.setattr(telnyx, "account_sid", lambda: "acc")

    def fake(method, path, **kw):
        if path.endswith("/Conferences"):
            assert kw["params"] == {"FriendlyName": "iag-call-1"}
            return {"conferences": [{"sid": "c1"}]}
        assert path.endswith("/Conferences/c1/Recordings")
        return {"recordings": [{"status": "processing", "media_url": "https://r/no"},
                               {"status": "completed", "media_url": "https://r/yes"}]}

    monkeypatch.setattr(telnyx, "_request", fake)
    assert telnyx.conference_recording_url("iag-call-1") == "https://r/yes"
    monkeypatch.setattr(telnyx, "_request", lambda *a, **k: {"conferences": []})
    assert telnyx.conference_recording_url("iag-call-1") is None


def test_listener_joins_the_call_room_muted():
    conf = _xml([{"action": "conversation", "name": "iag-call-1", "startOnEnter": False, "endOnExit": False,
                  "muted": True, "label": "sv-1"}]).find("Dial/Conference")
    assert conf.text == "iag-call-1" and conf.get("muted") == "true" and conf.get("participantLabel") == "sv-1"
    assert conf.get("startConferenceOnEnter") == "false" and conf.get("record") is None
    # Everyone else is unchanged: no muted attribute at all.
    assert _xml([{"action": "conversation", "name": "iag-call-1"}]).find("Dial/Conference").get("muted") is None


def test_participant_update_targets_the_live_room(monkeypatch):
    monkeypatch.setattr(telnyx, "simulating", lambda: False)
    monkeypatch.setattr(telnyx, "account_sid", lambda: "acc")
    seen = []

    def fake(method, path, **kw):
        seen.append((method, path, kw.get("params"), kw.get("form")))
        if path.endswith("/Conferences"):
            return {"conferences": [{"sid": "c1"}]}
        return {"coaching": True}

    monkeypatch.setattr(telnyx, "_request", fake)
    assert telnyx.update_participant("iag-call-1", "v3:sv", {"Muted": "true"}) == {"coaching": True}
    assert seen[0][2] == {"FriendlyName": "iag-call-1", "Status": "in-progress"}
    assert seen[1][:2] == ("POST", "/texml/Accounts/acc/Conferences/c1/Participants/v3:sv")
    assert seen[1][3] == {"Muted": "true"}
