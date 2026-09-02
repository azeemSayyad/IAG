"""
Sender number pool (production-grade) for up to 300+ outbound numbers.

Selection strategy:
  1. STICKY per lead — a retry of the SAME lead reuses its number (carrier
     reputation / conversation continuity). Stored in Redis `sender:lead:{lead_id}`.
  2. Otherwise EVEN ROUND-ROBIN across the pool — one atomic per-pool cursor hands
     out sequential slots, so N leads divide evenly over M numbers (N==M -> one
     each; N>M -> ~N/M each; N<M -> distinct, scattered numbers). The pool is
     deterministically shuffled so the rotation scatters instead of marching
     1,2,3,…. Numbers that are unhealthy or over their daily cap are skipped.
  3. Failover — if every number is exhausted/unhealthy, falls back to the
     least-loaded number (degraded) so messaging never hard-stops.

Because the campaign engine sends one campaign at a time, this global per-pool
cursor effectively divides each campaign's leads evenly across the numbers, for
any campaign size (fully dynamic to the CSV upload).

Health: `record_result(number, success)` decays a per-number health score; numbers
below threshold are skipped until they recover (TTL).
"""
import hashlib
import random as _random
import time
from datetime import datetime, timezone
from typing import List, Optional

from app.core.config import settings
from app.core.redis import redis_service

DAILY_CAP_PER_NUMBER = getattr(settings, "SENDER_DAILY_CAP", 2000)   # 10DLC-safe default
HEALTH_MIN = 40          # below this -> skip the number
HEALTH_TTL = 3600        # health window (seconds)
RR_TTL = 90000           # ~25h; round-robin cursor TTL (refreshed while sending)
RATE_TTL = 2             # per-DID one-message-per-second slot key TTL (seconds)
CARRIER_WINDOW = 3600    # rolling window (s) for per-carrier breaker ok/fail counters


def _int(v) -> int:
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _numbers() -> List[str]:
    raw = (settings.ENGAGECLOUD_FROM_NUMBERS or "").replace(";", ",")
    nums = [n.strip() for n in raw.split(",") if n.strip()]
    out = []
    for n in nums:
        digits = "".join(c for c in n if c.isdigit() or c == "+")
        if digits and not digits.startswith("+"):
            digits = "+" + digits
        out.append(digits)
    return out


def _is_reserved(num: str) -> bool:
    """True if `num` belongs to the dedicated hiree pool — those numbers are reserved
    for applicant messaging and must NEVER be handed out for lead outreach."""
    try:
        from app.core.applicant_numbers import is_applicant_number
        return is_applicant_number(num)
    except Exception:
        return False


def _primary_pool() -> List[str]:
    """Primary-carrier numbers across ALL configured carriers (the round-robin
    spreads evenly over them and skips capped/unhealthy ones, so it jumps carrier->
    carrier automatically). Falls back to the legacy flat pool when no carriers are
    configured — identical to today's behaviour."""
    try:
        from app.ai.services import carrier_registry as cr
        nums = cr.primary_numbers()
        return nums if nums else _numbers()
    except Exception:
        return _numbers()


def _reserve_pool() -> List[str]:
    """Reserve / 'safety' carrier numbers — kept OUT of normal rotation and used only
    when the primary fleet is saturated (empty unless reserve carriers configured)."""
    try:
        from app.ai.services import carrier_registry as cr
        return cr.reserve_numbers()
    except Exception:
        return []


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _ns(provider: str = "") -> str:
    """Redis-key namespace for a provider. Sinch (default) uses NO namespace so its
    existing keys are byte-identical; a 2nd provider (e.g. 'engage2') gets its own
    prefix so its counters/health/per-second slots never touch Sinch's."""
    p = (provider or "").strip().lower()
    return "" if p in ("", "sinch") else f"{p}:"


def _count_key(num: str, provider: str = "") -> str:
    return f"sender:count:{_ns(provider)}{_today()}:{num}"


def _health_key(num: str, provider: str = "") -> str:
    return f"sender:health:{_ns(provider)}{num}"


def _count(client, num: str, provider: str = "") -> int:
    try:
        v = client.get(_count_key(num, provider))
        return int(v) if v else 0
    except Exception:
        return 0


def _health(client, num: str, provider: str = "") -> int:
    try:
        v = client.get(_health_key(num, provider))
        return int(v) if v is not None else 100
    except Exception:
        return 100


