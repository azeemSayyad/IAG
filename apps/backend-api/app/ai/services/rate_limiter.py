"""
SMS Rate Limiting (Step 4.6)

Prevents:
- Spam filtering by carriers
- provider account bans
- Customer complaints

Limits:
- Per-lead: max 3 messages per 24 hours
- Per-tenant: max 100 messages per hour
- Global: max 1000 messages per hour
- Minimum interval: 60 seconds between messages to same lead
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.core.redis import redis_service


class RateLimitResult:
    def __init__(self, allowed: bool, reason: str = "", retry_after: int = 0):
        self.allowed = allowed
        self.reason = reason
        self.retry_after = retry_after  # seconds

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "retry_after": self.retry_after,
        }


# Rate limit configurations — env-tunable (raised for production send volume).
from app.core.config import settings as _settings

RATE_LIMITS = {
    "per_lead_per_day": _settings.RATE_LIMIT_PER_LEAD_PER_DAY,
    "per_lead_interval_seconds": _settings.RATE_LIMIT_PER_LEAD_INTERVAL_SECONDS,
    "per_tenant_per_hour": _settings.RATE_LIMIT_PER_TENANT_PER_HOUR,
    "global_per_hour": _settings.RATE_LIMIT_GLOBAL_PER_HOUR,
}


def check_rate_limit(
    tenant_id: str,
    lead_id: str,
    phone: str,
) -> RateLimitResult:
    """
    Check if an SMS can be sent to a lead.

    Returns RateLimitResult with allowed status and reason.
    """
    now = datetime.now(timezone.utc)

    # 1. Check per-lead daily limit
    lead_key = f"rate:lead:{tenant_id}:{lead_id}:day"
    lead_count = redis_service.client.get(lead_key)
    if lead_count and int(lead_count) >= RATE_LIMITS["per_lead_per_day"]:
        return RateLimitResult(
            allowed=False,
            reason=f"Lead daily limit reached ({RATE_LIMITS['per_lead_per_day']} messages/day)",
            retry_after=86400,  # 24 hours
        )

    # 2. Check per-lead interval (minimum time between messages)
    interval_key = f"rate:lead:{tenant_id}:{lead_id}:last"
    last_sent = redis_service.client.get(interval_key)
    if last_sent:
        last_time = datetime.fromisoformat(last_sent)
        elapsed = (now - last_time).total_seconds()
        if elapsed < RATE_LIMITS["per_lead_interval_seconds"]:
            wait = int(RATE_LIMITS["per_lead_interval_seconds"] - elapsed)
            return RateLimitResult(
                allowed=False,
                reason=f"Too soon. Wait {wait} seconds between messages.",
                retry_after=wait,
            )

    # 3. Check per-tenant hourly limit
    tenant_key = f"rate:tenant:{tenant_id}:hour"
    tenant_count = redis_service.client.get(tenant_key)
    if tenant_count and int(tenant_count) >= RATE_LIMITS["per_tenant_per_hour"]:
        return RateLimitResult(
            allowed=False,
            reason=f"Tenant hourly limit reached ({RATE_LIMITS['per_tenant_per_hour']} messages/hour)",
            retry_after=3600,
        )

    # 4. Check global hourly limit
    global_key = "rate:global:hour"
    global_count = redis_service.client.get(global_key)
    if global_count and int(global_count) >= RATE_LIMITS["global_per_hour"]:
        return RateLimitResult(
            allowed=False,
            reason=f"Global hourly limit reached ({RATE_LIMITS['global_per_hour']} messages/hour)",
            retry_after=3600,
        )

    return RateLimitResult(allowed=True)


def record_sms_sent(tenant_id: str, lead_id: str) -> None:
    """
    Record that an SMS was sent. Updates all rate limit counters.
    """
    now = datetime.now(timezone.utc)

    # Per-lead daily counter
    lead_key = f"rate:lead:{tenant_id}:{lead_id}:day"
    pipe = redis_service.client.pipeline()
    pipe.incr(lead_key)
    # Expire at end of day
    seconds_until_midnight = 86400 - (now.hour * 3600 + now.minute * 60 + now.second)
    pipe.expire(lead_key, seconds_until_midnight)

    # Per-lead last sent timestamp
    interval_key = f"rate:lead:{tenant_id}:{lead_id}:last"
    pipe.set(interval_key, now.isoformat(), ex=300)  # 5 min TTL

    # Per-tenant hourly counter
    tenant_key = f"rate:tenant:{tenant_id}:hour"
    pipe.incr(tenant_key)
    pipe.expire(tenant_key, 3600)

    # Global hourly counter
    global_key = "rate:global:hour"
    pipe.incr(global_key)
    pipe.expire(global_key, 3600)

    pipe.execute()


def get_rate_limit_status(tenant_id: str, lead_id: str) -> Dict:
    """
    Get current rate limit status for a lead.
    """
    now = datetime.now(timezone.utc)

    lead_key = f"rate:lead:{tenant_id}:{lead_id}:day"
    lead_count = int(redis_service.client.get(lead_key) or 0)

    interval_key = f"rate:lead:{tenant_id}:{lead_id}:last"
    last_sent = redis_service.client.get(interval_key)
    last_sent_seconds = 0
    if last_sent:
        last_time = datetime.fromisoformat(last_sent)
        last_sent_seconds = int((now - last_time).total_seconds())

    tenant_key = f"rate:tenant:{tenant_id}:hour"
    tenant_count = int(redis_service.client.get(tenant_key) or 0)

    global_key = "rate:global:hour"
    global_count = int(redis_service.client.get(global_key) or 0)

    return {
        "lead_daily_count": lead_count,
        "lead_daily_limit": RATE_LIMITS["per_lead_per_day"],
        "lead_seconds_since_last": last_sent_seconds,
        "lead_min_interval": RATE_LIMITS["per_lead_interval_seconds"],
        "tenant_hourly_count": tenant_count,
        "tenant_hourly_limit": RATE_LIMITS["per_tenant_per_hour"],
        "global_hourly_count": global_count,
        "global_hourly_limit": RATE_LIMITS["global_per_hour"],
    }
