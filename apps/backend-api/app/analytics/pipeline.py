"""
Enterprise Analytics Pipeline (Phase 45)

Converts all system actions into analytics events:

Step 45.1 — Event Streaming
    All actions become events with metadata

Step 45.2 — Warehouse ETL
    Postgres → ClickHouse pipeline

Step 45.3 — Real-Time Dashboards
    Conversion, occupancy, no-show, reply rate

Step 45.4 — Predictive Analytics
    Forecast staffing, bookings, conversions

Event Flow:
Action → Event → Redis Stream → ClickHouse → Dashboard
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.redis import redis_service
from app.analytics.clickhouse import ClickHouseClient

logger = logging.getLogger(__name__)


# --- Event Types ---

class EventType:
    """Analytics event types."""
    # Lead events
    LEAD_CREATED = "lead.created"
    LEAD_UPDATED = "lead.updated"
    LEAD_SCORED = "lead.scored"
    LEAD_CONTACTED = "lead.contacted"
    LEAD_REPLIED = "lead.replied"
    LEAD_QUALIFIED = "lead.qualified"

    # Conversation events
    CONVERSATION_STARTED = "conversation.started"
    CONVERSATION_MESSAGE = "conversation.message"
    CONVERSATION_STATE_CHANGED = "conversation.state_changed"

    # Booking events
    BOOKING_STARTED = "booking.started"
    BOOKING_SLOT_SELECTED = "booking.slot_selected"
    BOOKING_CONFIRMED = "booking.confirmed"
    BOOKING_CANCELLED = "booking.cancelled"
    BOOKING_RESCHEDULED = "booking.rescheduled"
    BOOKING_NO_SHOW = "booking.no_show"

    # Appointment events
    APPOINTMENT_CREATED = "appointment.created"
    APPOINTMENT_COMPLETED = "appointment.completed"
    APPOINTMENT_DISPOSITION = "appointment.disposition"

    # AI events
    AI_RESPONSE_GENERATED = "ai.response_generated"
    AI_INTENT_DETECTED = "ai.intent_detected"
    AI_OUTREACH_SENT = "ai.outreach_sent"

    # Agent events
    AGENT_STATUS_CHANGED = "agent.status_changed"
    AGENT_CALL_STARTED = "agent.call_started"
    AGENT_CALL_ENDED = "agent.call_ended"

    # Campaign events
    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_UPDATED = "campaign.updated"
    CAMPAIGN_COMPLETED = "campaign.completed"


# --- Analytics Event ---

class AnalyticsEvent:
    """Represents an analytics event."""

    def __init__(
        self,
        event_type: str,
        tenant_id: str,
        data: Dict[str, Any] = None,
        user_id: str = None,
        lead_id: str = None,
        agent_id: str = None,
        campaign_id: str = None,
        conversation_id: str = None,
        appointment_id: str = None,
    ):
        self.event_id = str(uuid4())
        self.event_type = event_type
        self.tenant_id = tenant_id
        self.data = data or {}
        self.user_id = user_id
        self.lead_id = lead_id
        self.agent_id = agent_id
        self.campaign_id = campaign_id
        self.conversation_id = conversation_id
        self.appointment_id = appointment_id
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "data": json.dumps(self.data),
            "user_id": self.user_id,
            "lead_id": self.lead_id,
            "agent_id": self.agent_id,
            "campaign_id": self.campaign_id,
            "conversation_id": self.conversation_id,
            "appointment_id": self.appointment_id,
            "timestamp": self.timestamp,
            "date": self.date,
        }


# --- Event Capture ---

class EventCapture:
    """
    Captures system events and publishes to stream.

    Usage:
        capture = EventCapture(db)
        capture.capture(EventType.LEAD_CREATED, tenant_id, data)
    """

    STREAM_NAME = "analytics_events"

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service

    def capture(
        self,
        event_type: str,
        tenant_id: str,
        data: Dict[str, Any] = None,
        **kwargs,
    ) -> AnalyticsEvent:
        """
        Capture an analytics event.

        Args:
            event_type: Type of event
            tenant_id: Tenant ID
            data: Event data
            **kwargs: Additional IDs (user_id, lead_id, etc.)

        Returns:
            AnalyticsEvent
        """
        event = AnalyticsEvent(
            event_type=event_type,
            tenant_id=tenant_id,
            data=data,
            user_id=kwargs.get("user_id"),
            lead_id=kwargs.get("lead_id"),
            agent_id=kwargs.get("agent_id"),
            campaign_id=kwargs.get("campaign_id"),
            conversation_id=kwargs.get("conversation_id"),
            appointment_id=kwargs.get("appointment_id"),
        )

        # Publish to Redis stream
        self.redis.client.xadd(
            f"stream:{self.STREAM_NAME}",
            event.to_dict(),
            maxlen=50000,
        )

        return event

    def capture_lead_created(self, tenant_id: str, lead: Any) -> AnalyticsEvent:
        """Capture lead creation event."""
        return self.capture(
            EventType.LEAD_CREATED,
            tenant_id,
            data={
                "source": lead.source,
                "state": lead.state,
                "lead_score": lead.lead_score or 0,
            },
            lead_id=str(lead.id),
        )

    def capture_booking_confirmed(self, tenant_id: str, appointment: Any) -> AnalyticsEvent:
        """Capture booking confirmation event."""
        return self.capture(
            EventType.BOOKING_CONFIRMED,
            tenant_id,
            data={
                "agent_id": str(appointment.agent_id),
                "start_time": appointment.start_time.isoformat(),
                "booking_source": appointment.booking_source,
            },
            lead_id=str(appointment.lead_id),
            agent_id=str(appointment.agent_id),
            appointment_id=str(appointment.id),
        )

    def capture_ai_response(self, tenant_id: str, conversation_id: str, data: Dict) -> AnalyticsEvent:
        """Capture AI response event."""
        return self.capture(
            EventType.AI_RESPONSE_GENERATED,
            tenant_id,
            data=data,
            conversation_id=conversation_id,
        )

    def capture_agent_call(self, tenant_id: str, agent_id: str, event_type: str, data: Dict = None) -> AnalyticsEvent:
        """Capture agent call event."""
        return self.capture(
            event_type,
            tenant_id,
            data=data or {},
            agent_id=agent_id,
        )


# --- ClickHouse ETL ---

class ClickHouseETL:
    """
    Extracts data from Postgres and loads into ClickHouse.

    Pipeline:
    1. Extract from Postgres (batch)
    2. Transform to ClickHouse format
    3. Load into ClickHouse tables
    """

    def __init__(self, db: Session):
        self.db = db
        self.clickhouse = ClickHouseClient()

    async def etl_lead_metrics(self, since: datetime = None) -> Dict[str, int]:
        """ETL lead metrics to ClickHouse."""
        from app.models.lead import Lead

        if not since:
            since = datetime.now(timezone.utc) - timedelta(hours=1)

        leads = self.db.query(Lead).filter(
            Lead.created_at >= since,
            Lead.deleted_at.is_(None),
        ).all()

        inserted = 0
        for lead in leads:
            try:
                await self.clickhouse.insert_event(
                    tenant_id=str(lead.tenant_id),
                    event_type="lead_metric",
                    event_category="lead",
                    user_id=None,
                    lead_id=str(lead.id),
                    properties={
                        "source": lead.source,
                        "state": lead.state,
                        "lead_score": str(lead.lead_score or 0),
                        "status": lead.status,
                        "lifecycle_stage": lead.lifecycle_stage,
                    },
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"Failed to ETL lead {lead.id}: {e}")

        return {"leads_processed": len(leads), "leads_inserted": inserted}

    async def etl_appointment_metrics(self, since: datetime = None) -> Dict[str, int]:
        """ETL appointment metrics to ClickHouse."""
        from app.models.appointment import Appointment

        if not since:
            since = datetime.now(timezone.utc) - timedelta(hours=1)

        appointments = self.db.query(Appointment).filter(
            Appointment.created_at >= since,
        ).all()

        inserted = 0
        for appt in appointments:
            try:
                await self.clickhouse.insert_event(
                    tenant_id=str(appt.tenant_id),
                    event_type="appointment_metric",
                    event_category="appointment",
                    user_id=None,
                    lead_id=str(appt.lead_id),
                    appointment_id=str(appt.id),
                    agent_id=str(appt.agent_id),
                    properties={
                        "status": appt.status,
                        "disposition": appt.disposition or "",
                        "duration_seconds": str(appt.call_duration_seconds or 0),
                        "booking_source": appt.booking_source or "",
                    },
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"Failed to ETL appointment {appt.id}: {e}")

        return {"appointments_processed": len(appointments), "appointments_inserted": inserted}

    async def etl_message_metrics(self, since: datetime = None) -> Dict[str, int]:
        """ETL message metrics to ClickHouse."""
        from app.models.message import Message

        if not since:
            since = datetime.now(timezone.utc) - timedelta(hours=1)

        messages = self.db.query(Message).filter(
            Message.created_at >= since,
        ).limit(10000).all()

        inserted = 0
        for msg in messages:
            try:
                await self.clickhouse.insert_event(
                    tenant_id=str(msg.tenant_id),
                    event_type="message_metric",
                    event_category="message",
                    user_id=None,
                    lead_id=None,
                    properties={
                        "conversation_id": str(msg.conversation_id),
                        "sender": msg.sender,
                        "message_type": msg.message_type,
                        "intent": msg.intent or "",
                        "sentiment": msg.sentiment or "",
                        "content_length": str(len(msg.content) if msg.content else 0),
                    },
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"Failed to ETL message {msg.id}: {e}")

        return {"messages_processed": len(messages), "messages_inserted": inserted}

    async def run_full_etl(self, since: datetime = None) -> Dict[str, Any]:
        """Run full ETL pipeline."""
        results = {
            "leads": await self.etl_lead_metrics(since),
            "appointments": await self.etl_appointment_metrics(since),
            "messages": await self.etl_message_metrics(since),
        }

        total_inserted = sum(r.get("leads_inserted", 0) + r.get("appointments_inserted", 0) + r.get("messages_inserted", 0) for r in results.values())
        results["total_inserted"] = total_inserted

        return results


# --- Real-Time Dashboard ---

class RealTimeDashboard:
    """
    Generates real-time dashboard metrics.

    Metrics:
    - Conversion rate
    - Occupancy rate
    - No-show rate
    - Reply rate
    - Booking rate
    - Response time
    """

    def __init__(self, db: Session):
        self.db = db

    def get_dashboard(self, tenant_id: str, period_days: int = 30) -> Dict[str, Any]:
        """
        Generate complete dashboard metrics.

        Args:
            tenant_id: Tenant ID
            period_days: Analysis period

        Returns:
            Dict with all dashboard metrics
        """
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        return {
            "period": {
                "days": period_days,
                "start": period_start.isoformat(),
                "end": now.isoformat(),
            },
            "leads": self._lead_metrics(tenant_id, period_start),
            "conversations": self._conversation_metrics(tenant_id, period_start),
            "bookings": self._booking_metrics(tenant_id, period_start),
            "appointments": self._appointment_metrics(tenant_id, period_start),
            "agents": self._agent_metrics(tenant_id, period_start),
            "campaigns": self._campaign_metrics(tenant_id, period_start),
            "ai": self._ai_metrics(tenant_id, period_start),
        }

    def _lead_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate lead metrics."""
        from app.models.lead import Lead

        total = self.db.query(func.count(Lead.id)).filter(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= since,
            Lead.deleted_at.is_(None),
        ).scalar() or 0

        contacted = self.db.query(func.count(Lead.id)).filter(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= since,
            Lead.status.in_(["contacted", "replied", "interested", "booked"]),
        ).scalar() or 0

        replied = self.db.query(func.count(Lead.id)).filter(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= since,
            Lead.status.in_(["replied", "interested", "booked"]),
        ).scalar() or 0

        return {
            "total": total,
            "contacted": contacted,
            "replied": replied,
            "contact_rate": round(contacted / total, 3) if total > 0 else 0,
            "reply_rate": round(replied / contacted, 3) if contacted > 0 else 0,
        }

    def _conversation_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate conversation metrics."""
        from app.models.conversation import Conversation

        total = self.db.query(func.count(Conversation.id)).filter(
            Conversation.tenant_id == tenant_id,
            Conversation.created_at >= since,
        ).scalar() or 0

        active = self.db.query(func.count(Conversation.id)).filter(
            Conversation.tenant_id == tenant_id,
            Conversation.created_at >= since,
            Conversation.status == "active",
        ).scalar() or 0

        return {
            "total": total,
            "active": active,
        }

    def _booking_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate booking metrics."""
        from app.models.appointment import Appointment

        total = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
        ).scalar() or 0

        confirmed = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.status == "confirmed",
        ).scalar() or 0

        cancelled = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.status == "cancelled",
        ).scalar() or 0

        no_show = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.status.in_(["no_show", "missed"]),
        ).scalar() or 0

        return {
            "total": total,
            "confirmed": confirmed,
            "cancelled": cancelled,
            "no_show": no_show,
            "no_show_rate": round(no_show / total, 3) if total > 0 else 0,
            "cancellation_rate": round(cancelled / total, 3) if total > 0 else 0,
        }

    def _appointment_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate appointment completion metrics."""
        from app.models.appointment import Appointment

        completed = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.status == "completed",
        ).scalar() or 0

        won = self.db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.disposition == "won",
        ).scalar() or 0

        avg_duration = self.db.query(func.avg(Appointment.call_duration_seconds)).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= since,
            Appointment.call_duration_seconds.isnot(None),
        ).scalar() or 0

        return {
            "completed": completed,
            "won": won,
            "win_rate": round(won / completed, 3) if completed > 0 else 0,
            "avg_duration_seconds": round(avg_duration, 1),
        }

    def _agent_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate agent utilization metrics."""
        from app.models.agent import Agent
        from app.realtime.presence import PresenceManager

        total_agents = self.db.query(func.count(Agent.id)).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).scalar() or 0

        # Get online count from presence
        presence_mgr = PresenceManager(self.db)
        online = len(presence_mgr.get_online_agents(tenant_id))

        return {
            "total": total_agents,
            "online": online,
            "utilization_rate": round(online / total_agents, 3) if total_agents > 0 else 0,
        }

    def _campaign_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate campaign metrics."""
        from app.models.campaign import Campaign

        campaigns = self.db.query(Campaign).filter(
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        ).all()

        total_leads = sum(c.total_leads or 0 for c in campaigns)
        total_contacted = sum(c.total_contacted or 0 for c in campaigns)
        total_replied = sum(c.total_replied or 0 for c in campaigns)
        total_booked = sum(c.total_booked or 0 for c in campaigns)

        return {
            "active_campaigns": len(campaigns),
            "total_leads": total_leads,
            "total_contacted": total_contacted,
            "total_replied": total_replied,
            "total_booked": total_booked,
            "overall_reply_rate": round(total_replied / total_contacted, 3) if total_contacted > 0 else 0,
            "overall_booking_rate": round(total_booked / total_replied, 3) if total_replied > 0 else 0,
        }

    def _ai_metrics(self, tenant_id: str, since: datetime) -> Dict:
        """Calculate AI performance metrics."""
        from app.models.message import Message

        ai_messages = self.db.query(func.count(Message.id)).filter(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
            Message.sender == "ai",
        ).scalar() or 0

        customer_replies = self.db.query(func.count(Message.id)).filter(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
            Message.sender == "customer",
        ).scalar() or 0

        return {
            "ai_messages_sent": ai_messages,
            "customer_replies": customer_replies,
            "engagement_rate": round(customer_replies / ai_messages, 3) if ai_messages > 0 else 0,
        }


# --- Predictive Analytics ---

class PredictiveAnalytics:
    """
    Generates predictive forecasts.

    Forecasts:
    - Staffing needs
    - Booking volume
    - Conversion rates
    - Revenue projections
    """

    def __init__(self, db: Session):
        self.db = db

    def forecast_bookings(
        self,
        tenant_id: str,
        days_ahead: int = 7,
    ) -> Dict[str, Any]:
        """
        Forecast booking volume for next N days.

        Uses simple moving average of historical data.
        """
        from app.models.appointment import Appointment

        # Get historical daily bookings (last 30 days)
        now = datetime.now(timezone.utc)
        history_start = now - timedelta(days=30)

        appointments = self.db.query(Appointment).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= history_start,
        ).all()

        # Group by day
        daily_counts = {}
        for appt in appointments:
            day = appt.created_at.strftime("%Y-%m-%d")
            daily_counts[day] = daily_counts.get(day, 0) + 1

        if not daily_counts:
            return {"forecast": [], "confidence": "low"}

        # Calculate moving average
        counts = list(daily_counts.values())
        avg_daily = sum(counts) / len(counts) if counts else 0

        # Generate forecast
        forecast = []
        for i in range(1, days_ahead + 1):
            forecast_date = now + timedelta(days=i)
            forecast.append({
                "date": forecast_date.strftime("%Y-%m-%d"),
                "predicted_bookings": round(avg_daily),
                "confidence": "medium" if len(counts) >= 7 else "low",
            })

        return {
            "forecast": forecast,
            "historical_avg": round(avg_daily, 1),
            "historical_days": len(counts),
            "confidence": "medium" if len(counts) >= 14 else "low",
        }

    def forecast_staffing(
        self,
        tenant_id: str,
        days_ahead: int = 7,
    ) -> Dict[str, Any]:
        """
        Forecast staffing needs.

        Based on predicted booking volume and agent capacity.
        """
        from app.models.agent import Agent

        # Get current agents
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        total_capacity = sum(a.daily_capacity or 8 for a in agents)

        # Get booking forecast
        booking_forecast = self.forecast_bookings(tenant_id, days_ahead)

        staffing = []
        for day in booking_forecast.get("forecast", []):
            predicted = day["predicted_bookings"]
            agents_needed = max(1, round(predicted / 8))  # 8 bookings per agent per day

            staffing.append({
                "date": day["date"],
                "predicted_bookings": predicted,
                "current_capacity": total_capacity,
                "agents_needed": agents_needed,
                "current_agents": len(agents),
                "surplus": len(agents) - agents_needed,
            })

        return {
            "forecast": staffing,
            "current_agents": len(agents),
            "current_capacity": total_capacity,
        }

    def forecast_conversions(
        self,
        tenant_id: str,
        days_ahead: int = 7,
    ) -> Dict[str, Any]:
        """
        Forecast conversion rates.

        Based on historical conversion trends.
        """
        from app.models.lead import Lead
        from app.models.appointment import Appointment

        now = datetime.now(timezone.utc)

        # Historical conversion rates (last 4 weeks)
        weekly_rates = []
        for week in range(4):
            week_start = now - timedelta(days=(week + 1) * 7)
            week_end = now - timedelta(days=week * 7)

            leads = self.db.query(func.count(Lead.id)).filter(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= week_start,
                Lead.created_at < week_end,
            ).scalar() or 0

            bookings = self.db.query(func.count(Appointment.id)).filter(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= week_start,
                Appointment.created_at < week_end,
            ).scalar() or 0

            if leads > 0:
                weekly_rates.append(bookings / leads)

        avg_rate = sum(weekly_rates) / len(weekly_rates) if weekly_rates else 0.1

        return {
            "current_rate": round(avg_rate, 3),
            "trend": "stable",
            "weekly_rates": [round(r, 3) for r in weekly_rates],
            "confidence": "medium" if len(weekly_rates) >= 3 else "low",
        }
