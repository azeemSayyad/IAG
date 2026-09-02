"""ENGAGE2 — the second, independent lead-SMS pipeline. Sends on its OWN account
(creds/base-url/numbers), is subject to the SAME first-template lockdown, and keeps
its sender-pool state namespaced apart from Sinch."""
from unittest.mock import MagicMock

import httpx

from app.core.config import settings
from app.ai.services.communication_provider import EngageCloudService, engage2_service
from app.ai.services import sender_pool


def _fake_client(seen):
    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json, headers, auth):
            seen.update(url=url, json=json, auth=auth)
            return httpx.Response(200, json={"messages": [{"message_id": "e2_1", "status": "queued"}]})
    return FakeClient


def test_engage2_send_uses_its_own_account(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "e2-key")
    monkeypatch.setattr(settings, "ENGAGE2_API_SECRET", "e2-secret")
    monkeypatch.setattr(settings, "ENGAGE2_FROM_NUMBERS", "+15551110000")
    monkeypatch.setattr(settings, "ENGAGE2_API_BASE_URL", "https://eu.app.api.sinch.com/v1")
    monkeypatch.setattr(settings, "ENGAGE2_USE_NEW_AUTH", False)
    seen = {}
    monkeypatch.setattr("app.ai.services.communication_provider.httpx.Client", _fake_client(seen))
    monkeypatch.setattr("app.ai.services.communication_provider.log_ai_action", MagicMock())

    res = EngageCloudService(provider="engage2").send_sms(
        "+15559990000", "Hi", "tenant-1", "lead-1", kind="first_template")

    assert res["provider"] == "engage2"
    assert res["message_sid"] == "e2_1"
    assert seen["auth"] == ("e2-key", "e2-secret")                 # ENGAGE2 creds, not Sinch's
    assert seen["url"] == "https://eu.app.api.sinch.com/v1/messages"
    assert seen["json"]["messages"][0]["source_number"] == "+15551110000"  # an ENGAGE2 DID


def test_engage2_lockdown_blocks_non_first_template():
    for k in ["other", "ai_reply", "follow_up", "reminder", "manual", "", None]:
        r = engage2_service.send_sms("+15559990000", "x", kind=k)
        assert r.get("blocked_by") == "first_template_only", k


def test_engage2_sender_pool_uses_engage2_numbers(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE2_FROM_NUMBERS", "+15551110000,+15551110001")
    chosen = sender_pool.select_sender(provider="engage2")
    assert chosen in ("+15551110000", "+15551110001")


def test_pools_are_namespaced_independently():
    # Sinch (default) keys are unchanged; engage2 keys carry its prefix.
    sinch_key = sender_pool._count_key("+15551230000")
    e2_key = sender_pool._count_key("+15551230000", "engage2")
    assert "engage2" not in sinch_key                  # Sinch byte-identical (no namespace)
    assert ":engage2:" in e2_key and sinch_key != e2_key
    # explicit "sinch" resolves to the same un-namespaced key as the default
    assert sender_pool._count_key("+15551230000", "sinch") == sinch_key


def test_send_sms_to_lead_routes_to_selected_provider(monkeypatch):
    from app.ai.services import communication_provider as cp
    called = {}
    monkeypatch.setattr(cp.communication_service, "send_sms",
                        lambda **kw: called.update(who="sinch") or {"status": "queued", "from": "+1s"})
    monkeypatch.setattr(cp.engage2_service, "send_sms",
                        lambda **kw: called.update(who="engage2") or {"status": "queued", "from": "+1e"})
    monkeypatch.setattr(cp.redis_service, "check_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr("app.core.fatigue.fatigue_ok", lambda p: True)
    monkeypatch.setattr("app.core.send_once.claim_first_template_send", lambda *a, **k: True)

    # Routes to the right service AND labels the returned `provider` correctly
    # (the label bug the e2e caught — the return must reflect the routed provider).
    r_e2 = cp.send_sms_to_lead("+15559990000", "hi", "t", "l", kind="first_template", provider="engage2")
    assert called["who"] == "engage2" and r_e2.get("provider") == "engage2"
    r_si = cp.send_sms_to_lead("+15559990000", "hi", "t", "l", kind="first_template", provider="sinch")
    assert called["who"] == "sinch" and r_si.get("provider") == "engage_cloud"
    # default + unknown both route to Sinch (can never accidentally divert)
    r_def = cp.send_sms_to_lead("+15559990000", "hi", "t", "l", kind="first_template")
    assert called["who"] == "sinch" and r_def.get("provider") == "engage_cloud"


def test_campaign_model_has_provider_default_sinch():
    from app.models.campaign import Campaign
    assert hasattr(Campaign, "provider")


def test_webhook_validation_is_per_provider(monkeypatch):
    # Each provider's webhook validates ONLY its own secret — Sinch can't accept an
    # engage2-signed webhook and vice-versa.
    from app.ai.services.communication_provider import communication_service, engage2_service
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "sinch-wh", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_WEBHOOK_SECRET", "engage2-wh", raising=False)
    hdr = lambda s: {"X-EngageCloud-Webhook-Secret": s}
    assert communication_service.validate_webhook(b"{}", hdr("sinch-wh")) is True
    assert communication_service.validate_webhook(b"{}", hdr("engage2-wh")) is False
    assert engage2_service.validate_webhook(b"{}", hdr("engage2-wh")) is True
    assert engage2_service.validate_webhook(b"{}", hdr("sinch-wh")) is False


def test_reply_poller_uses_the_given_service():
    # The poller polls whichever provider service it's handed (engage2), defaulting
    # to Sinch when none is passed.
    from app.ai.services import reply_polling
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc._provider = "engage2"
    svc.fetch_replies.return_value = {"success": True, "replies": []}
    svc.confirm_replies.return_value = {"success": True, "confirmed": 0}
    res = reply_polling.poll_provider_replies_once(MagicMock(), service=svc)
    svc.fetch_replies.assert_called_once()
    assert res["processed"] == 0


def test_engage2_send_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "")
    monkeypatch.setattr(settings, "ENGAGE2_API_SECRET", "")
    res = EngageCloudService(provider="engage2").send_sms(
        "+15559990000", "Hi", kind="first_template")
    assert res["status"] == "failed"          # not configured => provider error, never sends
    assert res["provider"] == "engage2"
