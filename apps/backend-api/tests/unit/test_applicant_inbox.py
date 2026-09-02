"""Dedicated hiree (applicant) SMS number — pool, lead-pool exclusion, send, inbound.

Guards the business rule: the reserved number (+1 772 315 0752) is used ONLY to text
hirees, is NEVER handed to a lead send, inbound replies to it land in the applicant
inbox (not the lead pipeline), and the LEAD first-template lockdown is untouched.
"""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core import applicant_numbers as an
from app.ai.services import sender_pool
from app.ai.services.communication_provider import communication_service
from app.applicant_inbox.inbound import route_inbound_if_applicant
from app.applicant_inbox.provider import applicant_provider


@pytest.fixture(autouse=True)
def _pool(monkeypatch):
    """Pin the hiree pool so the tests don't depend on env."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_SMS_FROM_NUMBERS", "17723150752", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_SMS_FROM_NUMBER", "17723150752", raising=False)
    yield


# --------------------------------------------------------------- pool parsing
def test_applicant_pool_parsing():
    assert an.applicant_pool() == ["+17723150752"]


def test_is_applicant_number_is_format_agnostic():
    for variant in ("+1 (772) 315-0752", "7723150752", "17723150752", "+17723150752"):
        assert an.is_applicant_number(variant), variant
    assert not an.is_applicant_number("3055551234")
    assert not an.is_applicant_number("")


def test_pick_sender_returns_pool_number():
    assert an.pick_sender() == "+17723150752"


# ------------------------------------------------ excluded from lead sends
def test_reserved_predicate():
    assert sender_pool._is_reserved("+17723150752") is True
    assert sender_pool._is_reserved("+13055551234") is False


def test_select_sender_drops_the_hiree_number():
    # A pool of [hiree, lead] must resolve to the LEAD number, never the hiree one.
    chosen = sender_pool.select_sender(pool=["+17723150752", "+13055551234"])
    assert chosen == "+13055551234"


def test_select_sender_raises_when_only_the_hiree_number():
    # If the hiree number is the only thing configured, there are no lead senders.
    with pytest.raises(RuntimeError):
        sender_pool.select_sender(pool=["+17723150752"])


# ------------------------------------------------ lead lockdown untouched
@pytest.mark.parametrize("kind", ["other", "ai_reply", "follow_up", ""])
def test_lead_first_template_lockdown_still_blocks(kind):
    res = communication_service.send_sms(to="+13055551234", body="x", kind=kind)
    assert res.get("blocked_by") == "first_template_only"


# ------------------------------------------------ applicant send provider
def test_applicant_send_graceful_without_creds(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_AUTH_HEADER", "", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_SMS_LIVE_SEND_ENABLED", True, raising=False)
    res = applicant_provider.send(to="+17865551234", body="hi")
    assert res["status"] == "skipped"
    assert res["error"] == "applicant_sms_not_configured"
    assert res["from"] == "+17723150752"  # would have sent from the hiree number


def test_applicant_configured_via_authorization_header(monkeypatch):
    """Auth can be a ready-made Authorization header alone — no key/secret needed."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_AUTH_HEADER", "Basic YWJjOjEyMw==", raising=False)
    assert applicant_provider.auth_header() == "Basic YWJjOjEyMw=="
    assert applicant_provider.configured is True


