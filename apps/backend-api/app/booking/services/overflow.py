"""
Overflow Handling (Step 17.6)

When no slots are available:
1. Waitlist — Add lead to waitlist for notification when slot opens
2. Nearest Alternative — Suggest closest available time
3. Overflow Queue — Route to next available agent

Prevents lost bookings due to full schedules.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.appointment import Appointment
from app.models.agent import Agent
from app.models.agent_availability import AgentAvailability
from app.models.lead import Lead
from app.core.redis import RedisService
from app.core.audit import log_ai_action


redis_service = RedisService()


def add_to_waitlist(
    tenant_id: str,
    lead_id: str,
    preferred_time: datetime,
    preferred_agent_id: Optional[str] = None,
    callback_url: Optional[str] = None,
) -> Dict:
    """
    Add a lead to the booking waitlist.

    When a slot opens, the lead will be notified.
    """
    waitlist_entry = {
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "preferred_time": preferred_time.isoformat(),
        "preferred_agent_id": preferred_agent_id,
        "callback_url": callback_url,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "status": "waiting",
    }

    # Store in Redis sorted set with preferred_time as score
    key = f"waitlist:{tenant_id}"
    redis_service.client.zadd(
        key,
        {f"{lead_id}:{preferred_time.isoformat()}": preferred_time.timestamp()}
    )

    # Store details
    detail_key = f"waitlist:{tenant_id}:{lead_id}"
    redis_service.set_cache(detail_key, waitlist_entry, ttl=86400 * 7)  # 7 days

    log_ai_action(
        tenant_id=tenant_id,
        action="waitlist_added",
        resource_type="lead",
        resource_id=lead_id,
        details={"preferred_time": preferred_time.isoformat()},
    )

    return {
        "success": True,
        "message": "Added to waitlist",
        "position": redis_service.client.zrank(key, f"{lead_id}:{preferred_time.isoformat()}"),
    }


def remove_from_waitlist(
    tenant_id: str,
    lead_id: str,
) -> Dict:
    """Remove a lead from the waitlist."""
    key = f"waitlist:{tenant_id}"

    # Find and remove all entries for this lead
    members = redis_service.client.zrange(key, 0, -1)
    removed = 0
    for member in members:
        if member.decode().startswith(f"{lead_id}:"):
            redis_service.client.zrem(key, member)
            removed += 1

    # Remove detail key
    detail_key = f"waitlist:{tenant_id}:{lead_id}"
    redis_service.delete_cache(detail_key)

    return {"success": True, "removed": removed}


def get_waitlist(
    tenant_id: str,
) -> List[Dict]:
    """Get all waitlist entries for a tenant."""
    key = f"waitlist:{tenant_id}"
    members = redis_service.client.zrange(key, 0, -1, withscores=True)

    entries = []
    for member, score in members:
        parts = member.decode().split(":", 1)
        if len(parts) == 2:
            lead_id, preferred_time = parts
            detail_key = f"waitlist:{tenant_id}:{lead_id}"
            details = redis_service.get_cache(detail_key)
            if details:
                entries.append(details)

    return entries


def check_waitlist_for_slot(
    db: Session,
    tenant_id: str,
    agent_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> Optional[Dict]:
    """
    Check if any waitlist entries match a newly available slot.

    Called when a cancellation opens up a slot.
    """
    waitlist = get_waitlist(tenant_id)

    for entry in waitlist:
        if entry.get("status") != "waiting":
            continue

        preferred_time = datetime.fromisoformat(entry["preferred_time"])

        # Check if slot is close to preferred time (within 2 hours)
        time_diff = abs((start_time - preferred_time).total_seconds()) / 3600
        if time_diff <= 2:
            # Check if preferred agent matches (or no preference)
            if entry.get("preferred_agent_id") and entry["preferred_agent_id"] != str(agent_id):
                continue

            return {
                "lead_id": entry["lead_id"],
                "preferred_time": preferred_time.isoformat(),
                "available_slot": {
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "agent_id": str(agent_id),
                },
            }

    return None


def find_nearest_alternative(
    db: Session,
    tenant_id: UUID,
    preferred_time: datetime,
    preferred_agent_id: Optional[UUID] = None,
    max_days_ahead: int = 7,
) -> Optional[Dict]:
    """
    Find the nearest available slot to the preferred time.

    Searches forward from preferred_time up to max_days_ahead days.
    """
    search_start = preferred_time
    search_end = preferred_time + timedelta(days=max_days_ahead)

    # Get all active agents (or specific agent)
    agent_query = db.query(Agent).filter(
        Agent.tenant_id == tenant_id,
        Agent.status == "active",
    )
    if preferred_agent_id:
        agent_query = agent_query.filter(Agent.id == preferred_agent_id)

    agents = agent_query.all()
    if not agents:
        return None

    best_slot = None
    best_diff = float('inf')

    for agent in agents:
        # Get availability
        availabilities = (
            db.query(AgentAvailability)
            .filter(
                AgentAvailability.agent_id == agent.id,
                AgentAvailability.start_time < search_end,
                AgentAvailability.end_time > search_start,
                AgentAvailability.availability_status == "available",
            )
            .all()
        )

        for avail in availabilities:
            # Get booked slots
            booked = (
                db.query(Appointment)
                .filter(
                    Appointment.agent_id == agent.id,
                    Appointment.start_time < avail.end_time,
                    Appointment.end_time > avail.start_time,
                    Appointment.status.in_(["pending", "confirmed"]),
                )
                .order_by(Appointment.start_time)
                .all()
            )

            # Find first available 15-min slot
            current = max(avail.start_time, search_start)
            for appt in booked:
                # Check gap before this appointment
                if appt.start_time > current:
                    gap = (appt.start_time - current).total_seconds() / 60
                    if gap >= 15:
                        slot_end = current + timedelta(minutes=15)
                        diff = abs((current - preferred_time).total_seconds()) / 60
                        if diff < best_diff:
                            best_diff = diff
                            best_slot = {
                                "start_time": current.isoformat(),
                                "end_time": slot_end.isoformat(),
                                "agent_id": str(agent.id),
                                "minutes_from_preferred": int(diff),
                            }
                current = max(current, appt.end_time)

            # Check gap after last appointment
            if current < avail.end_time:
                gap = (avail.end_time - current).total_seconds() / 60
                if gap >= 15:
                    slot_end = current + timedelta(minutes=15)
                    diff = abs((current - preferred_time).total_seconds()) / 60
                    if diff < best_diff:
                        best_diff = diff
                        best_slot = {
                            "start_time": current.isoformat(),
                            "end_time": slot_end.isoformat(),
                            "agent_id": str(agent.id),
                            "minutes_from_preferred": int(diff),
                        }

    return best_slot


def get_overflow_queue(
    tenant_id: str,
) -> List[Dict]:
    """Get leads waiting in overflow queue."""
    key = f"overflow:{tenant_id}"
    members = redis_service.client.lrange(key, 0, -1)

    entries = []
    for member in members:
        import json
        try:
            entry = json.loads(member)
            entries.append(entry)
        except:
            continue

    return entries


def add_to_overflow_queue(
    tenant_id: str,
    lead_id: str,
    preferred_time: datetime,
) -> Dict:
    """Add lead to overflow queue for next available slot."""
    import json

    key = f"overflow:{tenant_id}"
    entry = json.dumps({
        "lead_id": lead_id,
        "preferred_time": preferred_time.isoformat(),
        "added_at": datetime.now(timezone.utc).isoformat(),
    })

    redis_service.client.rpush(key, entry)
    redis_service.client.expire(key, 86400 * 3)  # 3 days

    return {"success": True, "message": "Added to overflow queue"}


def process_overflow_queue(
    db: Session,
    tenant_id: str,
) -> Dict:
    """
    Process overflow queue when slots become available.

    Called periodically or after cancellations.
    """
    import json

    key = f"overflow:{tenant_id}"
    processed = 0
    booked = 0

    while True:
        entry_raw = redis_service.client.lpop(key)
        if not entry_raw:
            break

        try:
            entry = json.loads(entry_raw)
            processed += 1

            lead_id = entry["lead_id"]
            preferred_time = datetime.fromisoformat(entry["preferred_time"])

            # Try to find alternative
            alternative = find_nearest_alternative(
                db=db,
                tenant_id=tenant_id,
                preferred_time=preferred_time,
            )

            if alternative:
                # TODO: Notify lead about available slot
                booked += 1
            else:
                # Put back in queue
                redis_service.client.rpush(key, entry_raw)

        except Exception:
            continue

    return {"processed": processed, "booked": booked}