def _claim_second(client, num: str, provider: str = "") -> bool:
    """Reserve this DID's one-message-per-second slot, atomically. Returns True if
    the DID has NOT sent yet in the current second (slot now reserved), False if it
    already has — so the round-robin walks to the next DID and no number is asked to
    send more than once per second. Fail-open (True) on any Redis hiccup so a
    transient error never stalls sending."""
    try:
        return bool(client.set(f"sender:sec:{_ns(provider)}{num}:{int(time.time())}", "1", nx=True, ex=RATE_TTL))
    except Exception:
        return True


def _ordered(nums: List[str]) -> List[str]:
    """Stable, non-alphabetical order for the round-robin, so the rotation scatters
    across the pool instead of marching 1,2,3,…. Deterministic per pool (the same
    set of numbers always yields the same order), so every send agrees on the
    sequence and the cursor advances evenly."""
    arr = sorted(nums)
    seed = int(hashlib.md5((",".join(arr)).encode()).hexdigest()[:8], 16)
    _random.Random(seed).shuffle(arr)
    return arr


def _rr_key(order: List[str], provider: str = "") -> str:
    """Per-pool round-robin cursor key (one cursor per distinct set of numbers, so
    each state-matched pool rotates independently)."""
    sig = hashlib.md5((",".join(order)).encode()).hexdigest()[:12]
    return f"sender:rr:{_ns(provider)}{sig}"


def select_sender(tenant_id: Optional[str] = None, lead_id: Optional[str] = None,
                  pool: Optional[List[str]] = None, provider: str = "sinch") -> str:
    # A non-Sinch provider (e.g. "engage2") has its OWN flat pool + namespaced Redis
    # state — route it to its own selector so the Sinch path below stays byte-identical.
    from app.ai.services.sms_providers import normalize_provider
    _p = normalize_provider(provider)
    if _p != "sinch":
        return _select_provider_sender(_p, tenant_id, lead_id)
    # When `pool` is given (e.g. the lead's state-matched numbers), divide within
    # it; otherwise use the primary-carrier pool (all primary carriers combined, so
    # the rotation spreads across carriers and jumps to the next when one is capped).
    nums = [n for n in (pool if pool is not None else _primary_pool()) if n and not _is_reserved(n)]
    if not nums:
        raise RuntimeError("ENGAGECLOUD_FROM_NUMBERS is required for outbound messaging")
    if len(nums) == 1:
        return nums[0]

    client = redis_service.client

    # 1) sticky per lead — a retry of the same lead reuses its number.
    if lead_id:
        try:
            sticky = client.get(f"sender:lead:{lead_id}")
            if isinstance(sticky, bytes):
                sticky = sticky.decode()
            if (sticky and sticky in nums and _count(client, sticky) < DAILY_CAP_PER_NUMBER
                    and _health(client, sticky) >= HEALTH_MIN and _claim_second(client, sticky)):
                _bump(client, sticky, lead_id)
                return sticky
        except Exception:
            pass

    # 2) EVEN ROUND-ROBIN + 1-PER-DID-PER-SECOND CEILING — one atomic per-pool cursor
    #    hands out the next slot so leads divide evenly across the numbers; we then
    #    skip any number that is over its daily cap, unhealthy, OR has already sent in
    #    the CURRENT second, so no DID is asked to send more than 1 message/second.
    order = _ordered(nums)
    M = len(order)
    # CD: skip numbers whose CARRIER is circuit-broken, so a degrading carrier sheds
    # ALL its traffic to the others. Only build the map when something is tripped
    # (the common case is nothing tripped -> zero extra work).
    tripped = _tripped_carriers()
    caps = _carrier_caps()
    cmap = {}
    if tripped or caps:
        from app.ai.services import carrier_registry as cr
        cmap = cr.carrier_of_map(cr.load_carriers())
    try:
        idx = int(client.incr(_rr_key(order))) - 1
        client.expire(_rr_key(order), RR_TTL)
    except Exception:
        idx = 0
    chosen = None
    for k in range(M):
        cand = order[(idx + k) % M]
        cc = cmap.get(cand)
        if tripped and cc in tripped:
            continue   # carrier circuit-broken -> overflow to another carrier
        if caps.get(cc, 0) and _carrier_sec_count(client, cc) >= caps[cc]:
            continue   # carrier at its per-second ceiling -> overflow to another carrier
        if (_count(client, cand) < DAILY_CAP_PER_NUMBER and _health(client, cand) >= HEALTH_MIN
                and _claim_second(client, cand)):
            chosen = cand
            break
    if chosen is None:
        # 3) RESERVE overflow (CE): the whole primary pool is capped/unhealthy/just-
        #    sent. Pull in the 'safety' carrier numbers — kept out of normal rotation
        #    and used only now, so no primary carrier is pushed past its safe limit.
        reserve = [n for n in _reserve_pool() if n not in set(nums) and not _is_reserved(n)]
        ro = _ordered(reserve)
        for k in range(len(ro)):
            cand = ro[(idx + k) % len(ro)]
            if (_count(client, cand) < DAILY_CAP_PER_NUMBER and _health(client, cand) >= HEALTH_MIN
                    and _claim_second(client, cand)):
                chosen = cand
                break
    if chosen is None:
        # 4) final failover: least-loaded across the pool (degraded; keeps messaging
        #    flowing under extreme burst — the per-second ceiling is best-effort).
        chosen = min(nums, key=lambda n: _count(client, n))

    _bump(client, chosen, lead_id)
    return chosen


