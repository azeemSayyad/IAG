"""
Distributed Realtime System (Phase 44)

Makes the realtime system horizontally scalable:

Step 44.1 — Redis Socket Adapter
    Multi-instance Socket.IO via Redis pub/sub

Step 44.2 — Sticky Sessions
    Route same user to same socket node

Step 44.3 — Durable Event Streaming
    Redis Streams for persistent event log

Step 44.4 — Event Replay
    Missed events recoverable on reconnect

Step 44.5 — Distributed Presence Store
    Presence state in Redis, not in-memory

Architecture:
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Instance 1  │     │  Instance 2  │     │  Instance 3  │
│  Socket.IO   │     │  Socket.IO   │     │  Socket.IO   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                    ┌───────▼───────┐
                    │  Redis Pub/Sub │
                    │  Redis Streams │
                    │  Presence Store│
                    └───────────────┘
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Callable
from uuid import UUID, uuid4

from app.core.redis import redis_service

logger = logging.getLogger(__name__)


# --- Constants ---

# Redis key prefixes
STREAM_KEY = "rt:stream:"
PRESENCE_KEY = "rt:presence:"
SESSION_KEY = "rt:session:"
NODE_KEY = "rt:node:"
EVENT_LOG_KEY = "rt:events"

# Timing
STREAM_MAX_LEN = 10000  # Max events per stream
STREAM_TTL = 86400 * 7  # 7 days
PRESENCE_TTL = 300  # 5 minutes
SESSION_TTL = 3600  # 1 hour
NODE_HEARTBEAT_TTL = 30  # 30 seconds


# --- Distributed Node ---

class DistributedNode:
    """Represents a server instance in the cluster."""

    def __init__(self, node_id: str = None):
        self.node_id = node_id or str(uuid4())[:8]
        self.redis = redis_service
        self.started_at = datetime.now(timezone.utc)

    def register(self) -> None:
        """Register this node in the cluster."""
        self.redis.client.hset(
            f"{NODE_KEY}{self.node_id}",
            mapping={
                "node_id": self.node_id,
                "started_at": self.started_at.isoformat(),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "active",
            },
        )
        self.redis.client.expire(f"{NODE_KEY}{self.node_id}", NODE_HEARTBEAT_TTL * 2)

    def heartbeat(self) -> None:
        """Send heartbeat to cluster."""
        self.redis.client.hset(
            f"{NODE_KEY}{self.node_id}",
            "last_heartbeat",
            datetime.now(timezone.utc).isoformat(),
        )
        self.redis.client.expire(f"{NODE_KEY}{self.node_id}", NODE_HEARTBEAT_TTL * 2)

    def get_active_nodes(self) -> List[Dict]:
        """Get all active nodes in the cluster."""
        nodes = []
        for key in self.redis.client.keys(f"{NODE_KEY}*"):
            node_data = self.redis.client.hgetall(key)
            if node_data:
                last_heartbeat = node_data.get("last_heartbeat")
                if last_heartbeat:
                    try:
                        hb_time = datetime.fromisoformat(last_heartbeat)
                        if (datetime.now(timezone.utc) - hb_time).seconds < NODE_HEARTBEAT_TTL * 2:
                            nodes.append(node_data)
                    except (ValueError, TypeError):
                        pass
        return nodes

    def deregister(self) -> None:
        """Remove this node from the cluster."""
        self.redis.client.delete(f"{NODE_KEY}{self.node_id}")


# --- Sticky Session Manager ---

class StickySessionManager:
    """
    Routes users to consistent socket nodes.

    Uses consistent hashing to ensure the same user
    always connects to the same node (when available).
    """

    def __init__(self, node: DistributedNode):
        self.node = node
        self.redis = redis_service

    def get_node_for_user(self, user_id: str) -> Optional[str]:
        """
        Get the assigned node for a user.

        Uses consistent hashing based on user_id.
        """
        # Check existing assignment
        assigned = self.redis.client.get(f"{SESSION_KEY}{user_id}")
        if assigned:
            # Verify node is still active
            if self.redis.client.exists(f"{NODE_KEY}{assigned}"):
                return assigned

        # Get active nodes
        active_nodes = self.node.get_active_nodes()
        if not active_nodes:
            return self.node.node_id

        # Consistent hash
        node_ids = sorted([n["node_id"] for n in active_nodes])
        hash_val = hash(user_id) % len(node_ids)
        assigned_node = node_ids[hash_val]

        # Store assignment
        self.redis.client.setex(
            f"{SESSION_KEY}{user_id}",
            SESSION_TTL,
            assigned_node,
        )

        return assigned_node

    def is_local_user(self, user_id: str) -> bool:
        """Check if user is assigned to this node."""
        assigned = self.get_node_for_user(user_id)
        return assigned == self.node.node_id

    def reassign_user(self, user_id: str, new_node_id: str) -> None:
        """Reassign a user to a different node."""
        self.redis.client.setex(
            f"{SESSION_KEY}{user_id}",
            SESSION_TTL,
            new_node_id,
        )


# --- Durable Event Stream ---

class DurableEventStream:
    """
    Redis Streams-based durable event log.

    Features:
    - Persistent event storage
    - Consumer groups for parallel processing
    - Event replay from any point
    - Automatic trimming
    """

    def __init__(self):
        self.redis = redis_service

    def publish(
        self,
        stream: str,
        event_type: str,
        data: Dict[str, Any],
        tenant_id: str = None,
        target: str = None,
    ) -> str:
        """
        Publish an event to a stream.

        Args:
            stream: Stream name (e.g., "notifications", "bookings")
            event_type: Type of event
            data: Event data
            tenant_id: Optional tenant scoping
            target: Optional target (user_id, agent_id, room)

        Returns:
            Event ID
        """
        event = {
            "type": event_type,
            "data": json.dumps(data),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "node_id": getattr(self, '_node_id', 'unknown'),
        }

        if tenant_id:
            event["tenant_id"] = tenant_id
        if target:
            event["target"] = target

        stream_key = f"{STREAM_KEY}{stream}"
        event_id = self.redis.client.xadd(
            stream_key,
            event,
            maxlen=STREAM_MAX_LEN,
        )

        # Also log to global event log
        self.redis.client.xadd(
            EVENT_LOG_KEY,
            {"stream": stream, "event_id": event_id, "type": event_type},
            maxlen=STREAM_MAX_LEN,
        )

        return event_id

    def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 1000,
    ) -> List[Dict]:
        """
        Consume events from a stream using consumer groups.

        Args:
            stream: Stream name
            group: Consumer group name
            consumer: Consumer name
            count: Max events per read
            block_ms: Block timeout in milliseconds

        Returns:
            List of events
        """
        stream_key = f"{STREAM_KEY}{stream}"

        # Create consumer group if not exists
        try:
            self.redis.client.xgroup_create(stream_key, group, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists

        # Read events
        results = self.redis.client.xreadgroup(
            group, consumer,
            {stream_key: ">"},
            count=count,
            block=block_ms,
        )

        events = []
        for stream_name, messages in results:
            for event_id, data in messages:
                events.append({
                    "id": event_id,
                    "type": data.get("type"),
                    "data": json.loads(data.get("data", "{}")),
                    "timestamp": data.get("timestamp"),
                    "tenant_id": data.get("tenant_id"),
                    "target": data.get("target"),
                })
                # Acknowledge
                self.redis.client.xack(stream_key, group, event_id)

        return events

    def replay(
        self,
        stream: str,
        from_id: str = "0",
        count: int = 100,
    ) -> List[Dict]:
        """
        Replay events from a stream.

        Args:
            stream: Stream name
            from_id: Start from this event ID (0 = beginning)
            count: Max events

        Returns:
            List of events
        """
        stream_key = f"{STREAM_KEY}{stream}"

        results = self.redis.client.xrange(stream_key, min=from_id, max="+", count=count)

        events = []
        for event_id, data in results:
            events.append({
                "id": event_id,
                "type": data.get("type"),
                "data": json.loads(data.get("data", "{}")),
                "timestamp": data.get("timestamp"),
                "tenant_id": data.get("tenant_id"),
                "target": data.get("target"),
            })

        return events

    def get_stream_info(self, stream: str) -> Dict:
        """Get stream information."""
        stream_key = f"{STREAM_KEY}{stream}"

        try:
            info = self.redis.client.xinfo_stream(stream_key)
            return {
                "length": info.get("length", 0),
                "first_entry": info.get("first-entry"),
                "last_entry": info.get("last-entry"),
                "groups": info.get("groups", 0),
            }
        except Exception:
            return {"length": 0, "groups": 0}

    def trim_stream(self, stream: str, max_len: int = STREAM_MAX_LEN) -> int:
        """Trim a stream to max length."""
        stream_key = f"{STREAM_KEY}{stream}"
        return self.redis.client.xtrim(stream_key, maxlen=max_len)


# --- Event Replay Manager ---

class EventReplayManager:
    """
    Handles event replay for reconnecting clients.

    When a client reconnects, it can request events
    since its last known event ID.
    """

    def __init__(self, stream: DurableEventStream):
        self.stream = stream
        self.redis = redis_service

    def save_cursor(self, user_id: str, stream: str, event_id: str) -> None:
        """Save the last processed event ID for a user."""
        self.redis.client.setex(
            f"rt:cursor:{user_id}:{stream}",
            STREAM_TTL,
            event_id,
        )

    def get_cursor(self, user_id: str, stream: str) -> Optional[str]:
        """Get the last processed event ID for a user."""
        return self.redis.client.get(f"rt:cursor:{user_id}:{stream}")

    def replay_for_user(
        self,
        user_id: str,
        stream: str,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Replay events for a reconnecting user.

        Returns events since the user's last cursor.
        """
        cursor = self.get_cursor(user_id, stream)

        if cursor:
            events = self.stream.replay(stream, from_id=cursor, count=limit)
        else:
            # No cursor — replay recent events
            events = self.stream.replay(stream, from_id="0", count=limit)

        # Update cursor
        if events:
            self.save_cursor(user_id, stream, events[-1]["id"])

        return events

    def replay_missed_events(
        self,
        user_id: str,
        streams: List[str],
        limit: int = 100,
    ) -> Dict[str, List[Dict]]:
        """Replay missed events across multiple streams."""
        result = {}

        for stream in streams:
            events = self.replay_for_user(user_id, stream, limit)
            if events:
                result[stream] = events

        return result


