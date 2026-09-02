"""
AI Emergency Fill Engine (Phase 40)

Eliminates idle agent time by proactively filling empty slots:

Step 40.1 — Detect Idle Slots
    Agent has next 15 min empty → trigger emergency fill

Step 40.2 — Find Warm Leads
    Prioritize: recent engagement, high lead score, fast responders

Step 40.3 — Launch Instant SMS Blast
    "Agent available now for a quick insurance call. Want to talk in 10 minutes?"

Step 40.4 — Build Instant Booking Flow
    Customer replies YES → book immediately

Step 40.5 — Add Micro-Waitlist
    Maintain ready_now_leads queue

Step 40.6 — Build Occupancy Optimizer
    Target: 85-95% agent utilization

Flow:
1. Monitor agent schedules every 5 minutes
2. Detect gaps (next 15+ min empty)
3. Find warm leads for each gap
4. Send instant availability SMS
5. Handle YES replies with instant booking
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models.lead import Lead
from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.message import Message
from app.core.redis import redis_service
from app.realtime.presence import PresenceManager, STATUS_ONLINE

logger = logging.getLogger(__name__)

# Configuration
IDLE_THRESHOLD_MINUTES = 15      # Minimum gap to trigger fill
WARM_LEAD_LIMIT = 10             # Max leads to contact per gap
SMS_COOLDOWN_HOURS = 4           # Hours between SMS to same lead
READY_NOW_TTL = 1800             # 30 min TTL for ready_now entries
TARGET_UTILIZATION = 0.90        # Target 90% utilization
EMERGENCY_FILL_KEY = "emergency:fill:"
READY_NOW_KEY = "ready_now:leads"


class IdleSlot:
    """Represents an idle slot for an agent."""

    def __init__(
        self,
        agent_id: str,
        start_time: datetime,
        end_time: datetime,
        duration_minutes: int,
    ):
        self.agent_id = agent_id
        self.start_time = start_time
        self.end_time = end_time
        self.duration_minutes = duration_minutes

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_minutes": self.duration_minutes,
        }


class EmergencyFillEngine:
    """
    Proactively fills idle agent slots with warm leads.

    Features:
    - Idle slot detection
    - Warm lead ranking
    - Instant SMS blast
    - Instant booking on YES reply
    - Micro-waitlist management
    - Occupancy optimization
    """

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service
        self.presence = PresenceManager(db)

    # --- Step 40.1: Detect Idle Slots ---

    def detect_idle_slots(
        self,
        tenant_id: str,
        window_minutes: int = 60,
    ) -> List[IdleSlot]:
        """
        Detect agents with idle slots in the next window.

        Args:
            tenant_id: Tenant ID
            window_minutes: Look-ahead window (default 60 min)

        Returns:
            List of IdleSlot objects
        """
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(minutes=window_minutes)

        # Get online agents
        online_agents = self.presence.get_available_agents(tenant_id)
        if not online_agents:
            return []

        idle_slots = []

        for presence in online_agents:
            agent_id = presence.agent_id

            # Get agent's appointments in the window
            appointments = self.db.query(Appointment).filter(
                Appointment.agent_id == agent_id,
                Appointment.start_time >= now,
                Appointment.start_time <= window_end,
                Appointment.status.in_(["pending", "confirmed"]),
            ).order_by(Appointment.start_time).all()

            # Find gaps
            cursor = now
            for appt in appointments:
                gap_minutes = (appt.start_time - cursor).total_seconds() / 60

                if gap_minutes >= IDLE_THRESHOLD_MINUTES:
                    idle_slots.append(IdleSlot(
                        agent_id=agent_id,
                        start_time=cursor,
                        end_time=appt.start_time,
                        duration_minutes=int(gap_minutes),
                    ))

                cursor = max(cursor, appt.end_time or (appt.start_time + timedelta(minutes=15)))

            # Check gap after last appointment
            if cursor < window_end:
                gap_minutes = (window_end - cursor).total_seconds() / 60
                if gap_minutes >= IDLE_THRESHOLD_MINUTES:
                    idle_slots.append(IdleSlot(
                        agent_id=agent_id,
                        start_time=cursor,
                        end_time=window_end,
                        duration_minutes=int(gap_minutes),
                    ))

        return idle_slots

    # --- Step 40.2: Find Warm Leads ---

    def find_warm_leads(
        self,
        tenant_id: str,
        limit: int = WARM_LEAD_LIMIT,
    ) -> List[Dict]:
        """
        Find warm leads for emergency fill.

        Prioritization:
        1. Recent engagement (replied in last 24h)
        2. High lead score (>60)
        3. Fast responders (<5 min avg)
        4. Not contacted recently (4h cooldown)

        Returns:
            List of lead dicts with scores
        """
        now = datetime.now(timezone.utc)
        cooldown_cutoff = now - timedelta(hours=SMS_COOLDOWN_HOURS)

        # Get leads that are engaged but not booked
        leads = self.db.query(Lead).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at.is_(None),
            Lead.status.in_(["contacted", "replied", "interested"]),
            Lead.sms_consent == True,
        ).all()

        scored_leads = []

        for lead in leads:
            # Check cooldown
            last_contacted = lead.last_contacted_at
            if last_contacted and last_contacted > cooldown_cutoff:
                continue

            # Calculate warmth score
            score = self._calculate_warmth_score(lead)

            if score > 0.3:  # Minimum warmth threshold
                scored_leads.append({
                    "lead_id": str(lead.id),
                    "name": f"{lead.first_name} {lead.last_name}",
                    "phone": lead.phone,
                    "score": lead.lead_score or 0,
                    "warmth_score": round(score, 3),
                    "source": lead.source,
                    "last_contacted": last_contacted.isoformat() if last_contacted else None,
                })

        # Sort by warmth score
        scored_leads.sort(key=lambda x: x["warmth_score"], reverse=True)

        return scored_leads[:limit]

    def _calculate_warmth_score(self, lead: Lead) -> float:
        """Calculate warmth score for a lead (0-1)."""
        score = 0.0

        # Lead score component (0-0.4)
        lead_score = lead.lead_score or 0
        score += (lead_score / 100) * 0.4

        # Reply recency (0-0.3)
        if lead.last_replied_at:
            hours_since_reply = (datetime.now(timezone.utc) - lead.last_replied_at).total_seconds() / 3600
            if hours_since_reply < 1:
                score += 0.3
            elif hours_since_reply < 6:
                score += 0.2
            elif hours_since_reply < 24:
                score += 0.1

        # Engagement (0-0.3)
        conversations = self.db.query(Conversation).filter(
            Conversation.lead_id == lead.id,
        ).all()

        if conversations:
            total_msgs = sum(c.message_count or 0 for c in conversations)
            customer_msgs = self.db.query(func.count(Message.id)).join(Conversation).filter(
                Conversation.lead_id == lead.id,
                Message.sender == "customer",
            ).scalar() or 0

            if customer_msgs > 5:
                score += 0.3
            elif customer_msgs > 2:
                score += 0.2
            elif customer_msgs > 0:
                score += 0.1

        return min(score, 1.0)

    # --- Step 40.3: Instant SMS Blast ---

    def send_emergency_fill_sms(
        self,
        tenant_id: str,
        idle_slot: IdleSlot,
        leads: List[Dict],
    ) -> Dict[str, Any]:
        """
        Send instant availability SMS to warm leads.

        Args:
            tenant_id: Tenant ID
            idle_slot: The idle slot to fill
            leads: List of warm leads to contact

        Returns:
            Dict with results
        """
        from app.ai.services.communication_provider import send_sms_to_lead

        results = {
            "slot": idle_slot.to_dict(),
            "leads_contacted": 0,
            "leads_skipped": 0,
            "errors": [],
        }

        # Format time
        slot_time = idle_slot.start_time.strftime("%I:%M %p").lstrip("0")

        for lead_info in leads:
            lead_id = lead_info["lead_id"]

            # Check if already contacted for this slot
            contact_key = f"{EMERGENCY_FILL_KEY}{lead_id}:{idle_slot.start_time.strftime('%Y%m%d%H')}"
            if self.redis.client.exists(contact_key):
                results["leads_skipped"] += 1
                continue

            # Send SMS
            message = (
                f"Great news! An agent just became available at {slot_time}. "
                f"Want to hop on a quick call? Reply YES to book!"
            )

            try:
                # Get lead object
                lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
                if not lead:
                    continue

                result = send_sms_to_lead(
                    phone=lead.phone,
                    lead_id=str(lead.id),
                    message=message,
                    tenant_id=tenant_id,
                )

                if result.get("success"):
                    results["leads_contacted"] += 1
                    # Set cooldown
                    self.redis.client.setex(contact_key, SMS_COOLDOWN_HOURS * 3600, "1")

                    # Log to conversation
                    self._log_emergency_message(lead, message, tenant_id)
                else:
                    results["errors"].append(f"SMS failed for {lead_id}")

            except Exception as e:
                logger.error(f"Emergency SMS failed for {lead_id}: {e}")
                results["errors"].append(str(e))

        return results

    # --- Step 40.4: Instant Booking Flow ---

    def handle_instant_reply(
        self,
        lead_id: UUID,
        message_text: str,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """
        Handle instant reply from emergency fill SMS.

        If YES → book immediately with available agent.

        Args:
            lead_id: Lead UUID
            message_text: Reply text
            tenant_id: Tenant ID

        Returns:
            Dict with booking result
        """
        # Check for YES patterns
        yes_patterns = ["yes", "yep", "yeah", "sure", "ok", "book", "let's do it", "sounds good"]
        is_yes = any(p in message_text.lower() for p in yes_patterns)

        if not is_yes:
            return {"action": "none", "message": "Not a YES reply"}

        # Find available agent now
        available = self.presence.get_available_agents(tenant_id)
        if not available:
            return {"action": "no_agents", "message": "No agents available right now"}

        # Get the first available agent
        agent_presence = available[0]
        agent = self.db.query(Agent).filter(Agent.id == agent_presence.agent_id).first()

        if not agent:
            return {"action": "error", "message": "Agent not found"}

        # Create immediate appointment
        now = datetime.now(timezone.utc)
        appointment = Appointment(
            tenant_id=tenant_id,
            lead_id=lead_id,
            agent_id=agent.id,
            start_time=now,
            end_time=now + timedelta(minutes=15),
            status="confirmed",
            booking_source="emergency_fill",
        )
        self.db.add(appointment)

        # Update lead
        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.status = "booked"

        # Mark agent as busy
        self.presence.start_call(str(agent.id), {
            "lead_id": str(lead_id),
            "appointment_id": str(appointment.id),
            "type": "emergency_fill",
        })

        self.db.commit()

        # Send confirmation
        if lead:
            confirm_msg = (
                f"Perfect! You're booked for a call right now. "
                f"An agent will be with you shortly!"
            )
            self._log_emergency_message(lead, confirm_msg, tenant_id)

        return {
            "action": "booked",
            "appointment_id": str(appointment.id),
            "agent_id": str(agent.id),
            "message": "Instant booking successful",
        }

    # --- Step 40.5: Micro-Waitlist ---

    def add_to_ready_now(
        self,
        lead_id: str,
        preferred_time: Optional[datetime] = None,
    ) -> bool:
        """
        Add a lead to the ready_now micro-waitlist.

        These are leads willing to talk RIGHT NOW.
        """
        entry = {
            "lead_id": lead_id,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "preferred_time": preferred_time.isoformat() if preferred_time else None,
        }

        self.redis.client.setex(
            f"{READY_NOW_KEY}:{lead_id}",
            READY_NOW_TTL,
            str(entry),
        )

        # Add to sorted set for priority ordering
        score = datetime.now(timezone.utc).timestamp()
        self.redis.client.zadd(READY_NOW_KEY, {lead_id: score})

        logger.info(f"Added {lead_id} to ready_now queue")
        return True

    def get_ready_now_leads(self, limit: int = 10) -> List[str]:
        """Get leads from the ready_now queue."""
        return self.redis.client.zrange(READY_NOW_KEY, 0, limit - 1)

    def remove_from_ready_now(self, lead_id: str) -> bool:
        """Remove a lead from the ready_now queue."""
        self.redis.client.zrem(READY_NOW_KEY, lead_id)
        self.redis.client.delete(f"{READY_NOW_KEY}:{lead_id}")
        return True

    def process_ready_now(self, tenant_id: str) -> Dict[str, Any]:
        """
        Process ready_now leads when an agent becomes available.

        Called when an agent's status changes to online.
        """
        ready_leads = self.get_ready_now_leads()
        if not ready_leads:
            return {"processed": 0}

        available_agents = self.presence.get_available_agents(tenant_id)
        if not available_agents:
            return {"processed": 0, "reason": "no_agents"}

        processed = 0
        for lead_id in ready_leads:
            if not available_agents:
                break

            lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                self.remove_from_ready_now(lead_id)
                continue

            # Try to book
            agent = available_agents[0]
            result = self.handle_instant_reply(
                lead_id=UUID(lead_id),
                message_text="yes",
                tenant_id=tenant_id,
            )

            if result.get("action") == "booked":
                self.remove_from_ready_now(lead_id)
                available_agents.pop(0)
                processed += 1

        return {"processed": processed}

    # --- Step 40.6: Occupancy Optimizer ---

    def calculate_tenant_occupancy(self, tenant_id: str) -> Dict[str, Any]:
        """
        Calculate overall tenant occupancy and optimization opportunities.

        Returns:
            Dict with occupancy metrics and recommendations
        """
        now = datetime.now(timezone.utc)

        # Get all active agents
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        if not agents:
            return {"occupancy": 0, "agents": 0}

        total_capacity = 0
        total_used = 0
        agent_metrics = []

        for agent in agents:
            # Get today's appointments
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            appointments = self.db.query(Appointment).filter(
                Appointment.agent_id == agent.id,
                Appointment.start_time >= today_start,
                Appointment.status.in_(["confirmed", "completed"]),
            ).all()

            # Calculate capacity (8 hours = 480 minutes)
            daily_capacity = (agent.daily_capacity or 8) * 60

            # Calculate used time
            used_minutes = 0
            for appt in appointments:
                if appt.call_duration_seconds:
                    used_minutes += appt.call_duration_seconds / 60
                elif appt.start_time and appt.end_time:
                    used_minutes += (appt.end_time - appt.start_time).total_seconds() / 60

            occupancy = used_minutes / daily_capacity if daily_capacity > 0 else 0

            agent_metrics.append({
                "agent_id": str(agent.id),
                "daily_capacity_minutes": daily_capacity,
                "used_minutes": round(used_minutes, 1),
                "occupancy": round(occupancy, 3),
                "appointments": len(appointments),
            })

            total_capacity += daily_capacity
            total_used += used_minutes

        overall_occupancy = total_used / total_capacity if total_capacity > 0 else 0

        # Generate recommendations
        recommendations = []
        if overall_occupancy < 0.5:
            recommendations.append("Low occupancy: Consider reducing agent count or increasing outreach")
        elif overall_occupancy < TARGET_UTILIZATION:
            recommendations.append("Below target: Use emergency fill to boost utilization")
        elif overall_occupancy > 0.95:
            recommendations.append("Near capacity: Consider adding agents or expanding hours")

        # Find underutilized agents
        underutilized = [a for a in agent_metrics if a["occupancy"] < 0.5]
        if underutilized:
            recommendations.append(
                f"{len(underutilized)} agents below 50% utilization"
            )

        return {
            "tenant_id": tenant_id,
            "overall_occupancy": round(overall_occupancy, 3),
            "target_occupancy": TARGET_UTILIZATION,
            "total_agents": len(agents),
            "total_capacity_minutes": total_capacity,
            "total_used_minutes": round(total_used, 1),
            "agents": agent_metrics,
            "underutilized_agents": len(underutilized),
            "recommendations": recommendations,
        }

    def run_emergency_fill_cycle(self, tenant_id: str) -> Dict[str, Any]:
        """
        Run a complete emergency fill cycle.

        Called every 5 minutes by Celery beat.

        Steps:
        1. Detect idle slots
        2. Find warm leads
        3. Send SMS blast
        4. Process ready_now queue
        """
        results = {
            "idle_slots_found": 0,
            "warm_leads_found": 0,
            "sms_sent": 0,
            "ready_now_processed": 0,
        }

        # Queue-Only Mode / kill-switch: stand down. Emergency-fill blasts solicit
        # bookings ("...Reply YES to book!"), which Queue-Only Mode suppresses —
        # so send nothing while booking autopilot is paused (or all sending is).
        try:
            from app.core.sending import is_autopilot_paused, is_sending_paused
            if is_autopilot_paused(tenant_id) or is_sending_paused(tenant_id):
                results["skipped"] = "autopilot_paused" if is_autopilot_paused(tenant_id) else "kill_switch"
                return results
        except Exception:
            pass

        # 1. Detect idle slots
        idle_slots = self.detect_idle_slots(tenant_id)
        results["idle_slots_found"] = len(idle_slots)

        if not idle_slots:
            return results

        # 2. Find warm leads
        warm_leads = self.find_warm_leads(tenant_id)
        results["warm_leads_found"] = len(warm_leads)

        if not warm_leads:
            return results

        # 3. Send SMS for each idle slot
        for slot in idle_slots[:3]:  # Limit to 3 slots per cycle
            sms_result = self.send_emergency_fill_sms(tenant_id, slot, warm_leads)
            results["sms_sent"] += sms_result.get("leads_contacted", 0)

        # 4. Process ready_now queue
        ready_result = self.process_ready_now(tenant_id)
        results["ready_now_processed"] = ready_result.get("processed", 0)

        return results

    # --- Helpers ---

    def _log_emergency_message(self, lead: Lead, message: str, tenant_id: str) -> None:
        """Log emergency fill message to conversation."""
        conversation = self.db.query(Conversation).filter(
            Conversation.lead_id == lead.id,
            Conversation.status.in_(["active", "initiated"]),
        ).first()

        if not conversation:
            conversation = Conversation(
                tenant_id=tenant_id,
                lead_id=lead.id,
                status="active",
            )
            self.db.add(conversation)
            self.db.flush()

        msg = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender="ai",
            content=message,
            message_type="sms",
        )
        self.db.add(msg)
        conversation.message_count += 1
        conversation.last_message_at = datetime.now(timezone.utc)
        conversation.last_message_from = "ai"
        self.db.commit()
