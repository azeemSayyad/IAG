"""Tests for the cached per-number recipient-carrier lookup."""
import pytest

from app.ai.services import carrier_lookup as cl
from app.core.config import settings


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = str(v)

    def expire(self, k, ttl):
        return True


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(cl, "_client", lambda: r)
    return r


def test_put_get_roundtrip_is_format_insensitive(fake):
    cl.put("+1 (305) 555-1234", "T-Mobile")
    assert cl.get("3055551234") == "t-mobile"          # normalized, digit-keyed
    assert cl.get("+1-305-555-1234") == "t-mobile"     # same number, any format
    assert cl.get("+13055559999") == ""                # cache miss -> unknown


def test_get_is_cache_only_never_calls_backend(fake):
    calls = {"n": 0}

    def backend(_n):
        calls["n"] += 1
        return "AT&T"

    cl.set_backend(backend)
    try:
        assert cl.get("+13055550000") == ""    # the hot-path read never hits the backend
        assert calls["n"] == 0
    finally:
        cl.set_backend(None)


def test_enrich_uses_backend_then_caches(fake):
    cl.set_backend(lambda _n: "Verizon")
    try:
        assert cl.enrich("+13055551111") == "verizon"
        assert cl.get("+13055551111") == "verizon"     # cached now

        # A subsequent enrich is served from cache — backend must not be called again.
        def boom(_n):
            raise AssertionError("backend should not be called on a cache hit")

        cl.set_backend(boom)
        assert cl.enrich("+13055551111") == "verizon"
    finally:
        cl.set_backend(None)


def test_enrich_without_backend_is_empty(fake):
    cl.set_backend(None)
    assert cl.enrich("+13055552222") == ""


def test_put_bulk_and_enrich_bulk(fake):
    assert cl.put_bulk({"+13055550001": "T-Mobile", "+13055550002": "AT&T"}) == 2
    assert cl.get("+13055550001") == "t-mobile"
    assert cl.get("+13055550002") == "at&t"

    cl.set_backend(lambda _n: "T-Mobile")
    try:
        out = cl.enrich_bulk(["+13055550003", "+13055550004"])
        assert out == {"+13055550003": "t-mobile", "+13055550004": "t-mobile"}
        assert cl.get("+13055550003") == "t-mobile"
    finally:
        cl.set_backend(None)


# ------------------------- the generic, config-driven HTTP lookup connector
def test_http_backend_extracts_carrier_from_configured_service(monkeypatch):
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_URL", "https://look.up/{number}")
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_AUTH", "Bearer X")
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_FIELD", "data.carrier.name")
    captured = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"data": {"carrier": {"name": "T-Mobile USA"}}}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return Resp()

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)

    assert cl.http_backend("+1 (305) 555-1234") == "t-mobile usa"
    assert captured["url"] == "https://look.up/3055551234"   # {number} -> digits, leading 1 dropped
    assert captured["headers"] == {"Authorization": "Bearer X"}


def test_http_backend_is_off_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_URL", "")
    assert cl.http_backend("+13055551234") == ""


def test_http_backend_post_with_custom_headers_and_body(monkeypatch):
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_URL", "https://look.up/lookup")
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_METHOD", "POST")
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_AUTH", "")
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_HEADERS", '{"X-API-Key": "abc123"}')
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_BODY", '{"msisdn": "{number}"}')
    monkeypatch.setattr(settings, "CARRIER_LOOKUP_FIELD", "carrier")
    captured = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"carrier": "AT&T Mobility"}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    assert cl.http_backend("+1-305-555-1234") == "at&t mobility"
    assert captured["headers"] == {"X-API-Key": "abc123"}     # custom header used (no Authorization)
    assert captured["json"] == {"msisdn": "3055551234"}        # body template filled, leading 1 dropped