# --- Distributed Presence (Redis-backed) ---

class DistributedPresenceStore:
    """
    Redis-backed presence store.

    Stores presence state in Redis so it's shared
    across all instances.
    """

    def __init__(self):
        self.redis = redis_service

    def set_online(self, user_id: str, tenant_id: str, metadata: Dict = None) -> None:
        """Mark user as online."""
        data = {
            "status": "online",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
        }
        if metadata:
            data["metadata"] = json.dumps(metadata)

        self.redis.client.hset(
            f"{PRESENCE_KEY}{user_id}",
            mapping=data,
        )
        self.redis.client.expire(f"{PRESENCE_KEY}{user_id}", PRESENCE_TTL)

        # Add to tenant online set
        self.redis.client.sadd(f"{PRESENCE_KEY}tenant:{tenant_id}", user_id)

    def set_offline(self, user_id: str, tenant_id: str) -> None:
        """Mark user as offline."""
        self.redis.client.delete(f"{PRESENCE_KEY}{user_id}")
        self.redis.client.srem(f"{PRESENCE_KEY}tenant:{tenant_id}", user_id)

    def heartbeat(self, user_id: str) -> None:
        """Update presence heartbeat."""
        self.redis.client.hset(
            f"{PRESENCE_KEY}{user_id}",
            "last_seen",
            datetime.now(timezone.utc).isoformat(),
        )
        self.redis.client.expire(f"{PRESENCE_KEY}{user_id}", PRESENCE_TTL)

    def get_presence(self, user_id: str) -> Optional[Dict]:
        """Get presence for a user."""
        data = self.redis.client.hgetall(f"{PRESENCE_KEY}{user_id}")
        if data:
            return {
                "user_id": user_id,
                "status": data.get("status"),
                "last_seen": data.get("last_seen"),
                "tenant_id": data.get("tenant_id"),
            }
        return None

    def get_online_users(self, tenant_id: str) -> List[str]:
        """Get all online users for a tenant."""
        return list(self.redis.client.smembers(f"{PRESENCE_KEY}tenant:{tenant_id}"))

    def get_online_count(self, tenant_id: str) -> int:
        """Get count of online users."""
        return self.redis.client.scard(f"{PRESENCE_KEY}tenant:{tenant_id}")

    def cleanup_stale(self, tenant_id: str) -> int:
        """Remove stale presence entries."""
        online_users = self.get_online_users(tenant_id)
        removed = 0

        for user_id in online_users:
            presence = self.get_presence(user_id)
            if not presence:
                self.redis.client.srem(f"{PRESENCE_KEY}tenant:{tenant_id}", user_id)
                removed += 1

        return removed


