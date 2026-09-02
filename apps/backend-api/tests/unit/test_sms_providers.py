"""Lead-SMS provider registry — Sinch resolves to the existing config (unchanged),
engage2 resolves to ENGAGE2_*, and unknown/blank can never divert Sinch traffic."""
from app.core.config import settings
from app.ai.services import sms_providers as sp


def test_normalize_defaults_to_sinch():
    for v in ("", None, "sinch", "Sinch", "telnyx", "twilio", "vonage", "nonsense"):
        assert sp.normalize_provider(v) == sp.SINCH, v


def test_normalize_engage2_aliases():
    for v in ("engage2", "engage_cloud", "EngageCloud", "Engage Cloud", "engage-cloud"):
        assert sp.normalize_provider(v) == sp.ENGAGE2, v


def test_sinch_config_is_the_existing_engagecloud(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "sinch-key", raising=False)
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_SECRET", "sinch-secret", raising=False)
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_BASE_URL", "https://eu.app.api.sinch.com/v1", raising=False)
    monkeypatch.setattr(settings, "ENGAGE_CLOUD_WEBHOOK_SECRET", "sinch-wh", raising=False)
    cfg = sp.get_provider_config("sinch")
    assert cfg.key == "sinch"
    assert cfg.name == "engage_cloud"        # back-compat record name (unchanged)
    assert cfg.api_key == "sinch-key"
    assert cfg.api_secret == "sinch-secret"
    assert cfg.base_url == "https://eu.app.api.sinch.com/v1"
    assert cfg.webhook_secret == "sinch-wh"


def test_engage2_config_reads_engage2_env(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "e2-key", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_API_SECRET", "e2-secret", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_API_BASE_URL", "https://eu.app.api.sinch.com/v1", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_FROM_NUMBERS", "+13055550001,+13055550002", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_WEBHOOK_SECRET", "e2-wh", raising=False)
    cfg = sp.get_provider_config("engage_cloud")   # UI alias -> engage2
    assert cfg.key == "engage2"
    assert cfg.name == "engage2"
    assert cfg.api_key == "e2-key"
    assert cfg.from_numbers == "+13055550001,+13055550002"
    assert cfg.webhook_secret == "e2-wh"


def test_engage2_and_sinch_do_not_bleed(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGECLOUD_API_KEY", "sinch-key", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "e2-key", raising=False)
    assert sp.get_provider_config("sinch").api_key == "sinch-key"
    assert sp.get_provider_config("engage2").api_key == "e2-key"


def test_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_API_SECRET", "", raising=False)
    assert sp.is_configured("engage2") is False
    monkeypatch.setattr(settings, "ENGAGE2_API_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "ENGAGE2_API_SECRET", "s", raising=False)
    assert sp.is_configured("engage2") is True
