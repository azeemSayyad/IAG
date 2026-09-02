"""Wrap-up presence — agents currently on the Add Deal form (after-call work).

Display-only for the Agent Availability "Wrapping" KPI. A per-tenant Redis sorted
set scored by expiry epoch: the Add Deal form heartbeats (ZADD) while it is open,
and the count is the members not yet expired. Closing the tab / submitting stops
the heartbeat, so the entry ages out within _TTL.

This does NOT touch queue status, lead assignment, or the send path — it is a
pure read-side counter, so the first-template lockdown is unaffected.
"""
import time as _time

from app.core.redis import redis_service

_TTL = 60  # seconds; the form heartbeats well inside this, so a closed tab self-clears
_KEY = "sms:wrap:{tid}"


def mark_wrapping(tenant_id: str, user_id: str, active: bool) -> None:
    """Mark (active=True) or clear (active=False) an agent as on the Add Deal form."""
    try:
        c = redis_service.client
        key = _KEY.format(tid=tenant_id)
        if active:
            c.zadd(key, {str(user_id): _time.time() + _TTL})
            c.expire(key, _TTL * 3)  # whole-set safety expiry
        else:
            c.zrem(key, str(user_id))
    except Exception:
        pass


def wrapping_count(tenant_id: str) -> int:
    """How many agents are wrapping right now (expired entries pruned first)."""
    try:
        c = redis_service.client
        key = _KEY.format(tid=tenant_id)
        c.zremrangebyscore(key, 0, _time.time())  # drop expired heartbeats
        return int(c.zcard(key))
    except Exception:
        return 0


def wrapping_user_ids(tenant_id: str) -> set:
    """The user_ids currently wrapping (expired heartbeats pruned first) — so the Agent
    Availability board can flag WHICH agents are on the Add Deal form, not just a count."""
    try:
        c = redis_service.client
        key = _KEY.format(tid=tenant_id)
        c.zremrangebyscore(key, 0, _time.time())  # drop expired heartbeats
        return {
            (m.decode() if isinstance(m, (bytes, bytearray)) else str(m))
            for m in (c.zrange(key, 0, -1) or [])
        }
    except Exception:
        return set()