def _bump(client, num: str, lead_id: Optional[str], provider: str = ""):
    try:
        pipe = client.pipeline()
        pipe.incr(_count_key(num, provider))
        pipe.expire(_count_key(num, provider), 90000)  # ~25h
        if lead_id:
            pipe.set(f"sender:lead:{_ns(provider)}{lead_id}", num, ex=60 * 60 * 24 * 7)  # 7-day stickiness
        pipe.execute()
    except Exception:
        pass
    # Carrier per-second throttle counter (Sinch only — the carrier registry / per-
    # carrier caps are a Sinch-side concept; the 2nd provider uses a flat pool).
    if _ns(provider):
        return
    try:
        caps = _carrier_caps()
        if caps:
            from app.ai.services import carrier_registry as cr
            carrier = cr.carrier_of(num)
            if caps.get(carrier, 0):
                key = f"carrier:rate:{carrier}:{int(time.time())}"
                client.incr(key)
                client.expire(key, 2)
    except Exception:
        pass


def _provider_numbers(provider: str) -> List[str]:
    """A non-Sinch provider's flat DID pool, from its configured from_numbers
    (E.164-normalized, hiree numbers excluded)."""
    from app.ai.services.sms_providers import get_provider_config
    raw = (get_provider_config(provider).from_numbers or "").replace(";", ",")
    out: List[str] = []
    for n in raw.split(","):
        n = n.strip()
        if not n:
            continue
        digits = "".join(c for c in n if c.isdigit() or c == "+")
        if digits and not digits.startswith("+"):
            digits = "+" + digits
        if digits and not _is_reserved(digits):
            out.append(digits)
    return out


def _select_provider_sender(provider: str, tenant_id: Optional[str] = None,
                            lead_id: Optional[str] = None) -> str:
    """Sticky + even round-robin + 1-msg/DID/sec + daily cap + health over a 2nd
    provider's OWN pool, with provider-namespaced Redis state. Mirrors the Sinch core
    selection; no carrier breaker/reserve (those are Sinch-only)."""
    nums = _provider_numbers(provider)
    if not nums:
        raise RuntimeError(f"{provider} has no configured sender numbers")
    if len(nums) == 1:
        return nums[0]
    client = redis_service.client
    # 1) sticky per lead
    if lead_id:
        try:
            sticky = client.get(f"sender:lead:{_ns(provider)}{lead_id}")
            if isinstance(sticky, bytes):
                sticky = sticky.decode()
            if (sticky and sticky in nums and _count(client, sticky, provider) < DAILY_CAP_PER_NUMBER
                    and _health(client, sticky, provider) >= HEALTH_MIN
                    and _claim_second(client, sticky, provider)):
                _bump(client, sticky, lead_id, provider)
                return sticky
        except Exception:
            pass
    # 2) even round-robin + 1-per-DID-per-second + daily cap + health
    order = _ordered(nums)
    M = len(order)
    try:
        idx = int(client.incr(_rr_key(order, provider))) - 1
        client.expire(_rr_key(order, provider), RR_TTL)
    except Exception:
        idx = 0
    for k in range(M):
        cand = order[(idx + k) % M]
        if (_count(client, cand, provider) < DAILY_CAP_PER_NUMBER
                and _health(client, cand, provider) >= HEALTH_MIN
                and _claim_second(client, cand, provider)):
            _bump(client, cand, lead_id, provider)
            return cand
    # 3) failover: least-loaded (degraded; keeps messaging flowing)
    chosen = min(nums, key=lambda n: _count(client, n, provider))
    _bump(client, chosen, lead_id, provider)
    return chosen


