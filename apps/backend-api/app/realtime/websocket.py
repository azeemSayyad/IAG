"""
WebSocket Manager (Step 20.1 & 20.2)

Realtime updates for:
- Bookings
- Notifications
- Calendar changes
- Dashboard updates
- Lead updates
- AI responses

Uses Socket.IO for WebSocket communication with JWT authentication.
"""

import json
from typing import Dict, List, Optional, Set
from datetime import datetime, timezone
from uuid import UUID

import socketio

from app.core.security import decode_token
from app.core.database import get_db
from app.core.config import settings
from app.models.user import User


# Cross-process Socket.IO via a Redis manager so events emitted from other
# processes (e.g. Celery workers sending outbound SMS) reach browsers connected
# to the web server. Falls back to in-memory single-node mode if Redis is down.
#
# IMPORTANT: AsyncRedisManager() does NOT raise when Redis is unreachable (it
# connects lazily), and with a Redis manager EVERY emit — even to a client in
# THIS process — is routed through Redis pub/sub. So if Redis is down, realtime
# silently stops delivering entirely. We therefore PROBE Redis first and only
# use the Redis manager when it actually answers; otherwise we use the in-memory
# manager (single-process delivery), which is correct for local dev and a
# web-only deploy.
_client_manager = None
try:
    import redis as _redis_probe
    _probe = _redis_probe.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
    )
    _probe.ping()
    try:
        _probe.close()
    except Exception:
        pass
    _client_manager = socketio.AsyncRedisManager(settings.REDIS_URL)
except Exception:
    _client_manager = None  # in-memory fallback (single-process delivery)

# Create Socket.IO server
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    client_manager=_client_manager,
    logger=False,
    engineio_logger=False,
)


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        # tenant_id -> set of sid
        self.tenant_connections: Dict[str, Set[str]] = {}
        # user_id -> sid
        self.user_connections: Dict[str, str] = {}
        # sid -> user info
        self.connection_info: Dict[str, Dict] = {}

    async def connect(self, sid: str, user_id: str, tenant_id: str, role: str):
        """Register a new connection."""
        # Add to tenant connections
        if tenant_id not in self.tenant_connections:
            self.tenant_connections[tenant_id] = set()
        self.tenant_connections[tenant_id].add(sid)

        # Map user to connection
        self.user_connections[user_id] = sid

        # Store connection info
        self.connection_info[sid] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }

        # Join tenant room
        await sio.enter_room(sid, f"tenant:{tenant_id}")

        # Join role-specific room
        await sio.enter_room(sid, f"tenant:{tenant_id}:{role}")

        # Per-user room for EVERY role — enables reliable realtime delivery to a
        # single user across all their tabs (in-app DM / admin↔agent chat). The
        # legacy user_connections SID map only tracks one tab; this room doesn't.
        await sio.enter_room(sid, f"user:{user_id}")

        # If agent, join agent room
        if role == "agent":
            await sio.enter_room(sid, f"agent:{user_id}")

    async def disconnect(self, sid: str):
        """Remove a connection."""
        info = self.connection_info.get(sid)
        if not info:
            return

        user_id = info["user_id"]
        tenant_id = info["tenant_id"]
        role = info["role"]

        # Remove from tenant connections
        if tenant_id in self.tenant_connections:
            self.tenant_connections[tenant_id].discard(sid)

        # Remove user mapping
        if user_id in self.user_connections:
            del self.user_connections[user_id]

        # Remove connection info
        del self.connection_info[sid]

        # Leave rooms
        await sio.leave_room(sid, f"tenant:{tenant_id}")
        await sio.leave_room(sid, f"tenant:{tenant_id}:{role}")
        await sio.leave_room(sid, f"user:{user_id}")
        if role == "agent":
            await sio.leave_room(sid, f"agent:{user_id}")

    def get_online_users(self, tenant_id: str) -> List[Dict]:
        """Get list of online users for a tenant."""
        connections = self.tenant_connections.get(tenant_id, set())
        users = []
        for sid in connections:
            info = self.connection_info.get(sid)
            if info:
                users.append({
                    "user_id": info["user_id"],
                    "role": info["role"],
                    "connected_at": info["connected_at"],
                })
        return users

    def is_user_online(self, user_id: str) -> bool:
        """Check if a user is online."""
        return user_id in self.user_connections


# Global connection manager
manager = ConnectionManager()


# Socket.IO event handlers
@sio.event
async def connect(sid, environ, auth):
    """
    Handle new WebSocket connection.

    Authentication:
    - Requires JWT token in auth payload
    - Validates token and extracts user info
    - Joins appropriate rooms based on role
    """
    if not auth or not auth.get("token"):
        raise socketio.exceptions.ConnectionRefusedError("Authentication required: missing token")

    try:
        # Validate JWT token
        token = auth["token"]
        payload = decode_token(token)

        if not payload or payload.get("type") != "access":
            raise socketio.exceptions.ConnectionRefusedError("Invalid token type")

        user_id = payload.get("sub")
        if not user_id:
            raise socketio.exceptions.ConnectionRefusedError("Invalid token: missing user ID")

        # Get user from database
        db = next(get_db())
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user or user.deleted_at:
                raise socketio.exceptions.ConnectionRefusedError("User not found")

            tenant_id = str(user.tenant_id)
            role = user.role
            user_id = str(user.id)

        finally:
            db.close()

    except socketio.exceptions.ConnectionRefusedError:
        raise
    except Exception as e:
        raise socketio.exceptions.ConnectionRefusedError(f"Authentication failed: {str(e)}")

    await manager.connect(sid, user_id, tenant_id, role)

    # Send connection confirmation
    await sio.emit("connected", {
        "sid": sid,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, room=sid)


@sio.event
async def disconnect(sid):
    """Handle WebSocket disconnection."""
    await manager.disconnect(sid)


@sio.event
async def join_room(sid, data):
    """Join a specific room."""
    room = data.get("room")
    if room:
        await sio.enter_room(sid, room)
        await sio.emit("room_joined", {"room": room}, room=sid)


@sio.event
async def leave_room(sid, data):
    """Leave a specific room."""
    room = data.get("room")
    if room:
        await sio.leave_room(sid, room)
        await sio.emit("room_left", {"room": room}, room=sid)


# Notification emitters
async def emit_to_tenant(tenant_id: str, event: str, data: Dict):
    """Emit an event to all connections in a tenant."""
    await sio.emit(event, data, room=f"tenant:{tenant_id}")


async def emit_to_user(user_id: str, event: str, data: Dict):
    """Emit an event to a specific user (legacy single-tab SID lookup)."""
    sid = manager.user_connections.get(user_id)
    if sid:
        await sio.emit(event, data, room=sid)


async def emit_to_user_room(user_id: str, event: str, data: Dict):
    """Emit to a user's per-user room (all roles, all tabs). Preferred for
    direct/in-app delivery — robust to multi-tab and the SID map."""
    await sio.emit(event, data, room=f"user:{user_id}")


async def emit_to_agent(agent_id: str, event: str, data: Dict):
    """Emit an event to a specific agent."""
    await sio.emit(event, data, room=f"agent:{agent_id}")


async def emit_to_role(tenant_id: str, role: str, event: str, data: Dict):
    """Emit an event to all users with a specific role in a tenant."""
    await sio.emit(event, data, room=f"tenant:{tenant_id}:{role}")
