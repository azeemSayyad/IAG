"""Carrier / DID fleet dashboard — operational usage + a provisioning forecast.

Per carrier it surfaces:
  - today's usage: sent_today / daily_capacity (e.g. 100 / 240, 42%)
  - a rolling daily-send history (kept in Redis ~35 days) so demand can be trended
  - "need new DID in X days": when projected daily demand will cross the provision
    threshold of current capacity, plus how many numbers to add.

Read-only on the send path. It records only a per-day rollup counter and reads the
carrier registry + sender-pool health. It never sends anything. The forecast math is
a pure function (forecast) so it unit-tests without Redis.
"""
import math
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.redis import redis_service

_DKEY = "dash:carrier:{name}:{day}"
_TTL = 35 * 86400   # keep ~35 days of daily totals


def _day(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y%m%d")


def record_daily(carrier: str, sent_today: int) -> None:
    """Store today's running total for a carrier (idempotent — overwrites today)."""
    try:
        redis_service.client.set(_DKEY.format(name=carrier, day=_day()), int(sent_today), ex=_TTL)
    except Exception:
        pass


def history(carrier: str, days: int = None) -> list:
    """Daily send totals for the last N days, oldest..today (missing days skipped)."""
    days = days or int(getattr(settings, "DID_FORECAST_WINDOW_DAYS", 14))
    out = []
    try:
        c = redis_service.client
        for off in range(days - 1, -1, -1):
            v = c.get(_DKEY.format(name=carrier, day=_day(off)))
            if v is not None:
                out.append(int(v))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# PURE forecast — no Redis. history = daily send totals oldest..today.
# ---------------------------------------------------------------------------
def forecast(daily_capacity: int, hist: list, sent_today: int, threshold: float,
             per_number_cap: int, total_numbers: int) -> dict:
    """Project when the carrier needs new DIDs. Linear trend of daily demand vs. the
    provision threshold of capacity. days_until_new_did: 0 = provision now, an int =
    days out, None = not needed at the current (flat/declining) rate."""
    cap = max(0, int(daily_capacity))
    today = max(0, int(sent_today))
    h = [max(0, int(x)) for x in hist] or [today]
    n = len(h)
    growth = (h[-1] - h[0]) / (n - 1) if n >= 2 else 0.0       # avg sends/day change
    projected = today + max(0.0, growth)                       # ~next-day demand
    provision_at = threshold * cap

    if cap <= 0 or today >= cap:
        days, recommend = 0, True
    elif today >= provision_at or projected >= provision_at:
        days, recommend = 0, True
    elif growth > 0:
        days = max(0, math.ceil((provision_at - today) / growth))
        recommend = days <= 7
    else:
        days, recommend = None, False

    # How many numbers to add so today's demand sits under the threshold of capacity.
    suggested = 0
    if per_number_cap > 0 and threshold > 0:
        need = math.ceil(today / (per_number_cap * threshold)) if today else 0
        suggested = max(0, need - int(total_numbers))

    return {
        "projected_daily_demand": round(projected),
        "growth_per_day": round(growth, 1),
        "days_until_new_did": days,
        "recommend_provision": recommend,
        "suggested_new_dids": suggested,
        "history_days_used": n,
    }


def fleet_dashboard() -> dict:
    """Full per-carrier dashboard + a fleet rollup. Records today's totals as a side
    effect so the trend builds up over time."""
    from app.ai.services import sender_pool, carrier_registry as cr
    fs = sender_pool.fleet_status().get("carriers", [])
    cfg = {c["name"]: c for c in cr.load_carriers()}
    threshold = float(getattr(settings, "DID_PROVISION_THRESHOLD", 0.8))
    default_cap = int(getattr(settings, "SENDER_DAILY_CAP", 2000))

    carriers, tot_sent, tot_cap, soonest = [], 0, 0, None
    for c in fs:
        name, sent, cap, nums = c["carrier"], c["sent_today"], c["daily_capacity"], c["numbers"]
        record_daily(name, sent)
        per_cap = int(cfg.get(name, {}).get("daily_cap", default_cap))
        fc = forecast(cap, history(name), sent, threshold, per_cap, nums)
        d = fc["days_until_new_did"]
        status = ("critical" if (d == 0 or (cap and sent >= cap))
                  else "warn" if (d is not None and d <= 7) else "ok")
        carriers.append({
            "carrier": name, "role": c["role"], "status": status,
            "sent_today": sent, "daily_capacity": cap,
            "usage_pct": round(100 * sent / cap, 1) if cap else 0.0,
            "remaining_today": max(0, cap - sent),
            "total_numbers": nums, "healthy_numbers": c["healthy_available"],
            **fc,
        })
        tot_sent += sent
        tot_cap += cap
        if d is not None:
            soonest = d if soonest is None else min(soonest, d)

    # Recipient-carrier caps / T-Mobile dedup / working-hours snapshot (Pacific-day
    # buckets) for the "ENFORCEMENT" + "T-Mobile caps" sections. Best-effort so the
    # rest of the dashboard never breaks if Redis is unavailable.
    try:
        from app.ai.services import carrier_caps
        caps = carrier_caps.caps_status([c["carrier"] for c in fs])
    except Exception:
        caps = None

    return {
        "as_of": _day(),
        "fleet": {
            "sent_today": tot_sent, "daily_capacity": tot_cap,
            "usage_pct": round(100 * tot_sent / tot_cap, 1) if tot_cap else 0.0,
        },
        "soonest_provision_days": soonest,
        "provision_threshold_pct": round(threshold * 100),
        "carriers": carriers,
        "caps": caps,
    }


def did_fleet_view() -> dict:
    """The EXACT shape the DID Fleet page (did-fleet.html) reads: per-dimension
    enforcement, the provider pools (with the provider TOTAL daily cap), the
    per-provider T-Mobile pair caps, the recipient-carrier rollup, the dedup/routing
    tallies, and the Pacific reset. Sourced from the caps engine + the sender pool.
    Read-only (the per-day trend recording lives in fleet_dashboard, called below)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.ai.services import carrier_caps as cc

    base = fleet_dashboard()                       # keeps the per-day trend + forecast
    pools = base.get("carriers", []) or []         # sender pools: names + DID counts
    names = [p.get("carrier") for p in pools]
    numbers_by = {p.get("carrier"): p.get("total_numbers", 0) for p in pools}

    try:
        caps = cc.caps_status(names)
    except Exception:
        caps = {"providers": []}
    by_provider = {r.get("provider"): r for r in caps.get("providers", [])}

    providers, tmo_pairs = [], []
    for nm in names:
        r = by_provider.get(cc.normalize_carrier(nm), {})
        providers.append({
            "provider": nm,
            "sent_today": r.get("sent_today", 0),
            "limit": (r.get("provider_cap") or None),     # None => unlimited (clean "∞" in the UI)
            "numbers": numbers_by.get(nm, 0),
        })
        tmo_pairs.append({
            "provider": nm, "carrier": "T-Mobile",
            "sent_today": r.get("tmobile_today", 0),
            "limit": (r.get("tmobile_cap") or None),
        })

    tz = getattr(settings, "CAP_RESET_TZ", "America/Los_Angeles")
    try:
        generated_at = datetime.now(ZoneInfo(tz)).isoformat()
    except Exception:
        generated_at = None
    return {
        "generated_at": generated_at,
        "reset_tz": tz,
        "reset_time": "00:00",
        "soonest_provision_days": base.get("soonest_provision_days"),
        "enforcement": {
            "provider": cc.caps_enforced(),
            "did": cc.caps_enforced(),
            "tmobile_pair": cc.caps_enforced(),
            "dedup": cc.dedup_enforced(),
            "flag": "CARRIER_CAPS_ENFORCE / TMOBILE_DEDUP_ENFORCE",
        },
        "providers": providers,
        "tmobile_pair_caps": tmo_pairs,
        "carriers": cc.carrier_rollup(),           # recipient-carrier rollup (no carrier cap)
        "dispatch_stats": cc.dispatch_stats(),
        "dids": [],                                # per-DID drill-down: populated in a later step
    }