def record_result(num: str, success: bool, provider: str = ""):
    """Adjust per-number health AND per-carrier breaker counters from a send outcome.
    Called from the send path (send_sms_to_lead) on every first-template send.
    `provider` namespaces the health key so a 2nd provider's health is independent."""
    if not num:
        return
    try:
        client = redis_service.client
        h = _health(client, num, provider)
        h = min(100, h + 2) if success else max(0, h - 20)
        client.set(_health_key(num, provider), h, ex=HEALTH_TTL)
    except Exception:
        pass
    # Per-carrier circuit-breaker counters (CD): rolling-window ok/fail per carrier.
    # Sinch only — the carrier registry is a Sinch-side concept.
    if _ns(provider):
        return
    try:
        from app.ai.services import carrier_registry as cr
        client = redis_service.client
        key = f"carrier:{'ok' if success else 'fail'}:{cr.carrier_of(num)}"
        client.incr(key)
        client.expire(key, CARRIER_WINDOW)
    except Exception:
        pass


def carrier_tripped(carrier: str) -> bool:
    """A carrier is tripped (skip ALL its numbers, overflow elsewhere) when its
    failure rate over the window exceeds CARRIER_BREAKER_FAIL_RATE — but only once it
    has CARRIER_BREAKER_MIN_SAMPLE sends, so a cold carrier isn't tripped on noise.
    Auto-recovers when the windowed counters expire."""
    try:
        client = redis_service.client
        ok = _int(client.get(f"carrier:ok:{carrier}"))
        fail = _int(client.get(f"carrier:fail:{carrier}"))
        total = ok + fail
        if total < int(getattr(settings, "CARRIER_BREAKER_MIN_SAMPLE", 20)):
            return False
        return (fail / total) >= float(getattr(settings, "CARRIER_BREAKER_FAIL_RATE", 0.5))
    except Exception:
        return False


def _tripped_carriers() -> set:
    try:
        from app.ai.services import carrier_registry as cr
        return {c["name"] for c in cr.load_carriers() if carrier_tripped(c["name"])}
    except Exception:
        return set()


def _carrier_caps() -> dict:
    """{carrier: max_per_sec} for carriers that declare a per-second ceiling (>0).
    Empty -> no carrier-level throttle anywhere (so selection does zero extra work)."""
    try:
        from app.ai.services import carrier_registry as cr
        return {c["name"]: int(c.get("max_per_sec", 0)) for c in cr.load_carriers()
                if int(c.get("max_per_sec", 0)) > 0}
    except Exception:
        return {}


def _carrier_sec_count(client, carrier: str) -> int:
    """How many sends this carrier has already taken in the CURRENT second."""
    try:
        return _int(client.get(f"carrier:rate:{carrier}:{int(time.time())}"))
    except Exception:
        return 0


def pool_stats() -> dict:
    client = redis_service.client
    nums = _numbers()
    return {
        "count": len(nums),
        "daily_cap_per_number": DAILY_CAP_PER_NUMBER,
        "daily_capacity": len(nums) * DAILY_CAP_PER_NUMBER,
        "per_number": {n: {"sent_today": _count(client, n), "health": _health(client, n)} for n in nums[:50]},
    }


def fleet_status() -> dict:
    """Per-carrier fleet health + usage (CD circuit-breaker visibility + CF surface).

    Read-only aggregation. A carrier whose numbers are mostly over-cap or unhealthy
    is already 'pulled' by the per-number skip in select_sender (so traffic overflows
    to the next carrier / reserve); this just surfaces that state per carrier so it
    can be watched and alerted on. Status: ok / degraded / exhausted."""
    from app.ai.services import carrier_registry as cr
    client = redis_service.client
    out = []
    for c in cr.load_carriers():
        nums = c["numbers"]
        cap = int(c.get("daily_cap", DAILY_CAP_PER_NUMBER))
        sent = sum(_count(client, n) for n in nums)
        healthy = sum(1 for n in nums if _health(client, n) >= HEALTH_MIN and _count(client, n) < cap)
        frac = (healthy / len(nums)) if nums else 0.0
        out.append({
            "carrier": c["name"], "role": c["role"], "numbers": len(nums),
            "sent_today": sent, "daily_capacity": len(nums) * cap,
            "healthy_available": healthy, "available_fraction": round(frac, 3),
            "status": "ok" if frac > 0.2 else ("degraded" if frac > 0 else "exhausted"),
        })
    return {"carriers": out}