# --- Distributed Event Bus ---

class DistributedEventBus:
    """
    High-level event bus for distributed realtime.

    Combines:
    - Durable event streaming
    - Event replay
    - Presence tracking
    - Sticky sessions
    """

    def __init__(self, node_id: str = None):
        self.node = DistributedNode(node_id)
        self.stream = DurableEventStream()
        self.replay = EventReplayManager(self.stream)
        self.presence = DistributedPresenceStore()
        self.sessions = StickySessionManager(self.node)

        # Register this node
        self.node.register()

    def emit(
        self,
        event_type: str,
        data: Dict[str, Any],
        tenant_id: str = None,
        target: str = None,
        stream: str = "default",
    ) -> str:
        """
        Emit an event to the distributed bus.

        Args:
            event_type: Type of event
            data: Event data
            tenant_id: Optional tenant scoping
            target: Optional target
            stream: Stream name

        Returns:
            Event ID
        """
        return self.stream.publish(stream, event_type, data, tenant_id, target)

    def on_connect(self, user_id: str, tenant_id: str) -> Dict:
        """
        Handle user connection.

        Returns:
            Dict with missed events and presence info
        """
        # Set online
        self.presence.set_online(user_id, tenant_id)

        # Get missed events
        missed = self.replay.replay_missed_events(
            user_id,
            ["notifications", "bookings", "system"],
            limit=50,
        )

        return {
            "user_id": user_id,
            "missed_events": missed,
            "online_count": self.presence.get_online_count(tenant_id),
        }

    def on_disconnect(self, user_id: str, tenant_id: str) -> None:
        """Handle user disconnection."""
        self.presence.set_offline(user_id, tenant_id)

    def heartbeat(self, user_id: str) -> None:
        """Process heartbeat from user."""
        self.presence.heartbeat(user_id)
        self.node.heartbeat()

    def get_status(self) -> Dict:
        """Get distributed system status."""
        return {
            "node_id": self.node.node_id,
            "active_nodes": len(self.node.get_active_nodes()),
            "streams": {
                "notifications": self.stream.get_stream_info("notifications"),
                "bookings": self.stream.get_stream_info("bookings"),
                "system": self.stream.get_stream_info("system"),
            },
        }

    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.node.deregister()