def test_applicant_send_respects_disable_flag(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_SMS_LIVE_SEND_ENABLED", False, raising=False)
    res = applicant_provider.send(to="+17865551234", body="hi")
    assert res["status"] == "skipped"
    assert res["error"] == "applicant_live_send_disabled"


def test_applicant_send_configured_with_just_api_key(monkeypatch):
    """The hiree channel sends on basic auth alone — key + secret + base + number."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_BASE_URL", "https://eu.app.api.sinch.com/v1", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_KEY", "hiree-key", raising=False)
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_SECRET", "hiree-secret", raising=False)
    assert applicant_provider.configured is True


# ------------------------------------------------ dedicated, lead-independent config
def test_applicant_config_does_not_fall_back_to_lead(monkeypatch):
    # The hiree channel must NEVER read the lead account's key — they are separate.
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "lead-key", raising=False)
    assert applicant_provider.api_key() == ""          # blank, not the lead key
    assert applicant_provider.configured is False        # so it won't send via the lead account


def test_applicant_config_uses_dedicated_when_set(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_BASE_URL", "https://sub.example/v1", raising=False)
    assert applicant_provider.base_url() == "https://sub.example/v1"


def test_applicant_e164_adds_us_country_code():
    # A 10-digit hiree number must become +1XXXXXXXXXX, not +XXXXXXXXXX (which the
    # provider reads as another country, e.g. +55 Brazil) — that was the failed-send bug.
    from app.applicant_inbox.provider import _e164
    assert _e164("5513596301") == "+15513596301"
    assert _e164("(551) 359-6301") == "+15513596301"
    assert _e164("15513596301") == "+15513596301"    # already has the 1
    assert _e164("+17723150752") == "+17723150752"
    assert _e164("") == ""


def test_applicant_endpoint_tolerates_base_url_without_v1(monkeypatch):
    # The Sinch console shows the host WITHOUT /v1; the API needs /v1/messages.
    from app.core.config import settings
    for base in ("https://eu.app.api.sinch.com/", "https://eu.app.api.sinch.com",
                 "https://eu.app.api.sinch.com/v1", "https://eu.app.api.sinch.com/v1/"):
        monkeypatch.setattr(settings, "APPLICANT_ENGAGECLOUD_API_BASE_URL", base, raising=False)
        assert applicant_provider._endpoint("messages") == "https://eu.app.api.sinch.com/v1/messages"


# ------------------------------------------------ dedicated webhook validation
def test_applicant_webhook_open_in_non_prod_without_secret(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGE_CLOUD_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "development", raising=False)
    assert applicant_provider.validate_webhook(b"{}", {}) is True


def test_applicant_webhook_validates_shared_secret(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "APPLICANT_ENGAGE_CLOUD_WEBHOOK_SECRET", "s3cret", raising=False)
    ok = applicant_provider.validate_webhook(b"{}", {"X-EngageCloud-Webhook-Secret": "s3cret"})
    bad = applicant_provider.validate_webhook(b"{}", {"X-EngageCloud-Webhook-Secret": "nope"})
    assert ok is True and bad is False


# ------------------------------------------------ inbound routing
def _hiree(phone):
    h = MagicMock()
    h.id = uuid4()
    h.tenant_id = uuid4()
    h.phone = phone
    return h


def test_inbound_to_hiree_number_records_applicant_message():
    db = MagicMock()
    hiree = _hiree("(786) 555-1234")
    db.query.return_value.filter.return_value.all.return_value = [hiree]

    handled = route_inbound_if_applicant(db, to_number="17723150752",
                                         from_number="+1 786 555 1234", body="yes please")
    assert handled is True
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.direction == "INBOUND"
    assert added.contact_type == "hiree"
    assert added.hiree_id == hiree.id
    assert added.tenant_id == hiree.tenant_id
    assert added.body == "yes please"
    db.commit.assert_called_once()


def test_inbound_to_lead_number_is_not_handled_here():
    db = MagicMock()
    handled = route_inbound_if_applicant(db, to_number="13055551234",
                                         from_number="+17865551234", body="stop")
    assert handled is False           # falls through to the lead pipeline
    db.add.assert_not_called()


def test_inbound_to_hiree_number_unknown_sender_stays_out_of_lead_flow():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []  # no hiree matches
    handled = route_inbound_if_applicant(db, to_number="17723150752",
                                         from_number="+19998887777", body="hello")
    assert handled is True            # handled (swallowed) — never a lead
    db.add.assert_not_called()


# ------------------------------------------------ inbound polling (no webhook)
def test_poll_applicant_replies_files_inbound(monkeypatch):
    from app.applicant_inbox import reply_polling
    from app.ai.services.communication_provider import communication_service
    from app.core.redis import redis_service

    monkeypatch.setattr(applicant_provider, "fetch_replies", lambda: {
        "success": True,
        "replies": [{"reply_id": "r1", "from": "+15513596301",
                     "to": "17723150752", "body": "Hello"}],
    })
    seen = {}
    monkeypatch.setattr(applicant_provider, "confirm_replies",
                        lambda ids: seen.update(ids=list(ids)) or {"confirmed": len(ids)})
    monkeypatch.setattr(communication_service, "mark_inbound_seen", lambda *a, **k: True)
    monkeypatch.setattr(redis_service, "client", MagicMock())

    db = MagicMock()
    hiree = _hiree("(551) 359-6301")
    db.query.return_value.filter.return_value.all.return_value = [hiree]

    res = reply_polling.poll_applicant_replies_once(db)
    assert res["processed"] == 1
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.direction == "INBOUND" and added.body == "Hello"
    assert added.hiree_id == hiree.id
    assert seen["ids"] == ["r1"]            # confirmed back to the provider


def test_poll_applicant_replies_noop_when_unconfigured(monkeypatch):
    from app.applicant_inbox import reply_polling
    monkeypatch.setattr(applicant_provider, "fetch_replies",
                        lambda: {"success": False, "replies": [], "error": "applicant_sms_not_configured"})
    db = MagicMock()
    res = reply_polling.poll_applicant_replies_once(db)
    assert res["processed"] == 0
    db.add.assert_not_called()
