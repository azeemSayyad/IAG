"""
Slot Locking Engine (Step 6.3)

Prevents double-booking using Redis distributed locks.

Rules:
- Lock acquired via Redis SETNX
- Lock TTL: 5 minutes
- If customer doesn't confirm within 5 minutes → release lock
- If slot conflict detected → offer next available slot
- Never allow double-booking under any circumstances
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from uuid import UUID

from app.core.redis import redis_service


LOCK_TTL_SECONDS = 300  # 5 minutes
LOCK_PREFIX = "slot_lock:"


def acquire_slot_lock(
    tenant_id: str,
    agent_id: str,
    slot_key: str,
    lead_id: str,
    ttl: int = LOCK_TTL_SECONDS,
) -> bool:
    """
    Acquire a distributed lock for an appointment slot.

    Args:
        tenant_id: Tenant ID
        agent_id: Agent ID
        slot_key: Slot key (YYYYMMDD_HHMM format)
        lead_id: Lead ID acquiring the lock
        ttl: Lock TTL in seconds

    Returns:
        True if lock acquired, False if already locked
    """
    lock_key = f"{LOCK_PREFIX}{tenant_id}:{agent_id}:{slot_key}"
    lock_value = f"{lead_id}:{datetime.now(timezone.utc).isoformat()}"

    return redis_service.client.set(lock_key, lock_value, nx=True, ex=ttl)


def release_slot_lock(
    tenant_id: str,
    agent_id: str,
    slot_key: str,
    lead_id: str,
) -> bool:
    """
    Release a slot lock.

    Only releases if the lock is held by the same lead.

    Returns:
        True if lock released, False if not held by this lead
    """
    lock_key = f"{LOCK_PREFIX}{tenant_id}:{agent_id}:{slot_key}"
    current_value = redis_service.client.get(lock_key)

    if current_value and current_value.startswith(f"{lead_id}:"):
        redis_service.client.delete(lock_key)
        return True
    return False


def is_slot_locked(
    tenant_id: str,
    agent_id: str,
    slot_key: str,
) -> bool:
    """
    Check if a slot is currently locked.
    """
    lock_key = f"{LOCK_PREFIX}{tenant_id}:{agent_id}:{slot_key}"
    return bool(redis_service.client.exists(lock_key))


def get_slot_lock_info(
    tenant_id: str,
    agent_id: str,
    slot_key: str,
) -> Optional[Dict]:
    """
    Get information about a slot lock.
    """
    lock_key = f"{LOCK_PREFIX}{tenant_id}:{agent_id}:{slot_key}"
    lock_value = redis_service.client.get(lock_key)

    if not lock_value:
        return None

    parts = lock_value.split(":", 1)
    if len(parts) == 2:
        ttl = redis_service.client.ttl(lock_key)
        return {
            "lead_id": parts[0],
            "locked_at": parts[1],
            "ttl_seconds": ttl,
        }
    return None


def extend_slot_lock(
    tenant_id: str,
    agent_id: str,
    slot_key: str,
    lead_id: str,
    ttl: int = LOCK_TTL_SECONDS,
) -> bool:
    """
    Extend a slot lock TTL.
    """
    lock_key = f"{LOCK_PREFIX}{tenant_id}:{agent_id}:{slot_key}"
    current_value = redis_service.client.get(lock_key)

    if current_value and current_value.startswith(f"{lead_id}:"):
        redis_service.client.expire(lock_key, ttl)
        return True
    return False


def cleanup_expired_locks(tenant_id: str) -> int:
    """
    Clean up expired locks for a tenant.
    Returns number of locks cleaned up.
    """
    pattern = f"{LOCK_PREFIX}{tenant_id}:*"
    keys = redis_service.client.keys(pattern)
    cleaned = 0

    for key in keys:
        ttl = redis_service.client.ttl(key)
        if ttl == -1:  # No expiry set
            redis_service.client.delete(key)
            cleaned += 1

    return cleaned


def get_all_locked_slots(tenant_id: str) -> Dict[str, Dict]:
    """
    Get all currently locked slots for a tenant.
    """
    pattern = f"{LOCK_PREFIX}{tenant_id}:*"
    keys = redis_service.client.keys(pattern)
    locked = {}

    for key in keys:
        lock_info = redis_service.client.get(key)
        ttl = redis_service.client.ttl(key)
        if lock_info:
            # Extract slot info from key
            # Format: slot_lock:{tenant_id}:{agent_id}:{slot_key}
            parts = key.replace(LOCK_PREFIX, "").split(":", 2)
            if len(parts) == 3:
                slot_key = parts[2]
                locked[slot_key] = {
                    "lock_info": lock_info,
                    "ttl": ttl,
                }

    return locked
