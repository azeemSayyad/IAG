"""
Feature Pipeline (Phase 38.1)

Collects and engineers features from production data:

Lead Features:
- Reply rate, response time, engagement score
- Source quality, geographic value
- Message count, sentiment trends
- Booking history, no-show history

Agent Features:
- Conversion rate, booking rate
- Average call duration, utilization
- No-show rate, customer satisfaction
- Response time, workload

Campaign Features:
- Reply rate, booking rate, conversion rate
- Best performing tone, timing
- Objection patterns, success patterns
- Cost per lead, ROI

Pipeline Flow:
Raw Data → Feature Extraction → Feature Engineering → Feature Store
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.models.agent import Agent
from app.models.campaign import Campaign

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """
    Extracts and engineers features from production data.

    Usage:
        pipeline = FeaturePipeline(db)
        lead_features = pipeline.extract_lead_features(lead_id)
        agent_features = pipeline.extract_agent_features(agent_id)
        campaign_features = pipeline.extract_campaign_features(campaign_id)
    """

    def __init__(self, db: Session):
        self.db = db

    # --- Lead Features ---

    def extract_lead_features(self, lead_id: UUID) -> Dict[str, Any]:
        """
        Extract all features for a lead.

        Returns:
            Dict with feature names and values
        """
        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {}

        features = {}

        # Basic features
        features["source"] = lead.source
        features["state"] = lead.state
        features["lead_score"] = lead.lead_score or 0
        features["status"] = lead.status

        # Engagement features
        engagement = self._calculate_engagement_features(lead_id)
        features.update(engagement)

        # Response time features
        response = self._calculate_response_features(lead_id)
        features.update(response)

        # Booking features
        booking = self._calculate_booking_features(lead_id)
        features.update(booking)

        # Sentiment features
        sentiment = self._calculate_sentiment_features(lead_id)
        features.update(sentiment)

        # Source quality score
        features["source_quality"] = self._score_source(lead.source)

        # Geographic value
        features["geo_value"] = self._score_geography(lead.state)

        # Data completeness
        features["data_completeness"] = self._score_completeness(lead)

        return features

    def _calculate_engagement_features(self, lead_id: UUID) -> Dict[str, float]:
        """Calculate engagement-related features."""
        # Total messages
        total_msgs = self.db.query(func.count(Message.id)).join(Conversation).filter(
            Conversation.lead_id == lead_id,
        ).scalar() or 0

        # Customer messages
        customer_msgs = self.db.query(func.count(Message.id)).join(Conversation).filter(
            Conversation.lead_id == lead_id,
            Message.sender == "customer",
        ).scalar() or 0

        # AI messages
        ai_msgs = total_msgs - customer_msgs

        # Conversations
        conv_count = self.db.query(func.count(Conversation.id)).filter(
            Conversation.lead_id == lead_id,
        ).scalar() or 0

        # Customer reply ratio
        reply_ratio = customer_msgs / ai_msgs if ai_msgs > 0 else 0

        return {
            "total_messages": total_msgs,
            "customer_messages": customer_msgs,
            "ai_messages": ai_msgs,
            "conversation_count": conv_count,
            "reply_ratio": round(min(reply_ratio, 1.0), 3),
            "engagement_score": round(min(reply_ratio * 0.6 + (conv_count / 5) * 0.4, 1.0), 3),
        }

    def _calculate_response_features(self, lead_id: UUID) -> Dict[str, float]:
        """Calculate response time features."""
        # Get message pairs (AI → Customer)
        messages = (
            self.db.query(Message)
            .join(Conversation)
            .filter(Conversation.lead_id == lead_id)
            .order_by(Message.created_at)
            .all()
        )

        response_times = []
        last_ai_time = None

        for msg in messages:
            if msg.sender == "ai" and msg.created_at:
                last_ai_time = msg.created_at
            elif msg.sender == "customer" and last_ai_time and msg.created_at:
                delta = (msg.created_at - last_ai_time).total_seconds()
                if 0 < delta < 86400:  # Within 24 hours
                    response_times.append(delta)
                last_ai_time = None

        if not response_times:
            return {
                "avg_response_time_seconds": 0,
                "min_response_time_seconds": 0,
                "max_response_time_seconds": 0,
                "response_speed_bucket": "unknown",
            }

        avg_time = sum(response_times) / len(response_times)
        min_time = min(response_times)
        max_time = max(response_times)

        # Speed bucket
        if avg_time < 300:  # < 5 min
            speed = "fast"
        elif avg_time < 3600:  # < 1 hour
            speed = "moderate"
        else:
            speed = "slow"

        return {
            "avg_response_time_seconds": round(avg_time, 1),
            "min_response_time_seconds": round(min_time, 1),
            "max_response_time_seconds": round(max_time, 1),
            "response_speed_bucket": speed,
            "response_count": len(response_times),
        }

    def _calculate_booking_features(self, lead_id: UUID) -> Dict[str, Any]:
        """Calculate booking-related features."""
        appointments = self.db.query(Appointment).filter(
            Appointment.lead_id == lead_id,
        ).all()

        total = len(appointments)
        confirmed = sum(1 for a in appointments if a.status == "confirmed")
        completed = sum(1 for a in appointments if a.status == "completed")
        no_show = sum(1 for a in appointments if a.status in ("no_show", "missed"))
        cancelled = sum(1 for a in appointments if a.status == "cancelled")

        return {
            "total_appointments": total,
            "confirmed_appointments": confirmed,
            "completed_appointments": completed,
            "no_show_appointments": no_show,
            "cancelled_appointments": cancelled,
            "no_show_rate": round(no_show / total, 3) if total > 0 else 0,
            "completion_rate": round(completed / total, 3) if total > 0 else 0,
            "has_booked": total > 0,
        }

    def _calculate_sentiment_features(self, lead_id: UUID) -> Dict[str, Any]:
        """Calculate sentiment-related features."""
        conversations = self.db.query(Conversation).filter(
            Conversation.lead_id == lead_id,
        ).all()

        if not conversations:
            return {
                "current_sentiment": "neutral",
                "sentiment_score": 0.5,
                "sentiment_trend": "stable",
            }

        # Get latest conversation sentiment
        latest = conversations[0]
        for conv in conversations:
            if conv.updated_at and (not latest.updated_at or conv.updated_at > latest.updated_at):
                latest = conv

        ai_context = latest.ai_context or {}
        sentiment_data = ai_context.get("sentiment", {})

        return {
            "current_sentiment": sentiment_data.get("current", "neutral"),
            "sentiment_score": sentiment_data.get("score", 0.5),
            "sentiment_trend": sentiment_data.get("trend", "stable"),
        }

    def _score_source(self, source: str) -> float:
        """Score lead source quality."""
        source_scores = {
            "referral": 0.95,
            "google": 0.85,
            "facebook": 0.75,
            "webhook": 0.70,
            "csv_import": 0.50,
            "manual": 0.60,
        }
        return source_scores.get(source, 0.50)

    def _score_geography(self, state: Optional[str]) -> float:
        """Score geographic value."""
        if not state:
            return 0.5

        high_value_states = ["CA", "TX", "FL", "NY", "IL"]
        medium_value_states = ["PA", "OH", "GA", "NC", "MI"]

        if state.upper() in high_value_states:
            return 0.9
        elif state.upper() in medium_value_states:
            return 0.7
        else:
            return 0.5

    def _score_completeness(self, lead: Lead) -> float:
        """Score data completeness."""
        fields = [
            lead.first_name, lead.last_name, lead.phone,
            lead.email, lead.state, lead.city, lead.zip_code,
        ]
        filled = sum(1 for f in fields if f)
        return round(filled / len(fields), 3)

    # --- Agent Features ---

    def extract_agent_features(self, agent_id: UUID) -> Dict[str, Any]:
        """
        Extract all features for an agent.

        Returns:
            Dict with feature names and values
        """
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            return {}

        features = {}

        # Basic features
        features["timezone"] = agent.timezone
        features["daily_capacity"] = agent.daily_capacity
        features["weight"] = agent.weight
        features["status"] = agent.status

        # Performance features
        performance = self._calculate_agent_performance(agent_id)
        features.update(performance)

        # Utilization features
        utilization = self._calculate_agent_utilization(agent_id)
        features.update(utilization)

        return features

    def _calculate_agent_performance(self, agent_id: UUID) -> Dict[str, float]:
        """Calculate agent performance features."""
        appointments = self.db.query(Appointment).filter(
            Appointment.agent_id == agent_id,
        ).all()

        total = len(appointments)
        completed = sum(1 for a in appointments if a.status == "completed")
        no_show = sum(1 for a in appointments if a.status in ("no_show", "missed"))
        won = sum(1 for a in appointments if a.disposition == "won")

        # Call durations
        durations = [a.call_duration_seconds for a in appointments if a.call_duration_seconds]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "total_appointments": total,
            "completed_appointments": completed,
            "no_show_appointments": no_show,
            "won_appointments": won,
            "completion_rate": round(completed / total, 3) if total > 0 else 0,
            "no_show_rate": round(no_show / total, 3) if total > 0 else 0,
            "win_rate": round(won / completed, 3) if completed > 0 else 0,
            "avg_call_duration": round(avg_duration, 1),
        }

    def _calculate_agent_utilization(self, agent_id: UUID) -> Dict[str, float]:
        """Calculate agent utilization features."""
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        # Appointments this week
        weekly_appts = self.db.query(func.count(Appointment.id)).filter(
            Appointment.agent_id == agent_id,
            Appointment.start_time >= week_ago,
        ).scalar() or 0

        # Capacity this week (5 days * daily_capacity)
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        weekly_capacity = (agent.daily_capacity * 5) if agent else 40

        utilization = weekly_appts / weekly_capacity if weekly_capacity > 0 else 0

        return {
            "weekly_appointments": weekly_appts,
            "weekly_capacity": weekly_capacity,
            "utilization_rate": round(min(utilization, 1.0), 3),
        }

    # --- Campaign Features ---

    def extract_campaign_features(self, campaign_id: UUID) -> Dict[str, Any]:
        """
        Extract all features for a campaign.

        Returns:
            Dict with feature names and values
        """
        campaign = self.db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return {}

        features = {}

        # Basic features
        features["name"] = campaign.name
        features["tone"] = campaign.tone
        features["status"] = campaign.status
        features["max_retries"] = campaign.max_retries
        features["booking_enabled"] = campaign.booking_enabled

        # Performance features
        total_leads = campaign.total_leads or 0
        total_contacted = campaign.total_contacted or 0
        total_replied = campaign.total_replied or 0
        total_booked = campaign.total_booked or 0
        total_completed = campaign.total_completed or 0
        total_won = campaign.total_won or 0

        features["total_leads"] = total_leads
        features["total_contacted"] = total_contacted
        features["total_replied"] = total_replied
        features["total_booked"] = total_booked
        features["total_completed"] = total_completed
        features["total_won"] = total_won

        # Rates
        features["contact_rate"] = round(total_contacted / total_leads, 3) if total_leads > 0 else 0
        features["reply_rate"] = round(total_replied / total_contacted, 3) if total_contacted > 0 else 0
        features["booking_rate"] = round(total_booked / total_replied, 3) if total_replied > 0 else 0
        features["completion_rate"] = round(total_completed / total_booked, 3) if total_booked > 0 else 0
        features["win_rate"] = round(total_won / total_completed, 3) if total_completed > 0 else 0

        # Conversion funnel
        features["conversion_rate"] = round(total_won / total_leads, 3) if total_leads > 0 else 0

        # Targeting features
        features["target_sources"] = campaign.target_sources or []
        features["target_states"] = campaign.target_states or []
        features["min_lead_score"] = campaign.min_lead_score or 0
        features["max_lead_score"] = campaign.max_lead_score or 100

        return features

    # --- Batch Feature Extraction ---

    def extract_all_lead_features(self, limit: int = 1000) -> List[Dict]:
        """Extract features for all active leads."""
        leads = self.db.query(Lead).filter(
            Lead.deleted_at.is_(None),
            Lead.status.in_(["new", "contacted", "replied", "interested"]),
        ).limit(limit).all()

        features_list = []
        for lead in leads:
            features = self.extract_lead_features(lead.id)
            features["lead_id"] = str(lead.id)
            features_list.append(features)

        return features_list

    def extract_all_agent_features(self) -> List[Dict]:
        """Extract features for all active agents."""
        agents = self.db.query(Agent).filter(
            Agent.status == "active",
        ).all()

        features_list = []
        for agent in agents:
            features = self.extract_agent_features(agent.id)
            features["agent_id"] = str(agent.id)
            features_list.append(features)

        return features_list

    def extract_all_campaign_features(self) -> List[Dict]:
        """Extract features for all active campaigns."""
        campaigns = self.db.query(Campaign).filter(
            Campaign.deleted_at.is_(None),
        ).all()

        features_list = []
        for campaign in campaigns:
            features = self.extract_campaign_features(campaign.id)
            features["campaign_id"] = str(campaign.id)
            features_list.append(features)

        return features_list
