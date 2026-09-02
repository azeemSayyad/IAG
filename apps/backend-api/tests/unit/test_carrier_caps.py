"""Unit tests for recipient-carrier caps, T-Mobile dedup, and working-hours gating.

Covers the bits that matter most for correctness: Pacific daily buckets that DON'T
drift across PST<->PDT, the Eastern Mon-Fri 10-7 window across EST<->EDT, the
provider total cap, the T-Mobile per-provider cap, the cross-provider dedup, and the
observe-only-vs-enforce behaviour. No real Redis — a tiny in-memory fake is injected.
"""
from datetime import datetime, timezone

import pytest

from app.ai.services import carrier_caps as cc
from app.ai.services import carrier_registry as cr
from app.core.config import settings


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.sets = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = str(v)

    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k) or 0) + 1)
        return int(self.kv[k])

    def expire(self, k, ttl):
        return True

    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(str(v) for v in vals)

    def sismember(self, k, v):
        return str(v) in self.sets.get(k, set())

    def scard(self, k):
        return len(self.sets.get(k, set()))

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)
            self.sets.pop(k, None)


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(cc, "_client", lambda: r)
    from app.ai.services import carrier_lookup as cl
    monkeypatch.setattr(cl, "_client", lambda: r)   # share one fake Redis with the cache
    # default: every enforcement OFF (observe-only)
    monkeypatch.setattr(cc, "caps_enforced", lambda: False)
    monkeypatch.setattr(cc, "dedup_enforced", lambda: False)
    monkeypatch.setattr(cc, "hours_enforced", lambda: False)
    return r


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# ----------------------------------------------------- Pacific daily bucket (DST)
def test_pacific_day_summer_pdt_boundary():
    # PDT = UTC-7. Local midnight 2026-07-15 happens at 07:00 UTC.
    assert cc.pacific_day(utc(2026, 7, 15, 6, 59)) == "20260714"   # 23:59 PDT, prev day
    assert cc.pacific_day(utc(2026, 7, 15, 7, 1)) == "20260715"    # 00:01 PDT, new day


def test_pacific_day_winter_pst_boundary():
    # PST = UTC-8. Local midnight 2026-01-15 happens at 08:00 UTC (one hour later
    # than summer) — proves the reset tracks the offset and doesn't drift.
    assert cc.pacific_day(utc(2026, 1, 15, 7, 59)) == "20260114"   # 23:59 PST, prev day
    assert cc.pacific_day(utc(2026, 1, 15, 8, 1)) == "20260115"    # 00:01 PST, new day


# --------------------------------------------------- working hours (Eastern, DST)
def test_working_hours_summer_edt():
    # EDT = UTC-4. Wed 2026-07-15. 10 AM ET = 14:00 UTC.
    assert cc.within_working_hours(utc(2026, 7, 15, 14, 0)) is True     # 10:00 ET open
    assert cc.within_working_hours(utc(2026, 7, 15, 13, 59)) is False   # 09:59 ET closed
    assert cc.within_working_hours(utc(2026, 7, 15, 22, 59)) is True    # 18:59 ET open
    assert cc.within_working_hours(utc(2026, 7, 15, 23, 0)) is False    # 19:00 ET end-exclusive


def test_working_hours_winter_est():
    # EST = UTC-5. Thu 2026-01-15. 10 AM ET = 15:00 UTC (one hour later than summer).
    assert cc.within_working_hours(utc(2026, 1, 15, 15, 0)) is True     # 10:00 ET open
    assert cc.within_working_hours(utc(2026, 1, 15, 14, 0)) is False    # 09:00 ET closed


def test_working_hours_weekend_closed():
    # Sat 2026-07-18, 14:00 UTC = 10 AM EDT — inside the time window but a weekend.
    assert cc.within_working_hours(utc(2026, 7, 18, 14, 0)) is False
    # Sun 2026-07-19 likewise.
    assert cc.within_working_hours(utc(2026, 7, 19, 14, 0)) is False


# --------------------------------------------------------- carrier classification
def test_tmobile_alias_detection():
    assert cc.is_tmobile("T-Mobile") is True
    assert cc.is_tmobile("tmobile") is True
    assert cc.is_tmobile("Metro by T-Mobile") is True
    assert cc.is_tmobile("AT&T") is False
    assert cc.is_tmobile("") is False


def test_recipient_carrier_hint_and_lookup():
    assert cc.recipient_carrier("+13055551234", hint="T-Mobile") == "tmobile"
    assert cc.recipient_carrier("+13055551234") == ""   # no lookup wired -> unknown
    cc.set_carrier_lookup(lambda n: "T-Mobile")
    try:
        assert cc.recipient_carrier("+13055551234") == "tmobile"
    finally:
        cc.set_carrier_lookup(None)


# ----------------------------------------------------------- a Wednesday in-hours
WED = utc(2026, 7, 15, 16, 0)   # 12:00 ET, inside working hours (so it never adds a reason)


