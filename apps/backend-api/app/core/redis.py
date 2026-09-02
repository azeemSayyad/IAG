import redis
import json
from typing import Optional, Any

from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


class RedisService:
    """Redis wrapper for caching, slot locking, queues, and pub/sub."""

    def __init__(self, client: redis.Redis = redis_client):
        self.client = client

    # --- Caching ---
    def get_cache(self, key: str) -> Optional[Any]:
        data = self.client.get(key)
        if data:
            return json.loads(data)
        return None

    def set_cache(self, key: str, value: Any, ttl: int = 300) -> None:
        self.client.set(key, json.dumps(value), ex=ttl)

    def delete_cache(self, key: str) -> None:
        self.client.delete(key)

    # --- Slot Locking ---
    def acquire_slot_lock(self, slot_key: str, ttl: int = 300) -> bool:
        """Acquire a distributed lock for an appointment slot. Returns True if acquired."""
        return bool(self.client.set(f"slot_lock:{slot_key}", "locked", nx=True, ex=ttl))

    def release_slot_lock(self, slot_key: str) -> None:
        """Release a slot lock."""
        self.client.delete(f"slot_lock:{slot_key}")

    def is_slot_locked(self, slot_key: str) -> bool:
        """Check if a slot is currently locked."""
        return bool(self.client.exists(f"slot_lock:{slot_key}"))

    # --- Rate Limiting ---
    def check_rate_limit(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Returns True if under the rate limit, False if exceeded."""
        current = self.client.incr(f"rate:{key}")
        if current == 1:
            self.client.expire(f"rate:{key}", window_seconds)
        return current <= max_requests

    # --- SMS Queue ---
    def enqueue_sms(self, data: dict) -> None:
        """Push an SMS job to the outbound queue."""
        self.client.rpush("queue:outbound_sms", json.dumps(data))

    def dequeue_sms(self) -> Optional[dict]:
        """Pop an SMS job from the outbound queue."""
        data = self.client.lpop("queue:outbound_sms")
        if data:
            return json.loads(data)
        return None

    # --- Pub/Sub ---
    def publish_event(self, channel: str, event: dict) -> None:
        """Publish a realtime event to a channel."""
        self.client.publish(channel, json.dumps(event))

    # --- Notification Queue ---
    def enqueue_notification(self, tenant_id: str, notification: dict) -> None:
        """Push a notification for a tenant."""
        self.client.rpush(f"notifications:{tenant_id}", json.dumps(notification))

    def get_notifications(self, tenant_id: str, count: int = 10) -> list:
        """Get pending notifications for a tenant."""
        notifications = []
        for _ in range(count):
            data = self.client.lpop(f"notifications:{tenant_id}")
            if data:
                notifications.append(json.loads(data))
            else:
                break
        return notifications


# Singleton instance
redis_service = RedisService()
