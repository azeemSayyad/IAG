"""Unit tests for per-lead outreach fatigue (frequency cap + cooldown)."""
from types import SimpleNamespace

import pytest

from app.core import fatigue


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None, nx=False):
        self.store[k] = v
        return True

    def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    def expire(self, k, t):
        return True

    def pipeline(self):
        return self

    def execute(self):
        return None


def _on(monkeypatch, fake, cap=4):
    monkeypatch.setattr(fatigue.engine_flags, "engine_enabled", lambda name: True)
    monkeypatch.setattr(fatigue, "settings",
                        SimpleNamespace(FATIGUE_FREQ_CAP=cap, FATIGUE_COOLDOWN_HOURS=72))
    monkeypatch.setattr(fatigue.redis_service, "client", fake)


@pytest.mark.unit
def test_off_always_ok(monkeypatch):
    monkeypatch.setattr(fatigue.engine_flags, "engine_enabled", lambda name: False)
    assert fatigue.fatigue_ok("+13051112222") is True


@pytest.mark.unit
def test_blocks_when_in_cooldown(monkeypatch):
    fake = FakeRedis()
    fake.store["fatigue:cool:3051112222"] = "1"
    _on(monkeypatch, fake)
    assert fatigue.fatigue_ok("+1 (305) 111-2222") is False   # normalized to last 10 digits


@pytest.mark.unit
def test_blocks_over_cap(monkeypatch):
    fake = FakeRedis()
    fake.store["fatigue:count:3051112222"] = 4
    _on(monkeypatch, fake)
    assert fatigue.fatigue_ok("3051112222") is False


@pytest.mark.unit
def test_ok_under_cap(monkeypatch):
    fake = FakeRedis()
    fake.store["fatigue:count:3051112222"] = 2
    _on(monkeypatch, fake)
    assert fatigue.fatigue_ok("3051112222") is True


@pytest.mark.unit
def test_record_bumps_count_and_starts_cooldown(monkeypatch):
    fake = FakeRedis()
    _on(monkeypatch, fake)
    fatigue.fatigue_record("+13051112222")
    assert fake.store["fatigue:count:3051112222"] == 1
    assert fake.store["fatigue:cool:3051112222"] == "1"
    # after recording, the same phone is now in cooldown -> blocked
    assert fatigue.fatigue_ok("3051112222") is False