# ------------------------------------------------------------- provider total cap
def test_provider_cap_observe_then_enforce(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 2)
    cc.record_send("sinch", "+1305000001", "AT&T", now=WED)
    cc.record_send("sinch", "+1305000002", "AT&T", now=WED)   # provider now at 2/2

    d = cc.evaluate_send("sinch", "+1305000003", "AT&T", now=WED)
    assert "provider_cap" in d.reasons          # observed
    assert d.blocked_by == []                   # but enforcement is off
    assert d.allowed is True

    monkeypatch.setattr(cc, "caps_enforced", lambda: True)
    d2 = cc.evaluate_send("sinch", "+1305000003", "AT&T", now=WED)
    assert d2.blocked_by == ["provider_cap"]
    assert d2.allowed is False


# ------------------------------------------------- T-Mobile per-provider cap (2k)
def test_tmobile_cap_is_per_provider(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)        # disable provider cap, isolate T-Mobile
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 2)
    monkeypatch.setattr(cc, "caps_enforced", lambda: True)

    cc.record_send("sinch", "+1305000010", "T-Mobile", now=WED)
    cc.record_send("sinch", "+1305000011", "T-Mobile", now=WED)  # sinch T-Mobile at 2/2

    blocked = cc.evaluate_send("sinch", "+1305000012", "T-Mobile", now=WED)
    assert "tmobile_cap" in blocked.reasons and blocked.allowed is False

    # A DIFFERENT provider has its own 2,000 bucket -> not capped.
    other = cc.evaluate_send("telnyx", "+1305000012", "T-Mobile", now=WED)
    assert "tmobile_cap" not in other.reasons and other.allowed is True


# ----------------------------------------------- T-Mobile cross-provider dedup
def test_tmobile_dedup_across_providers(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 0)  # isolate dedup from caps
    monkeypatch.setattr(cc, "dedup_enforced", lambda: True)

    num = "+1 (305) 000-0099"
    cc.record_send("sinch", num, "T-Mobile", now=WED)            # sent once via sinch

    # Same number via a DIFFERENT provider -> flagged duplicate.
    d = cc.evaluate_send("telnyx", num, "T-Mobile", now=WED)
    assert "tmobile_duplicate" in d.reasons
    assert d.blocked_by == ["tmobile_duplicate"] and d.allowed is False

    # A non-T-Mobile recipient is never deduped.
    nd = cc.evaluate_send("telnyx", "+1305000100", "AT&T", now=WED)
    assert "tmobile_duplicate" not in nd.reasons and nd.allowed is True


def test_non_tmobile_only_hits_provider_cap(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)
    # Unknown / non-T-Mobile carrier: no tmobile counters touched, no dedup.
    cc.record_send("sinch", "+1305000200", "AT&T", now=WED)
    day = cc.pacific_day(WED)
    assert cc._get_int(cc._k_provider("sinch", day)) == 1
    assert cc._get_int(cc._k_tmobile("sinch", day)) == 0


# ------------------------------------------------------------- working-hours gate
def test_working_hours_reason_and_enforcement(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)
    off_hours = utc(2026, 7, 15, 2, 0)   # 22:00 ET previous evening -> closed
    d = cc.evaluate_send("sinch", "+1305000300", "AT&T", now=off_hours)
    assert "outside_working_hours" in d.reasons
    assert d.allowed is True             # observe-only

    monkeypatch.setattr(cc, "hours_enforced", lambda: True)
    d2 = cc.evaluate_send("sinch", "+1305000300", "AT&T", now=off_hours)
    assert d2.blocked_by == ["outside_working_hours"] and d2.allowed is False


# ------------------------------------------------------------------ status shape
# ------------------------------ recipient carrier comes from the provider/route
def test_recipient_carrier_via_tmobile_providers(monkeypatch):
    # "We have T-Mobile through Sinch" — tag the provider with a one-line env list.
    monkeypatch.setattr(settings, "CARRIER_POOLS_JSON", "")
    monkeypatch.setattr(settings, "TMOBILE_PROVIDERS", "sinch")
    assert cr.recipient_carrier_of("sinch") == "tmobile"
    assert cr.recipient_carrier_of("telnyx") == ""
    # No hint, no lookup — carrier is inferred from the provider the send used.
    assert cc.recipient_carrier("+13055550000", provider="sinch") == "tmobile"
    assert cc.recipient_carrier("+13055550000", provider="telnyx") == ""


def test_recipient_carrier_via_pool_field(monkeypatch):
    pools = '[{"name":"sinch-tmo","recipient_carrier":"T-Mobile","numbers":["+13050000001"]}]'
    monkeypatch.setattr(settings, "CARRIER_POOLS_JSON", pools)
    monkeypatch.setattr(settings, "TMOBILE_PROVIDERS", "")
    assert cr.recipient_carrier_of("sinch-tmo") == "t-mobile"
    assert cc.recipient_carrier("+13055550000", provider="sinch-tmo") == "tmobile"  # canonicalized


