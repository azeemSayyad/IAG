"""Runtime engine feature flags with a UI-toggleable Redis override.

Each engine flag defaults to its settings/env value, but a Redis override (set from
the SMS Manager toggle) takes precedence — so the capacity engine and fatigue can be
turned on/off LIVE with no redeploy, exactly like the existing send kill-switch. The
override is global (deployment-wide), matching the global env flags.

Only the two boolean engine switches are toggleable here. CARRIER_POOLS_JSON stays an
env value (it is carrier *configuration* — numbers/limits — not an on/off switch), and
the first-template lockdown is never exposed here.
"""
from app.core.config import settings

# Flags that may be overridden from the UI (must be booleans).
# The *_ENFORCE flags flip the DID-fleet caps/dedup/working-hours from observe-only
# (count + record, never block) to enforce (block over-cap / out-of-window sends).
TOGGLEABLE = (
    # SAME_DAY_PACING_ENABLED is the MASTER switch for the same-day pacing/release
    # engine (capacity-sized release, drip, booking). UI override wins over the env
    # default, so the whole engine can be turned on/off from the frontend.
    "SAME_DAY_PACING_ENABLED",
    "CAPACITY_PACING_ENABLED", "FATIGUE_ENABLED",
    "CARRIER_CAPS_ENFORCE", "TMOBILE_DEDUP_ENFORCE", "WORKING_HOURS_ENFORCE",
)
_KEY = "engine:flag:{name}"
_TRUE = (b"1", "1", 1, True, b"true", "true")


def engine_enabled(name: str) -> bool:
    """Effective on/off for a flag: a Redis override (UI toggle) wins; otherwise the
    env/settings default. Fail-safe: any Redis hiccup falls back to the env value."""
    try:
        from app.core.redis import redis_service
        v = redis_service.client.get(_KEY.format(name=name))
        if v is not None:
            return v in _TRUE
    except Exception:
        pass
    return bool(getattr(settings, name, False))


def set_engine_override(name: str, enabled) -> None:
    """Set or clear a flag's Redis override. enabled=True/False sets it; enabled=None
    clears it (reverts to the env default). Raises on an unknown flag."""
    if name not in TOGGLEABLE:
        raise ValueError(f"not a toggleable engine flag: {name}")
    from app.core.redis import redis_service
    key = _KEY.format(name=name)
    if enabled is None:
        redis_service.client.delete(key)
    else:
        redis_service.client.set(key, "1" if enabled else "0")


def flag_source(name: str) -> str:
    """'override' if a Redis override is set, else 'env' (the settings default)."""
    try:
        from app.core.redis import redis_service
        return "override" if redis_service.client.get(_KEY.format(name=name)) is not None else "env"
    except Exception:
        return "env"


def all_flags() -> dict:
    """{flag: {enabled, source}} for every toggleable flag — for the dashboard."""
    return {n: {"enabled": engine_enabled(n), "source": flag_source(n)} for n in TOGGLEABLE}


def same_day_pacing_enabled() -> bool:
    """Master gate for the same-day pacing/release engine: a UI override (Redis) wins,
    else the SAME_DAY_PACING_ENABLED env default. Use this everywhere the engine asks
    'is pacing on?' so the whole engine can be flipped from the frontend — when it's
    off (the default) behaviour is exactly as before."""
    return engine_enabled("SAME_DAY_PACING_ENABLED")
