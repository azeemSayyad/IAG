"""Recipient-carrier caps, T-Mobile cross-provider dedup, and working-hours gating.

Daily cap counters bucket on PACIFIC local time: every key is suffixed with the
local America/Los_Angeles date, so the boundary auto-tracks PST<->PDT and resets at
midnight Pacific with no one-hour drift. Working hours are a separate Eastern
(America/New_York) Mon-Fri 10:00-19:00 window — also DST-safe.

Terminology: a "provider" here is a SEND-SIDE sender pool (a carrier_registry pool,
e.g. "sinch"), the thing the DID Fleet page calls a provider. The "recipient carrier"
(T-Mobile / AT&T / ...) is the DESTINATION number's carrier. T-Mobile is the only
recipient carrier with its own per-provider cap (2,000/provider/day) plus a
cross-provider no-double-send dedup; every send also counts toward the provider total
(4,000/provider/day). Other recipient carriers roll up to the provider total only.

OBSERVE-ONLY by design. evaluate_send() reports allow/deny + machine reasons, but a
deny only becomes a real block when the matching enforcement flag is on
(CARRIER_CAPS_ENFORCE / TMOBILE_DEDUP_ENFORCE / WORKING_HOURS_ENFORCE — all default
OFF). record_send() always counts a real send (so the dashboard reflects reality)
regardless of mode. This module is STANDALONE: it does not import or touch the send
chokepoint or the first-template lockdown. Wire it in at the documented hook (see
README at bottom) when the backend is connected to the page.

Recipient-carrier detection is a seam: there is no carrier lookup in this codebase
yet. recipient_carrier() uses an explicit hint, else a pluggable lookup
(set_carrier_lookup), else "" (unknown). Until a real lookup is wired, no number is
classified T-Mobile, so the T-Mobile cap/dedup observe nothing — that's intentional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.engine_flags import engine_enabled


# --------------------------------------------------------------------------- time
def _in_tz(now: Optional[datetime], tz_name: str) -> datetime:
    """now (or wall-clock) expressed in tz_name. A naive `now` is treated as UTC so
    tests can pass plain UTC datetimes; an aware one is converted faithfully."""
    tz = ZoneInfo(tz_name)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(tz)


def pacific_day(now: Optional[datetime] = None) -> str:
    """YYYYMMDD in Pacific local time — the daily cap bucket. Rolls at midnight
    America/Los_Angeles (whichever of PST/PDT is in effect)."""
    return _in_tz(now, settings.CAP_RESET_TZ).strftime("%Y%m%d")


def _seconds_until_pacific_midnight(now: Optional[datetime] = None) -> int:
    dt = _in_tz(now, settings.CAP_RESET_TZ)
    nxt = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((nxt - dt).total_seconds()))


def within_working_hours(now: Optional[datetime] = None) -> bool:
    """True iff `now` falls inside the Eastern Mon-Fri working window (DST-safe).
    Start inclusive, end exclusive: 10:00 is open, 19:00 (7 PM) is closed."""
    dt = _in_tz(now, settings.WORKING_HOURS_TZ)
    days = {int(x) for x in str(settings.WORKING_DAYS).split(",") if str(x).strip() != ""}
    if dt.weekday() not in days:
        return False
    return int(settings.WORKING_HOURS_START) <= dt.hour < int(settings.WORKING_HOURS_END)


# ---------------------------------------------------------------- recipient carrier
def _digits(num: str) -> str:
    # Canonical US key: digits only, leading '1' country code dropped, so the same
    # number in any format dedups/keys identically.
    d = "".join(ch for ch in str(num or "") if ch.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def normalize_carrier(name: Optional[str]) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _tmobile_aliases() -> set:
    return {normalize_carrier(x) for x in str(settings.TMOBILE_CARRIER_NAMES).split(",") if x.strip()}


# Pluggable carrier lookup — wire a real HLR / provider lookup here later. Takes a
# destination number, returns a carrier name ("" when unknown).
_LOOKUP: Optional[Callable[[str], str]] = None


def set_carrier_lookup(fn: Optional[Callable[[str], str]]) -> None:
    """Install (or clear with None) the recipient-carrier lookup used when no hint is
    passed. The send-path integration can also just pass a carrier_hint instead."""
    global _LOOKUP
    _LOOKUP = fn


def recipient_carrier(number: str, hint: Optional[str] = None, provider: Optional[str] = None) -> str:
    """Normalized recipient carrier for a destination number. Source order:
      1. an explicit hint (a carrier already known on the lead / a provider reply);
      2. the PROVIDER/route's configured carrier, for the rare carrier-SPECIFIC route
         (carrier_registry.recipient_carrier_of) — normally "" since channels are mixed;
      3. the cached PER-NUMBER lookup (carrier_lookup.get) — the main path for mixed
         channels; populated by enrichment, read cache-only here so the send path is
         fast. An override installed via set_carrier_lookup() takes its place;
      4. "" (unknown).
    Every T-Mobile alias canonicalizes to a single "tmobile" label."""
    raw = hint or ""
    if not raw and provider:
        try:
            from app.ai.services import carrier_registry
            raw = carrier_registry.recipient_carrier_of(provider) or ""
        except Exception:
            raw = ""
    if not raw:
        fn = _LOOKUP
        if fn is None:
            try:
                from app.ai.services import carrier_lookup
                fn = carrier_lookup.get   # cached per-number carrier (mixed channels)
            except Exception:
                fn = None
        if fn is not None:
            try:
                raw = fn(number) or ""
            except Exception:
                raw = ""
    norm = normalize_carrier(raw)
    return "tmobile" if norm in _tmobile_aliases() else norm


def is_tmobile(carrier: Optional[str]) -> bool:
    return normalize_carrier(carrier) in _tmobile_aliases()


# ------------------------------------------------------------------------- storage
def _k_provider(provider: str, day: str) -> str:
    return f"caps:provider:{normalize_carrier(provider)}:{day}"


def _k_tmobile(provider: str, day: str) -> str:
    return f"caps:tmobile:{normalize_carrier(provider)}:{day}"


def _k_dedup(day: str) -> str:
    return f"caps:tmo:sent:{day}"   # Redis SET of recipient digit-strings (cross-provider)


def _k_carrier(carrier: str, day: str) -> str:
    return f"caps:carrier:{normalize_carrier(carrier)}:{day}"   # per-recipient-carrier total


def _k_carriers_seen(day: str) -> str:
    return f"caps:carriers:{day}"   # SET of recipient-carrier names seen today


def _k_recipients(day: str) -> str:
    return f"caps:recipients:{day}"   # SET of recipient digit-strings (distinct count)


def _k_sent(day: str) -> str:
    return f"caps:sent:{day}"        # total sends today


def _k_skip(reason: str, day: str) -> str:
    # reason -> "dup" (T-Mobile duplicate) or "held" (any cap/working-hours hold)
    return f"caps:skip:{'dup' if reason == 'tmobile_duplicate' else 'held'}:{day}"


def _client():
    from app.core.redis import redis_service
    return redis_service.client


def _get_int(key: str) -> int:
    try:
        v = _client().get(key)
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _dedup_contains(number: str, day: str) -> bool:
    try:
        return bool(_client().sismember(_k_dedup(day), _digits(number)))
    except Exception:
        return False


# --------------------------------------------------------------- enforcement flags
def caps_enforced() -> bool:
    return engine_enabled("CARRIER_CAPS_ENFORCE")


def dedup_enforced() -> bool:
    return engine_enabled("TMOBILE_DEDUP_ENFORCE")


def hours_enforced() -> bool:
    return engine_enabled("WORKING_HOURS_ENFORCE")


# ----------------------------------------------------------------------- decisions
@dataclass
class CapDecision:
    allowed: bool                                  # would the send go through right now?
    carrier: str                                   # resolved recipient carrier ("" = unknown)
    provider: str                                  # normalized sender-pool / provider
    is_tmobile: bool
    reasons: List[str] = field(default_factory=list)     # ALL rules that would block (observe)
    blocked_by: List[str] = field(default_factory=list)  # subset whose enforcement is ON
    detail: dict = field(default_factory=dict)


def evaluate_send(
    provider: str,
    to_number: str,
    carrier_hint: Optional[str] = None,
    now: Optional[datetime] = None,
) -> CapDecision:
    """Read-only check of the caps / dedup / working-hours rules for one send.
    `reasons` lists every rule that would block (for observe-mode dashboards);
    `blocked_by` lists only those whose enforcement switch is currently ON, and
    `allowed` is True iff `blocked_by` is empty. Nothing is incremented here."""
    day = pacific_day(now)
    carrier = recipient_carrier(to_number, carrier_hint, provider)
    tmo = is_tmobile(carrier)
    reasons: List[str] = []
    detail: dict = {"pacific_day": day}

    if not within_working_hours(now):
        reasons.append("outside_working_hours")

    prov_cap = int(settings.PROVIDER_DAILY_CAP or 0)
    prov_used = _get_int(_k_provider(provider, day))
    detail["provider_used"], detail["provider_cap"] = prov_used, prov_cap
    if prov_cap and prov_used >= prov_cap:
        reasons.append("provider_cap")

    if tmo:
        tcap = int(settings.TMOBILE_PER_PROVIDER_CAP or 0)
        tused = _get_int(_k_tmobile(provider, day))
        detail["tmobile_used"], detail["tmobile_cap"] = tused, tcap
        if tcap and tused >= tcap:
            reasons.append("tmobile_cap")
        if _dedup_contains(to_number, day):
            reasons.append("tmobile_duplicate")

    blocked_by: List[str] = []
    for r in reasons:
        if r == "outside_working_hours":
            if hours_enforced():
                blocked_by.append(r)
        elif r in ("provider_cap", "tmobile_cap"):
            if caps_enforced():
                blocked_by.append(r)
        elif r == "tmobile_duplicate":
            if dedup_enforced():
                blocked_by.append(r)

    return CapDecision(
        allowed=not blocked_by, carrier=carrier, provider=normalize_carrier(provider),
        is_tmobile=tmo, reasons=reasons, blocked_by=blocked_by, detail=detail,
    )


def record_send(
    provider: str,
    to_number: str,
    carrier_hint: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Count an ACTUAL send toward the provider total (+ the T-Mobile per-provider
    counter and the cross-provider dedup set when the recipient is T-Mobile). Call
    this after a successful send, in ANY mode, so the dashboard reflects reality.
    Counters expire shortly after the Pacific day they belong to. Best-effort —
    never raises, so counting can't break a send."""
    day = pacific_day(now)
    ttl = _seconds_until_pacific_midnight(now) + 3600
    carrier = recipient_carrier(to_number, carrier_hint, provider)
    try:
        c = _client()

        def bump(key):
            c.incr(key)
            c.expire(key, ttl)

        bump(_k_provider(provider, day))
        bump(_k_sent(day))                       # fleet total sent today
        c.sadd(_k_recipients(day), _digits(to_number)); c.expire(_k_recipients(day), ttl)
        if carrier:                              # per-recipient-carrier rollup (T-Mobile, AT&T, ...)
            bump(_k_carrier(carrier, day))
            c.sadd(_k_carriers_seen(day), carrier); c.expire(_k_carriers_seen(day), ttl)
        if is_tmobile(carrier):
            bump(_k_tmobile(provider, day))
            c.sadd(_k_dedup(day), _digits(to_number)); c.expire(_k_dedup(day), ttl)
    except Exception:
        pass


