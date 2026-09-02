"""Unit tests for the UI-toggleable engine flags (Redis override vs env default)."""
from types import SimpleNamespace

import pytest

from app.core import engine_flags as ef


class FakeRedis:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)


def _redis(monkeypatch, store):
    import app.core.redis as rmod
    monkeypatch.setattr(rmod.redis_service, "client", FakeRedis(store))


@pytest.mark.unit
def test_no_override_uses_env(monkeypatch):
    monkeypatch.setattr(ef, "settings", SimpleNamespace(CAPACITY_PACING_ENABLED=True, FATIGUE_ENABLED=False))
    _redis(monkeypatch, {})
    assert ef.engine_enabled("CAPACITY_PACING_ENABLED") is True
    assert ef.engine_enabled("FATIGUE_ENABLED") is False
    assert ef.flag_source("CAPACITY_PACING_ENABLED") == "env"


@pytest.mark.unit
def test_override_on_beats_env_off(monkeypatch):
    monkeypatch.setattr(ef, "settings", SimpleNamespace(CAPACITY_PACING_ENABLED=False))
    _redis(monkeypatch, {"engine:flag:CAPACITY_PACING_ENABLED": "1"})
    assert ef.engine_enabled("CAPACITY_PACING_ENABLED") is True
    assert ef.flag_source("CAPACITY_PACING_ENABLED") == "override"


@pytest.mark.unit
def test_override_off_beats_env_on(monkeypatch):
    monkeypatch.setattr(ef, "settings", SimpleNamespace(CAPACITY_PACING_ENABLED=True))
    _redis(monkeypatch, {"engine:flag:CAPACITY_PACING_ENABLED": "0"})
    assert ef.engine_enabled("CAPACITY_PACING_ENABLED") is False


@pytest.mark.unit
def test_set_then_clear_reverts_to_env(monkeypatch):
    monkeypatch.setattr(ef, "settings", SimpleNamespace(CAPACITY_PACING_ENABLED=False))
    _redis(monkeypatch, {})
    ef.set_engine_override("CAPACITY_PACING_ENABLED", True)
    assert ef.engine_enabled("CAPACITY_PACING_ENABLED") is True
    ef.set_engine_override("CAPACITY_PACING_ENABLED", None)          # clear -> env default
    assert ef.engine_enabled("CAPACITY_PACING_ENABLED") is False
    assert ef.flag_source("CAPACITY_PACING_ENABLED") == "env"


@pytest.mark.unit
def test_unknown_flag_rejected(monkeypatch):
    _redis(monkeypatch, {})
    with pytest.raises(ValueError):
        ef.set_engine_override("FIRST_TEMPLATE_ONLY", True)         # lockdown is never toggleable
