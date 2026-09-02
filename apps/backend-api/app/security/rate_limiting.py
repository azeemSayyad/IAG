"""
Rate Limiting Service (Step 12.2)

Prevents:
- Brute force attacks
- API abuse
- Spam

Implements:
- Per-endpoint rate limits
- Per-user rate limits
- Per-IP rate limits
- Sliding window counters
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from fastapi import Request, HTTPException, status

from app.core.redis import redis_service


# Rate limit configurations
RATE_LIMITS = {
    # Auth endpoints
    "login": {"max_requests": 5, "window_seconds": 300},  # 5 per 5 min
    "register": {"max_requests": 3, "window_seconds": 3600},  # 3 per hour
    "password_reset": {"max_requests": 3, "window_seconds": 3600},  # 3 per hour

    # API endpoints
    "api_general": {"max_requests": 100, "window_seconds": 60},  # 100 per minute
    "api_read": {"max_requests": 200, "window_seconds": 60},  # 200 per minute
    "api_write": {"max_requests": 50, "window_seconds": 60},  # 50 per minute

    # SMS endpoints
    "sms_send": {"max_requests": 10, "window_seconds": 60},  # 10 per minute
    "sms_bulk": {"max_requests": 100, "window_seconds": 3600},  # 100 per hour

    # Import endpoints
    "csv_import": {"max_requests": 10, "window_seconds": 3600},  # 10 per hour
    "webhook": {"max_requests": 100, "window_seconds": 60},  # 100 per minute
}


def get_rate_limit_key(
    limit_type: str,
    identifier: str,
) -> str:
    """Generate a Redis key for rate limiting."""
    return f"rate_limit:{limit_type}:{identifier}"


def check_rate_limit(
    limit_type: str,
    identifier: str,
    max_requests: int = None,
    window_seconds: int = None,
) -> Tuple[bool, Dict]:
    """
    Check if a request is within rate limits.

    Args:
        limit_type: Type of rate limit (e.g., "login", "api_general")
        identifier: Unique identifier (e.g., user_id, ip_address)
        max_requests: Override max requests
        window_seconds: Override window seconds

    Returns:
        Tuple of (is_allowed, rate_limit_info)
    """
    config = RATE_LIMITS.get(limit_type, RATE_LIMITS["api_general"])
    max_req = max_requests or config["max_requests"]
    window = window_seconds or config["window_seconds"]

    key = get_rate_limit_key(limit_type, identifier)
    now = datetime.now(timezone.utc)

    # Use sliding window with Redis sorted set
    pipe = redis_service.client.pipeline()

    # Remove old entries
    cutoff = (now - timedelta(seconds=window)).timestamp()
    pipe.zremrangebyscore(key, 0, cutoff)

    # Count current entries
    pipe.zcard(key)

    # Add current request
    pipe.zadd(key, {str(now.timestamp()): now.timestamp()})

    # Set expiry
    pipe.expire(key, window)

    results = pipe.execute()
    current_count = results[1]

    is_allowed = current_count < max_req

    return is_allowed, {
        "limit": max_req,
        "remaining": max(0, max_req - current_count - 1),
        "reset_at": (now + timedelta(seconds=window)).isoformat(),
        "window_seconds": window,
    }


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    # Check for forwarded headers
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    return request.client.host if request.client else "unknown"


def rate_limit_middleware(limit_type: str = "api_general"):
    """
    Rate limiting dependency for FastAPI.

    Usage:
        @router.get("/endpoint", dependencies=[Depends(rate_limit_middleware("api_read"))])
    """
    async def check_limit(request: Request):
        client_ip = get_client_ip(request)
        is_allowed, info = check_rate_limit(limit_type, client_ip)

        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "retry_after": info["window_seconds"],
                },
                headers={
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": str(info["remaining"]),
                    "X-RateLimit-Reset": info["reset_at"],
                    "Retry-After": str(info["window_seconds"]),
                },
            )

    return check_limit


def get_rate_limit_status(
    limit_type: str,
    identifier: str,
) -> Dict:
    """
    Get current rate limit status without incrementing.
    """
    config = RATE_LIMITS.get(limit_type, RATE_LIMITS["api_general"])
    key = get_rate_limit_key(limit_type, identifier)
    now = datetime.now(timezone.utc)

    # Clean old entries
    cutoff = (now - timedelta(seconds=config["window_seconds"])).timestamp()
    redis_service.client.zremrangebyscore(key, 0, cutoff)

    # Count current
    current = redis_service.client.zcard(key)

    return {
        "limit": config["max_requests"],
        "current": current,
        "remaining": max(0, config["max_requests"] - current),
        "window_seconds": config["window_seconds"],
    }
