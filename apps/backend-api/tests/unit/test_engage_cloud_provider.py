import hashlib
import hmac
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx

from app.ai.services.communication_provider import EngageCloudService
from app.core.config import settings


def test_send_sms_uses_messagemedia_basic_auth(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "api-key")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_SECRET", "api-secret")
    monkeypatch.setattr(settings, "ENGAGECLOUD_FROM_NUMBERS", "+15551230000")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_BASE_URL", "https://eu.app.api.sinch.com/v1")
    monkeypatch.setattr(settings, "ENGAGECLOUD_USE_NEW_AUTH", False)
    # Pin the sender so this test focuses on the HTTP/auth/payload path; the actual
    # sender-selection (state pool vs full-fleet fallback) is covered by
    # test_sender_fleet_fallback.py.
    monkeypatch.setattr(
        "app.ai.services.communication_provider.EngageCloudService._sender",
        lambda self, tenant_id=None, lead_id=None: "+15551230000",
    )

    seen = {}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, headers, auth):
            seen.update({"url": url, "json": json, "headers": headers, "auth": auth})
            return httpx.Response(200, json={"messages": [{"message_id": "msg_123", "status": "queued"}]})

    monkeypatch.setattr("app.ai.services.communication_provider.httpx.Client", FakeClient)
    monkeypatch.setattr("app.ai.services.communication_provider.log_ai_action", MagicMock())

    # kind="first_template" is the one send the lockdown allows — exercise the real
    # send path (the lockdown blocks every other kind before the HTTP call).
    result = EngageCloudService().send_sms("+15559870000", "Hello", "tenant-1", "lead-1", kind="first_template")

    assert result["provider"] == "engage_cloud"
    assert result["message_sid"] == "msg_123"
    assert seen["url"] == "https://eu.app.api.sinch.com/v1/messages"
    assert seen["auth"] == ("api-key", "api-secret")
    assert seen["json"] == {
        "messages": [
            {
                "content": "Hello",
                "destination_number": "+15559870000",
                "source_number": "+15551230000",
            }
        ]
    }


def test_webhook_hmac_hex_signature_validates(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "secret")
    body = b'{"event":"message.received"}'
    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert EngageCloudService().validate_webhook(body, {"X-EngageCloud-Signature": f"sha256={sig}"})


def test_webhook_hmac_base64_signature_validates(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "secret")
    body = b'{"event":"message.received"}'
    sig = b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()

    assert EngageCloudService().validate_webhook(body, {"X-Engage-Signature": sig})


def test_webhook_stale_timestamp_rejects(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "secret")
    body = b'{"event":"message.received"}'
    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)

    assert not EngageCloudService().validate_webhook(
        body,
        {
            "X-EngageCloud-Signature": sig,
            "X-EngageCloud-Timestamp": stale.isoformat(),
        },
    )


def test_send_sms_requires_engage_cloud_sender(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "api-key")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_SECRET", "api-secret")
    monkeypatch.setattr(settings, "ENGAGECLOUD_FROM_NUMBERS", "")
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+15559990000")
    # Unmatched-state leads now fall back to the hardcoded state fleet, so a sender is
    # missing ONLY when the fleet is ALSO empty. Empty both -> still a hard error.
    monkeypatch.setattr("app.core.state_sender_numbers.all_sender_numbers", lambda: [])

    result = EngageCloudService().send_sms("+15559870000", "Hello", kind="first_template")

    assert result["status"] == "failed"
    assert "ENGAGECLOUD_FROM_NUMBERS" in result["error"]


def test_fetch_replies_normalizes_sinch_reply_contract(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "api-key")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_SECRET", "api-secret")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_BASE_URL", "https://eu.app.api.sinch.com/v1")

    seen = {}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers, auth):
            seen.update({"url": url, "headers": headers, "auth": auth})
            return httpx.Response(200, json={
                "replies": [
                    {
                        "message_id": "msg_123",
                        "reply_id": "reply_123",
                        "source_number": "+15559870000",
                        "destination_number": "+15551230000",
                        "content": "Yes, please book me.",
                    }
                ]
            })

    monkeypatch.setattr("app.ai.services.communication_provider.httpx.Client", FakeClient)

    result = EngageCloudService().fetch_replies()

    assert result["success"] is True
    assert seen["url"] == "https://eu.app.api.sinch.com/v1/replies"
    assert seen["auth"] == ("api-key", "api-secret")
    assert result["replies"][0]["reply_id"] == "reply_123"
    assert result["replies"][0]["from"] == "+15559870000"
    assert result["replies"][0]["body"] == "Yes, please book me."


def test_confirm_replies_uses_sinch_confirm_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "api-key")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_SECRET", "api-secret")
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_BASE_URL", "https://eu.app.api.sinch.com/v1")

    seen = {}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, headers, auth):
            seen.update({"url": url, "json": json, "headers": headers, "auth": auth})
            return httpx.Response(202, json={})

    monkeypatch.setattr("app.ai.services.communication_provider.httpx.Client", FakeClient)

    result = EngageCloudService().confirm_replies(["reply_123"])

    assert result["success"] is True
    assert result["confirmed"] == 1
    assert seen["url"] == "https://eu.app.api.sinch.com/v1/replies/confirmed"
    assert seen["json"] == {"reply_ids": ["reply_123"]}
    assert seen["auth"] == ("api-key", "api-secret")


def test_parse_inbound_payload_normalizes_contract():
    payload = {
        "event": "message.received",
        "data": {
            "id": "msg_in_1",
            "from": "+15550001111",
            "to": "+15550002222",
            "body": "Interested",
            "conversationId": "thread_1",
        },
    }

    parsed = EngageCloudService().parse_webhook(payload)

    assert parsed["kind"] == "inbound_message"
    assert parsed["message_sid"] == "msg_in_1"
    assert parsed["from"] == "+15550001111"
    assert parsed["body"] == "Interested"
    assert parsed["thread_id"] == "thread_1"


def test_parse_delivery_payload_normalizes_contract():
    payload = {
        "event": "DeliveryResult",
        "data": {
            "messageId": "msg_out_1",
            "deliveryStatus": "DELIVERED",
            "to": "+15550001111",
        },
    }

    parsed = EngageCloudService().parse_webhook(payload)

    assert parsed["kind"] == "delivery_status"
    assert parsed["message_sid"] == "msg_out_1"
    assert parsed["status"] == "delivered"
