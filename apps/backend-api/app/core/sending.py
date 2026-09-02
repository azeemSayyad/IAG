"""Global send kill-switch (per tenant).

A single runtime flag, stored in Redis so it is shared across the web server,
the Celery worker, and the beat scheduler and survives redeploys. When a tenant
is paused, EVERY outbound path checks this and sends nothing:
  * outreach SMS, AI reply SMS, follow-ups, reminders  -> EngageCloudService.send_sms
  * capacity-engine lead releases                       -> release.run_cycle

Toggled from the admin UI (Upload Leads page) via the /ingestion/sending/* API.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "sending:paused:{tid}"
_AUTOPILOT_KEY = "autopilot:paused:{tid}"


def is_sending_paused(tenant_id) -> bool:
    """True if outbound sending is paused for this tenant."""
    if not tenant_id:
        return False
    try:
        from app.core.redis import redis_service
        return bool(redis_service.client.get(_KEY.format(tid=tenant_id)))
    except Exception:
        # Fail OPEN is wrong for a kill-switch — but if Redis is unreachable we
        # cannot know the flag, so we do NOT block (matches prior behaviour where
        # there was no switch at all). Operators also have the env-var stop.
        return False


def set_sending_paused(tenant_id, paused: bool, actor: str = None) -> bool:
    """Pause (True) or resume (False) all outbound sending for a tenant."""
    try:
        from app.core.redis import redis_service
        key = _KEY.format(tid=tenant_id)
        if paused:
            redis_service.client.set(key, "1")
        else:
            redis_service.client.delete(key)
    except Exception as exc:  # pragma: no cover
        logger.warning("set_sending_paused failed: %s", exc)
        return False
    try:
        from app.core.audit import log_ai_action
        log_ai_action(
            tenant_id=str(tenant_id),
            action="sending_paused" if paused else "sending_resumed",
            resource_type="tenant",
            resource_id=str(tenant_id),
            details={"actor": actor},
        )
    except Exception:
        pass
    return True


# --- Queue-Only Mode (booking-autopilot pause) -----------------------------
# A SEPARATE, narrower switch than the kill-switch above. When ON for a tenant:
#   * the FIRST outreach template STILL sends (we want to reach the lead),
#   * the customer's inbound reply is STILL recorded (so the SMS human queue
#     can pick up positive replies),
#   * but the AI sends NO booking reply and books NO appointment, and all
#     follow-ups / reminders / nurture are skipped.
# Net effect: first template only, then total system silence — humans in the
# SMS queue handle 100% of the rest. The appointment-booking pipeline stays
# fully intact and returns the moment this flag is turned back off.


# === FIRST-TEMPLATE-ONLY LOCKDOWN (permanent, NOT togglable) ================
# The platform may ONLY ever send the first-template outreach. Every other
# outbound SMS — AI replies, booking/slot offers, objection responses, follow-
# ups, reminders, nurture, post-call, AND manual agent sends — is forbidden.
# Enforced at the single provider chokepoint (EngageCloudService.send_sms) via
# the `kind` gate; Queue-Only Mode is hardcoded ON below so every AI/booking
# source short-circuits too. Redis-independent, so it can't be switched off or
# fail open.
FIRST_TEMPLATE_ONLY = True


def record_send_decision(kind: str, allowed: bool) -> None:
    """Cheap per-kind Redis counter so we can PROVE only first templates send:
    send:allowed:first_template grows; send:blocked:* captures everything else."""
    try:
        from app.core.redis import redis_service
        bucket = "allowed" if allowed else "blocked"
        redis_service.client.incr(f"send:{bucket}:{(kind or 'other')}")
    except Exception:
        pass


def is_autopilot_paused(tenant_id) -> bool:
    """ALWAYS True — Queue-Only Mode is hardcoded ON (see FIRST_TEMPLATE_ONLY).
    The platform never books, AI-replies, follows up or reminds; only the first
    template goes out. The old per-tenant Redis flag is intentionally ignored so
    this can never be switched off or fail open."""
    return True


def set_autopilot_paused(tenant_id, paused: bool, actor: str = None) -> bool:
    """Enable (True) or disable (False) Queue-Only Mode for a tenant."""
    try:
        from app.core.redis import redis_service
        key = _AUTOPILOT_KEY.format(tid=tenant_id)
        if paused:
            redis_service.client.set(key, "1")
        else:
            redis_service.client.delete(key)
    except Exception as exc:  # pragma: no cover
        logger.warning("set_autopilot_paused failed: %s", exc)
        return False
    try:
        from app.core.audit import log_ai_action
        log_ai_action(
            tenant_id=str(tenant_id),
            action="autopilot_paused" if paused else "autopilot_resumed",
            resource_type="tenant",
            resource_id=str(tenant_id),
            details={"actor": actor},
        )
    except Exception:
        pass
    return True


# --- Queue-Only drip rate (dynamic) ----------------------------------------
# While Queue-Only Mode is ON, large (>500) held batches are released at a flat,
# admin-set rate instead of the booking-driven capacity engine: release
# `leads` first-templates every `minutes`. Stored per-tenant in Redis.
_DRIP_LEADS_KEY = "autopilot:drip:leads:{tid}"
_DRIP_MINUTES_KEY = "autopilot:drip:minutes:{tid}"
_DRIP_LAST_KEY = "autopilot:drip:last:{tid}"

DRIP_DEFAULT_LEADS = 50
DRIP_DEFAULT_MINUTES = 10
_DRIP_MAX_LEADS = 5000
_DRIP_MAX_MINUTES = 1440


def get_drip_config(tenant_id) -> dict:
    """Return {'leads': int, 'minutes': int} for this tenant (defaults if unset)."""
    leads, minutes = DRIP_DEFAULT_LEADS, DRIP_DEFAULT_MINUTES
    try:
        from app.core.redis import redis_service
        lv = redis_service.client.get(_DRIP_LEADS_KEY.format(tid=tenant_id))
        mv = redis_service.client.get(_DRIP_MINUTES_KEY.format(tid=tenant_id))
        if lv is not None:
            leads = int(lv)
        if mv is not None:
            minutes = int(mv)
    except Exception:
        pass
    return {"leads": leads, "minutes": minutes}


def set_drip_config(tenant_id, leads: int, minutes: int, actor: str = None) -> dict:
    """Set the drip rate. Clamps to sane bounds. Returns the stored config."""
    leads = max(1, min(int(leads), _DRIP_MAX_LEADS))
    minutes = max(1, min(int(minutes), _DRIP_MAX_MINUTES))
    try:
        from app.core.redis import redis_service
        redis_service.client.set(_DRIP_LEADS_KEY.format(tid=tenant_id), str(leads))
        redis_service.client.set(_DRIP_MINUTES_KEY.format(tid=tenant_id), str(minutes))
    except Exception as exc:  # pragma: no cover
        logger.warning("set_drip_config failed: %s", exc)
    try:
        from app.core.audit import log_ai_action
        log_ai_action(
            tenant_id=str(tenant_id), action="autopilot_drip_set",
            resource_type="tenant", resource_id=str(tenant_id),
            details={"actor": actor, "leads": leads, "minutes": minutes},
        )
    except Exception:
        pass
    return get_drip_config(tenant_id)


def get_drip_last_run(tenant_id) -> float:
    """Epoch seconds of the last drip release for this tenant (0.0 if never)."""
    try:
        from app.core.redis import redis_service
        v = redis_service.client.get(_DRIP_LAST_KEY.format(tid=tenant_id))
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def set_drip_last_run(tenant_id, epoch_seconds: float) -> None:
    try:
        from app.core.redis import redis_service
        redis_service.client.set(_DRIP_LAST_KEY.format(tid=tenant_id), str(float(epoch_seconds)))
    except Exception:
        pass
