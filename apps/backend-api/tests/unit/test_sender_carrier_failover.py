"""Unit tests for carrier-aware sender selection: reserve/safety overflow when the
primary fleet is saturated (CC + CE). Uses a tiny fake Redis so no server is needed."""
from types import SimpleNamespace

import pytest

from app.ai.services import sender_pool


class _NoOpPipe:
    def incr(self, *a, **k):
        return self

    def expire(self, *a, **k):
        return self

    def set(self, *a, **k):
        return self

    def execute(self):
        return None


class FakeRedis:
    """Minimal Redis stand-in. `counts` maps number -> sent_today (to force over-cap)."""

    def __init__(self, counts=None, carrier_rate=0):
        self.counts = counts or {}
        self.carrier_rate = carrier_rate
        self._rr = 0
        self._slots = {}

    def get(self, key):
        if key.startswith("sender:count:"):
            return str(self.counts.get(key.split(":")[-1], 0))
        if key.startswith("sender:health:"):
            return "100"
        if key.startswith("carrier:rate:"):
            return str(self.carrier_rate)
        return None

    def set(self, key, value, nx=False, ex=None):
        if nx:
            if key in self._slots:
                return False
            self._slots[key] = value
            return True
        self._slots[key] = value
        return True

    def incr(self, key):
        self._rr += 1
        return self._rr

    def expire(self, key, ttl):
        return True

    def pipeline(self):
        return _NoOpPipe()


@pytest.mark.unit
def test_reserve_overflow_when_primary_saturated(monkeypatch):
    # both primary numbers are way over the daily cap -> must jump to the reserve.
    fake = FakeRedis(counts={"+1PRIM1": 999999, "+1PRIM2": 999999})
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: ["+1RES1"])
    chosen = sender_pool.select_sender(pool=["+1PRIM1", "+1PRIM2"])
    assert chosen == "+1RES1"


@pytest.mark.unit
def test_primary_used_and_reserve_untouched_when_healthy(monkeypatch):
    fake = FakeRedis(counts={})  # all under cap, healthy
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: ["+1RES1"])
    chosen = sender_pool.select_sender(pool=["+1PRIM1", "+1PRIM2"])
    assert chosen in ("+1PRIM1", "+1PRIM2")   # stayed on primary; safety pool not used


@pytest.mark.unit
def test_no_reserve_configured_falls_back_to_least_loaded(monkeypatch):
    fake = FakeRedis(counts={"+1PRIM1": 999999, "+1PRIM2": 999999})
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: [])   # no safety numbers
    chosen = sender_pool.select_sender(pool=["+1PRIM1", "+1PRIM2"])
    assert chosen in ("+1PRIM1", "+1PRIM2")   # degraded least-loaded, never hard-stops


@pytest.mark.unit
def test_fleet_status_reports_per_carrier(monkeypatch):
    from app.ai.services import carrier_registry as cr
    monkeypatch.setattr(cr, "load_carriers", lambda: [
        {"name": "sinch", "role": "primary", "daily_cap": 2000, "mps": 1, "numbers": ["+1A", "+1B"]},
        {"name": "safety", "role": "reserve", "daily_cap": 2000, "mps": 1, "numbers": ["+1R"]},
    ])
    fake = FakeRedis(counts={"+1A": 999999, "+1B": 999999})   # sinch exhausted, safety fresh
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    by = {c["carrier"]: c for c in sender_pool.fleet_status()["carriers"]}
    assert by["sinch"]["status"] == "exhausted" and by["sinch"]["sent_today"] == 999999 * 2
    assert by["safety"]["status"] == "ok" and by["safety"]["role"] == "reserve"


class _CounterRedis:
    """get() returns ok/fail counts based on the key (for carrier_tripped)."""
    def __init__(self, ok, fail):
        self.ok, self.fail = ok, fail

    def get(self, k):
        return str(self.ok) if ":ok:" in k else str(self.fail)


@pytest.mark.unit
def test_carrier_tripped_threshold_and_min_sample(monkeypatch):
    monkeypatch.setattr(sender_pool, "settings",
                        SimpleNamespace(CARRIER_BREAKER_MIN_SAMPLE=20, CARRIER_BREAKER_FAIL_RATE=0.5))
    # below min sample -> never tripped (no noise trips)
    monkeypatch.setattr(sender_pool.redis_service, "client", _CounterRedis(5, 5))
    assert sender_pool.carrier_tripped("x") is False
    # enough sample + failure rate over threshold -> tripped
    monkeypatch.setattr(sender_pool.redis_service, "client", _CounterRedis(5, 20))
    assert sender_pool.carrier_tripped("x") is True
    # enough sample + low failure rate -> healthy
    monkeypatch.setattr(sender_pool.redis_service, "client", _CounterRedis(20, 2))
    assert sender_pool.carrier_tripped("x") is False


@pytest.mark.unit
def test_select_skips_tripped_carrier(monkeypatch):
    from app.ai.services import carrier_registry as cr
    fake = FakeRedis(counts={})                       # all numbers healthy + under cap
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_tripped_carriers", lambda: {"sinch"})
    monkeypatch.setattr(cr, "load_carriers", lambda: [])
    monkeypatch.setattr(cr, "carrier_of_map", lambda carriers: {"+1A": "sinch", "+1B": "carrierB"})
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: [])
    chosen = sender_pool.select_sender(pool=["+1A", "+1B"])
    assert chosen == "+1B"                            # +1A's carrier 'sinch' is tripped -> skipped


@pytest.mark.unit
def test_carrier_throttle_skips_carrier_at_per_second_ceiling(monkeypatch):
    from app.ai.services import carrier_registry as cr
    fake = FakeRedis(counts={}, carrier_rate=1)       # sinch already sent 1 this second
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_tripped_carriers", lambda: set())
    monkeypatch.setattr(sender_pool, "_carrier_caps", lambda: {"sinch": 1})   # sinch max 1/sec
    monkeypatch.setattr(cr, "load_carriers", lambda: [])
    monkeypatch.setattr(cr, "carrier_of_map", lambda c: {"+1A": "sinch", "+1B": "carrierB"})
    monkeypatch.setattr(cr, "carrier_of", lambda n: {"+1A": "sinch", "+1B": "carrierB"}.get(n, "sinch"))
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: [])
    chosen = sender_pool.select_sender(pool=["+1A", "+1B"])
    assert chosen == "+1B"                            # sinch at its per-second ceiling -> overflow


@pytest.mark.unit
def test_carrier_throttle_allows_when_under_ceiling(monkeypatch):
    from app.ai.services import carrier_registry as cr
    fake = FakeRedis(counts={}, carrier_rate=0)       # sinch sent 0 this second
    monkeypatch.setattr(sender_pool.redis_service, "client", fake)
    monkeypatch.setattr(sender_pool, "_tripped_carriers", lambda: set())
    monkeypatch.setattr(sender_pool, "_carrier_caps", lambda: {"sinch": 5})
    monkeypatch.setattr(cr, "load_carriers", lambda: [])
    monkeypatch.setattr(cr, "carrier_of_map", lambda c: {"+1A": "sinch", "+1B": "sinch"})
    monkeypatch.setattr(cr, "carrier_of", lambda n: "sinch")
    monkeypatch.setattr(sender_pool, "_reserve_pool", lambda: [])
    chosen = sender_pool.select_sender(pool=["+1A", "+1B"])
    assert chosen in ("+1A", "+1B")                   # under ceiling -> carrier used normally