def record_skip(reason: str, now: Optional[datetime] = None) -> None:
    """Count a send that was HELD/skipped by enforcement (a duplicate, a cap, or
    out-of-hours) for the DID-fleet dashboard's 'skipped · duplicate' / 'held by cap'
    tiles. Best-effort; never raises."""
    day = pacific_day(now)
    ttl = _seconds_until_pacific_midnight(now) + 3600
    try:
        c = _client()
        key = _k_skip(reason, day)
        c.incr(key)
        c.expire(key, ttl)
    except Exception:
        pass


def dispatch_stats(now: Optional[datetime] = None) -> dict:
    """Today's routing tallies for the DID-fleet 'Dedup / routing' strip."""
    day = pacific_day(now)
    try:
        c = _client()
        recips = c.scard(_k_recipients(day))
    except Exception:
        recips = 0
    return {
        "recipients_total": int(recips or 0),
        "sent": _get_int(_k_sent(day)),
        "skipped_duplicate": _get_int(_k_skip("tmobile_duplicate", day)),
        "held_by_cap": _get_int(_k_skip("held", day)),
    }


def carrier_rollup(now: Optional[datetime] = None) -> List[dict]:
    """Per-recipient-carrier totals seen today (T-Mobile, AT&T, ...). Rollup only — no
    carrier-level cap (limit None), since T-Mobile is capped per provider elsewhere."""
    day = pacific_day(now)
    out: List[dict] = []
    try:
        names = _client().smembers(_k_carriers_seen(day)) or []
    except Exception:
        names = []
    for n in names:
        nm = n.decode() if isinstance(n, (bytes, bytearray)) else str(n)
        out.append({"carrier": nm, "sent_today": _get_int(_k_carrier(nm, day)), "limit": None})
    return out