def test_provider_drives_tmobile_cap_without_hint(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)        # isolate the T-Mobile cap
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 2)
    monkeypatch.setattr(cc, "caps_enforced", lambda: True)
    monkeypatch.setattr(cr, "recipient_carrier_of", lambda p: "tmobile" if p == "sinch" else "")

    # No carrier_hint at all — T-Mobile is inferred from provider="sinch".
    assert cc.evaluate_send("sinch", "+1305000500", now=WED).is_tmobile is True
    cc.record_send("sinch", "+1305000501", now=WED)
    cc.record_send("sinch", "+1305000502", now=WED)              # sinch T-Mobile at 2/2
    blocked = cc.evaluate_send("sinch", "+1305000503", now=WED)
    assert "tmobile_cap" in blocked.reasons and blocked.allowed is False
    # A provider that doesn't carry T-Mobile is never classified T-Mobile.
    assert cc.evaluate_send("telnyx", "+1305000503", now=WED).is_tmobile is False


def test_caps_classify_tmobile_via_number_cache(fake, monkeypatch):
    # MIXED channels: no hint, provider not carrier-tagged -> the recipient carrier
    # comes from the per-number cache (populated by enrichment).
    from app.ai.services import carrier_lookup as cl
    monkeypatch.setattr(settings, "TMOBILE_PROVIDERS", "")
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 2)
    monkeypatch.setattr(cc, "caps_enforced", lambda: True)

    cl.put("+1 (305) 555-7777", "T-Mobile")            # this number is enriched as T-Mobile
    d = cc.evaluate_send("sinch", "+13055557777", now=WED)
    assert d.is_tmobile is True and d.carrier == "tmobile"
    # A number with no cache entry is unknown -> not T-Mobile.
    assert cc.evaluate_send("sinch", "+13055558888", now=WED).is_tmobile is False


def test_tmobile_rollover_to_next_provider(fake, monkeypatch):
    # T-Mobile caps PER PROVIDER, then roll to the next — only hold when all are full.
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 2)
    provs = ["sinch", "telnyx", "twilio"]
    assert cc.tmobile_provider_with_room(provs, now=WED) == "sinch"        # all empty -> first

    cc.record_send("sinch", "+1305000001", "T-Mobile", now=WED)
    cc.record_send("sinch", "+1305000002", "T-Mobile", now=WED)           # sinch full (2/2)
    assert cc.tmobile_provider_with_room(provs, now=WED) == "telnyx"       # roll over

    cc.record_send("telnyx", "+1305000003", "T-Mobile", now=WED)
    cc.record_send("telnyx", "+1305000004", "T-Mobile", now=WED)          # telnyx full
    assert cc.tmobile_provider_with_room(provs, now=WED) == "twilio"

    cc.record_send("twilio", "+1305000005", "T-Mobile", now=WED)
    cc.record_send("twilio", "+1305000006", "T-Mobile", now=WED)          # all 3 full
    assert cc.tmobile_provider_with_room(provs, now=WED) is None          # -> hold for later


def test_dispatch_stats_and_carrier_rollup(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 0)
    cc.record_send("sinch", "+1305000001", "T-Mobile", now=WED)
    cc.record_send("sinch", "+1305000002", "T-Mobile", now=WED)
    cc.record_send("sinch", "+1305000003", "AT&T", now=WED)
    cc.record_skip("tmobile_duplicate", now=WED)        # -> skipped_duplicate
    cc.record_skip("provider_cap", now=WED)             # -> held_by_cap

    ds = cc.dispatch_stats(now=WED)
    assert ds["sent"] == 3
    assert ds["recipients_total"] == 3
    assert ds["skipped_duplicate"] == 1
    assert ds["held_by_cap"] == 1

    roll = {r["carrier"]: r["sent_today"] for r in cc.carrier_rollup(now=WED)}
    assert roll.get("tmobile") == 2 and roll.get("at&t") == 1
    assert all(r["limit"] is None for r in cc.carrier_rollup(now=WED))   # rollup only, no cap


def test_caps_status_snapshot(fake, monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER_DAILY_CAP", 4000)
    monkeypatch.setattr(settings, "TMOBILE_PER_PROVIDER_CAP", 2000)
    cc.record_send("sinch", "+1305000400", "T-Mobile", now=WED)
    s = cc.caps_status(["sinch", "telnyx"], now=WED)
    assert s["provider_daily_cap"] == 4000
    assert s["tmobile_per_provider_cap"] == 2000
    assert s["enforcement"]["caps"] == "observe-only"
    rows = {r["provider"]: r for r in s["providers"]}
    assert rows["sinch"]["sent_today"] == 1 and rows["sinch"]["tmobile_today"] == 1
    assert rows["sinch"]["remaining"] == 3999
    assert rows["telnyx"]["sent_today"] == 0
