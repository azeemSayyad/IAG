"""Per-lead outreach fatigue: frequency cap + cooldown (Redis).

Stops the same person being over-texted across campaigns / re-runs — the top cause
of spam complaints, which is the top cause of cold-number death. Flag-gated
(FATIGUE_ENABLED, default OFF) so it's dormant until enabled. Enforced at the
first-template send gate (send_sms_to_lead), alongside the existing per-lead
rate-limit and per-campaign send-once. Redis-based, so no DB migration.

Keyed by the last 10 digits of the phone, so the same person matches regardless of
formatting. Fail-open: a Redis hiccup never blocks a legitimate send.
"""
from app.core.config import settings
from app.core.redis import redis_service
from app.core import engine_flags


def _norm(phone) -> str:
    d = "".join(c for c in str(phone or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def fatigue_ok(phone) -> bool:
    """True if this phone may be texted now: under the frequency cap AND not in
    cooldown. Always True when the feature is off or the phone is unusable."""
    if not engine_flags.engine_enabled("FATIGUE_ENABLED"):
        return True
    p = _norm(phone)
    if not p:
        return True
    cap = int(getattr(settings, "FATIGUE_FREQ_CAP", 4))
    client = redis_service.client
    try:
        if client.get(f"fatigue:cool:{p}"):
            return False
        if int(client.get(f"fatigue:count:{p}") or 0) >= cap:
            return False
    except Exception:
        return True  # fail-open
    return True


def fatigue_record(phone) -> None:
    """Record a REAL send: bump the lifetime count and start the cooldown window.
    Call only after a genuine send (not a suppressed duplicate / failure)."""
    if not engine_flags.engine_enabled("FATIGUE_ENABLED"):
        return
    p = _norm(phone)
    if not p:
        return
    hours = int(getattr(settings, "FATIGUE_COOLDOWN_HOURS", 72))
    client = redis_service.client
    try:
        pipe = client.pipeline()
        pipe.incr(f"fatigue:count:{p}")
        pipe.expire(f"fatigue:count:{p}", 60 * 60 * 24 * 365)   # ~1yr lifetime window
        pipe.set(f"fatigue:cool:{p}", "1", ex=hours * 3600)
        pipe.execute()
    except Exception:
        pass