def tmobile_provider_with_room(providers: List[str], now: Optional[datetime] = None) -> Optional[str]:
    """For a T-Mobile recipient: the FIRST provider (in the given order) that still has
    T-Mobile headroom today. As each provider fills its per-provider cap (2,000/day) the
    next one is chosen — i.e. roll over instead of stopping. Returns None only when EVERY
    provider is maxed for the day; the caller then HOLDS the send for later. Buckets are
    Pacific-day, so all of them reset together at midnight PT. (Cap of 0 disables the
    limit, so the first provider is always returned.)"""
    day = pacific_day(now)
    cap = int(settings.TMOBILE_PER_PROVIDER_CAP or 0)
    for p in (providers or []):
        if not cap or _get_int(_k_tmobile(p, day)) < cap:
            return p
    return None


def caps_status(providers: Optional[List[str]] = None, now: Optional[datetime] = None) -> dict:
    """Snapshot for the DID Fleet dashboard: per-provider totals + T-Mobile counters,
    the working-hours state, and the active enforcement modes (observe-only/enforce)."""
    day = pacific_day(now)
    rows = []
    for p in (providers or []):
        used = _get_int(_k_provider(p, day))
        cap = int(settings.PROVIDER_DAILY_CAP or 0)
        rows.append({
            "provider": normalize_carrier(p),
            "sent_today": used,
            "provider_cap": cap,
            "remaining": (max(0, cap - used) if cap else None),
            "tmobile_today": _get_int(_k_tmobile(p, day)),
            "tmobile_cap": int(settings.TMOBILE_PER_PROVIDER_CAP or 0),
        })
    return {
        "as_of_pacific": day,
        "reset_tz": settings.CAP_RESET_TZ,
        "provider_daily_cap": int(settings.PROVIDER_DAILY_CAP or 0),
        "tmobile_per_provider_cap": int(settings.TMOBILE_PER_PROVIDER_CAP or 0),
        "working_hours": {
            "open": within_working_hours(now),
            "window": f"{int(settings.WORKING_HOURS_START):02d}:00-{int(settings.WORKING_HOURS_END):02d}:00",
            "tz": settings.WORKING_HOURS_TZ,
            "days": "Mon-Fri",
        },
        "enforcement": {
            "caps": "enforce" if caps_enforced() else "observe-only",
            "dedup": "enforce" if dedup_enforced() else "observe-only",
            "working_hours": "enforce" if hours_enforced() else "observe-only",
        },
        "providers": rows,
    }


# --- Integration hook (wire when connecting the page) -------------------------
# In the send path, AFTER the first-template lockdown passes and a sender/provider
# is selected (communication_provider.send_sms, where `carrier = self._carrier_of(
# source_number)`), do:
#
#     from app.ai.services import carrier_caps
#     dec = carrier_caps.evaluate_send(provider=carrier, to_number=to, carrier_hint=...)
#     if not dec.allowed:                 # only blocks when an *_ENFORCE flag is on
#         return {"status": "skipped", "blocked_by": dec.blocked_by, ...}
#     ... perform the real send ...
#     carrier_caps.record_send(provider=carrier, to_number=to, carrier_hint=...)
#
# With all enforcement flags OFF (default) `dec.allowed` is always True, so this is
# pure observation — it changes nothing about sending or the first-template lockdown.
