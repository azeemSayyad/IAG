"""
Agent Presence System (Phase 39.1-39.5)

Tracks live agent status:
- current_status: online, busy, away, offline
- last_seen: Last heartbeat timestamp
- active_call: Current call info
- occupancy_score: Real-time utilization (0-1)

Storage:
- Redis: Real-time presence data (fast reads/writes)
- PostgreSQL: Historical presence logs

Status Transitions:
- online → busy (call started)
- busy → online (call ended)
- online → away (no heartbeat for 30s)
- away → offline (no heartbeat for 5min)
- offline → online (heartbeat received)
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.redis import redis_service

try:
    from app.realtime.websocket import socketio_server
except ImportError:
    socketio_server = None

logger = logging.getLogger(__name__)


# Status constants
STATUS_ONLINE = "online"
STATUS_BUSY = "busy"
STATUS_AWAY = "away"
STATUS_OFFLINE = "offline"

# Timing constants (seconds)
HEARTBEAT_INTERVAL = 10       # Expected heartbeat every 10s
HEARTBEAT_TIMEOUT = 30        # Mark away after 30s no heartbeat
OFFLINE_TIMEOUT = 300          # Mark offline after 5min no heartbeat
CALL_TIMEOUT = 3600            # Max call duration (1 hour)

# Redis key prefixes
PRESENCE_KEY = "presence:agent:"
HEARTBEAT_KEY = "heartbeat:agent:"
CALL_KEY = "call:agent:"
OCCUPANCY_KEY = "occupancy:agent:"


class AgentPresence:
    """Represents an agent's current presence state."""

    def __init__(
        self,
        agent_id: str,
        status: str = STATUS_OFFLINE,
        last_seen: Optional[datetime] = None,
        active_call: Optional[Dict] = None,
        occupancy_score: float = 0.0,
        metadata: Optional[Dict] = None,
    ):
        self.agent_id = agent_id
        self.status = status
        self.last_seen = last_seen or datetime.now(timezone.utc)
        self.active_call = active_call
        self.occupancy_score = occupancy_score
        self.metadata = metadata or {}

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "active_call": self.active_call,
            "occupancy_score": round(self.occupancy_score, 3),
            "metadata": self.metadata,
        }

    def is_available(self) -> bool:
        """Check if agent can take a new call."""
        return self.status == STATUS_ONLINE and self.active_call is None


