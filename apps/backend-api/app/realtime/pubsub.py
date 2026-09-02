"""
Redis Pub/Sub Service (Step 10.2)

Used for:
- Broadcasting events across multiple server instances
- Real-time sync between services
- Event-driven architecture
"""

import json
import asyncio
from typing import Dict, Callable, Optional, List
from datetime import datetime, timezone

from app.core.redis import redis_service


# Channel names
CHANNEL_NOTIFICATIONS = "realtime:notifications"
CHANNEL_BOOKINGS = "realtime:bookings"
CHANNEL_AGENTS = "realtime:agents"
CHANNEL_SYSTEM = "realtime:system"


class PubSubManager:
    """Manages Redis pub/sub subscriptions."""

    def __init__(self):
        self._pubsub = None
        self._handlers: Dict[str, List[Callable]] = {}
        self._running = False

    async def start(self):
        """Start listening for messages."""
        if self._running:
            return

        self._pubsub = redis_service.client.pubsub()
        self._running = True

        # Subscribe to all channels
        for channel in self._handlers.keys():
            self._pubsub.subscribe(channel)

        # Start listener task
        asyncio.create_task(self._listen())

    async def stop(self):
        """Stop listening for messages."""
        self._running = False
        if self._pubsub:
            self._pubsub.unsubscribe()
            self._pubsub.close()

    def subscribe(self, channel: str, handler: Callable):
        """Subscribe to a channel with a handler."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        # Subscribe if already running
        if self._pubsub and self._running:
            self._pubsub.subscribe(channel)

    def unsubscribe(self, channel: str, handler: Callable = None):
        """Unsubscribe from a channel."""
        if channel in self._handlers:
            if handler:
                self._handlers[channel] = [h for h in self._handlers[channel] if h != handler]
            else:
                del self._handlers[channel]

    async def publish(self, channel: str, data: Dict):
        """Publish a message to a channel."""
        message = {
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        redis_service.client.publish(channel, json.dumps(message))

    async def _listen(self):
        """Listen for messages on subscribed channels."""
        while self._running:
            try:
                message = self._pubsub.get_message(timeout=1.0)
                if message and message["type"] == "message":
                    channel = message["channel"]
                    data = json.loads(message["data"])

                    # Call handlers
                    for handler in self._handlers.get(channel, []):
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(data)
                            else:
                                handler(data)
                        except Exception as e:
                            print(f"Handler error for {channel}: {e}")

                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"PubSub listener error: {e}")
                await asyncio.sleep(1)


# Global pub/sub manager
pubsub_manager = PubSubManager()


# Convenience functions
async def publish_notification(tenant_id: str, notification: Dict):
    """Publish a notification event."""
    await pubsub_manager.publish(CHANNEL_NOTIFICATIONS, {
        "tenant_id": tenant_id,
        "notification": notification,
    })


async def publish_booking_event(tenant_id: str, event_type: str, booking_data: Dict):
    """Publish a booking event."""
    await pubsub_manager.publish(CHANNEL_BOOKINGS, {
        "tenant_id": tenant_id,
        "event_type": event_type,
        "booking": booking_data,
    })


async def publish_agent_event(tenant_id: str, agent_id: str, event_type: str, data: Dict):
    """Publish an agent event."""
    await pubsub_manager.publish(CHANNEL_AGENTS, {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "data": data,
    })


async def publish_system_event(tenant_id: str, event_type: str, data: Dict):
    """Publish a system event."""
    await pubsub_manager.publish(CHANNEL_SYSTEM, {
        "tenant_id": tenant_id,
        "event_type": event_type,
        "data": data,
    })