class PresenceManager:
    """
    Manages agent presence state.

    Features:
    - Heartbeat tracking
    - Status management
    - Call tracking
    - Occupancy calculation
    - Auto-idle detection
    """

    def __init__(self, db: Session = None):
        self.db = db
        self.redis = redis_service

    # --- Heartbeat ---

    def heartbeat(self, agent_id: str, metadata: Optional[Dict] = None) -> AgentPresence:
        """
        Process a heartbeat from an agent.

        Called every 10 seconds by the frontend.

        Args:
            agent_id: Agent ID
            metadata: Optional metadata (page, activity, etc.)

        Returns:
            Updated AgentPresence
        """
        now = datetime.now(timezone.utc)

        # Update heartbeat timestamp
        self.redis.client.set(
            f"{HEARTBEAT_KEY}{agent_id}",
            now.isoformat(),
            ex=HEARTBEAT_TIMEOUT * 2,
        )

        # Get current presence
        presence = self.get_presence(agent_id)

        # Update status based on current state
        if presence.status == STATUS_OFFLINE or presence.status == STATUS_AWAY:
            presence.status = STATUS_ONLINE
            self._broadcast_status_change(agent_id, STATUS_ONLINE)

        presence.last_seen = now
        if metadata:
            presence.metadata.update(metadata)

        # Save to Redis
        self._save_presence(presence)

        return presence

    def get_last_heartbeat(self, agent_id: str) -> Optional[datetime]:
        """Get the last heartbeat timestamp for an agent."""
        data = self.redis.client.get(f"{HEARTBEAT_KEY}{agent_id}")
        if data:
            try:
                return datetime.fromisoformat(data)
            except (ValueError, TypeError):
                pass
        return None

    # --- Status Management ---

    def get_presence(self, agent_id: str) -> AgentPresence:
        """Get current presence for an agent."""
        data = self.redis.client.get(f"{PRESENCE_KEY}{agent_id}")
        if data:
            try:
                d = json.loads(data)
                return AgentPresence(
                    agent_id=agent_id,
                    status=d.get("status", STATUS_OFFLINE),
                    last_seen=datetime.fromisoformat(d["last_seen"]) if d.get("last_seen") else None,
                    active_call=d.get("active_call"),
                    occupancy_score=d.get("occupancy_score", 0),
                    metadata=d.get("metadata", {}),
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        return AgentPresence(agent_id=agent_id, status=STATUS_OFFLINE)

    def set_status(self, agent_id: str, status: str) -> AgentPresence:
        """
        Set agent status.

        Args:
            agent_id: Agent ID
            status: New status (online, busy, away, offline)

        Returns:
            Updated AgentPresence
        """
        presence = self.get_presence(agent_id)
        old_status = presence.status
        presence.status = status
        presence.last_seen = datetime.now(timezone.utc)

        self._save_presence(presence)

        if old_status != status:
            self._broadcast_status_change(agent_id, status)

        return presence

    def get_all_presence(self, tenant_id: str) -> List[AgentPresence]:
        """Get presence for all agents in a tenant."""
        from app.models.agent import Agent

        if not self.db:
            return []
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        presences = []
        for agent in agents:
            presence = self.get_presence(str(agent.id))
            presences.append(presence)

        return presences

    def get_online_agents(self, tenant_id: str) -> List[AgentPresence]:
        """Get all online agents in a tenant."""
        all_presence = self.get_all_presence(tenant_id)
        return [p for p in all_presence if p.status in (STATUS_ONLINE, STATUS_BUSY)]

    def get_available_agents(self, tenant_id: str) -> List[AgentPresence]:
        """Get all agents available for calls."""
        all_presence = self.get_all_presence(tenant_id)
        return [p for p in all_presence if p.is_available()]

    # --- Call Tracking ---

    def start_call(self, agent_id: str, call_info: Dict) -> AgentPresence:
        """
        Mark agent as in a call.

        Args:
            agent_id: Agent ID
            call_info: Dict with lead_id, appointment_id, etc.

        Returns:
            Updated AgentPresence
        """
        presence = self.get_presence(agent_id)
        presence.status = STATUS_BUSY
        presence.active_call = {
            **call_info,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        self._save_presence(presence)
        self._broadcast_status_change(agent_id, STATUS_BUSY)

        return presence

    def end_call(self, agent_id: str) -> AgentPresence:
        """
        Mark agent as done with call.

        Args:
            agent_id: Agent ID

        Returns:
            Updated AgentPresence
        """
        presence = self.get_presence(agent_id)
        presence.status = STATUS_ONLINE
        presence.active_call = None

        self._save_presence(presence)
        self._broadcast_status_change(agent_id, STATUS_ONLINE)

        return presence

    def get_active_call(self, agent_id: str) -> Optional[Dict]:
        """Get current call info for an agent."""
        presence = self.get_presence(agent_id)
        return presence.active_call

    # --- Occupancy ---

    def update_occupancy(self, agent_id: str, score: float) -> None:
        """Update occupancy score for an agent."""
        presence = self.get_presence(agent_id)
        presence.occupancy_score = min(max(score, 0), 1.0)
        self._save_presence(presence)

    def calculate_occupancy(self, agent_id: str, window_hours: int = 8) -> float:
        """
        Calculate real-time occupancy score.

        Occupancy = (time in calls) / (total time) over window.

        Args:
            agent_id: Agent ID
            window_hours: Time window in hours

        Returns:
            Occupancy score (0-1)
        """
        from app.models.appointment import Appointment

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=window_hours)

        if not self.db:
            return 0.0
        # Get appointments in window
        appointments = self.db.query(Appointment).filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= window_start,
            Appointment.status.in_(["confirmed", "completed"]),
        ).all()

        if not appointments:
            return 0.0

        # Calculate total call time
        total_call_seconds = 0
        for appt in appointments:
            if appt.call_duration_seconds:
                total_call_seconds += appt.call_duration_seconds
            elif appt.start_time and appt.end_time:
                duration = (appt.end_time - appt.start_time).total_seconds()
                total_call_seconds += duration

        # Calculate window seconds
        window_seconds = window_hours * 3600

        occupancy = min(total_call_seconds / window_seconds, 1.0)

        # Update stored occupancy
        self.update_occupancy(agent_id, occupancy)

        return occupancy

    # --- Auto-Idle Detection ---

    def check_timeouts(self, tenant_id: str) -> List[Dict]:
        """
        Check for agents that have timed out.

        Called periodically (every 30s) to detect idle agents.

        Returns:
            List of status changes
        """
        now = datetime.now(timezone.utc)
        changes = []

        if not self.db:
            return []
        agents = self.db.query(
            __import__("app.models.agent", fromlist=["Agent"]).Agent
        ).filter(
            __import__("app.models.agent", fromlist=["Agent"]).Agent.tenant_id == tenant_id,
            __import__("app.models.agent", fromlist=["Agent"]).Agent.status == "active",
        ).all()

        for agent in agents:
            agent_id = str(agent.id)
            presence = self.get_presence(agent_id)

            if presence.status == STATUS_OFFLINE:
                continue

            last_heartbeat = self.get_last_heartbeat(agent_id)
            if not last_heartbeat:
                continue

            elapsed = (now - last_heartbeat).total_seconds()

            # Check for offline timeout
            if elapsed > OFFLINE_TIMEOUT and presence.status != STATUS_OFFLINE:
                old_status = presence.status
                presence.status = STATUS_OFFLINE
                presence.active_call = None
                self._save_presence(presence)
                self._broadcast_status_change(agent_id, STATUS_OFFLINE)
                changes.append({
                    "agent_id": agent_id,
                    "old_status": old_status,
                    "new_status": STATUS_OFFLINE,
                    "reason": "heartbeat_timeout",
                })

            # Check for away timeout
            elif elapsed > HEARTBEAT_TIMEOUT and presence.status == STATUS_ONLINE:
                presence.status = STATUS_AWAY
                self._save_presence(presence)
                self._broadcast_status_change(agent_id, STATUS_AWAY)
                changes.append({
                    "agent_id": agent_id,
                    "old_status": STATUS_ONLINE,
                    "new_status": STATUS_AWAY,
                    "reason": "heartbeat_miss",
                })

            # Check for call timeout
            if presence.active_call:
                started_at = presence.active_call.get("started_at")
                if started_at:
                    try:
                        call_start = datetime.fromisoformat(started_at)
                        if (now - call_start).total_seconds() > CALL_TIMEOUT:
                            presence.active_call = None
                            presence.status = STATUS_ONLINE
                            self._save_presence(presence)
                            changes.append({
                                "agent_id": agent_id,
                                "old_status": STATUS_BUSY,
                                "new_status": STATUS_ONLINE,
                                "reason": "call_timeout",
                            })
                    except (ValueError, TypeError):
                        pass

        return changes

    # --- Internal Methods ---

    def _save_presence(self, presence: AgentPresence) -> None:
        """Save presence to Redis."""
        data = {
            "status": presence.status,
            "last_seen": presence.last_seen.isoformat() if presence.last_seen else None,
            "active_call": presence.active_call,
            "occupancy_score": presence.occupancy_score,
            "metadata": presence.metadata,
        }
        self.redis.client.set(
            f"{PRESENCE_KEY}{presence.agent_id}",
            json.dumps(data),
            ex=OFFLINE_TIMEOUT * 2,
        )

    def _broadcast_status_change(self, agent_id: str, new_status: str) -> None:
        """Broadcast status change via WebSocket."""
        try:
            if not self.db:
                return
            if socketio_server:
                # Get agent's tenant
                from app.models.agent import Agent
                agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
                if agent:
                    socketio_server.emit(
                        "agent:status_change",
                        {
                            "agent_id": agent_id,
                            "status": new_status,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        room=f"tenant:{agent.tenant_id}",
                    )
        except Exception as e:
            logger.warning(f"Failed to broadcast status change: {e}")

    # --- Statistics ---

    def get_tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Get presence statistics for a tenant."""
        all_presence = self.get_all_presence(tenant_id)

        total = len(all_presence)
        online = sum(1 for p in all_presence if p.status == STATUS_ONLINE)
        busy = sum(1 for p in all_presence if p.status == STATUS_BUSY)
        away = sum(1 for p in all_presence if p.status == STATUS_AWAY)
        offline = sum(1 for p in all_presence if p.status == STATUS_OFFLINE)

        avg_occupancy = (
            sum(p.occupancy_score for p in all_presence) / total
            if total > 0 else 0
        )

        return {
            "tenant_id": tenant_id,
            "total_agents": total,
            "online": online,
            "busy": busy,
            "away": away,
            "offline": offline,
            "available": online - busy,
            "avg_occupancy": round(avg_occupancy, 3),
            "utilization_rate": round((online + busy) / total, 3) if total > 0 else 0,
        }
