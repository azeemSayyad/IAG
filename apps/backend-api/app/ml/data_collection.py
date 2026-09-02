"""
ML Data Collection (Step 21.1)

Collects and stores training data for ML models.

Data Sources:
- Replies — Customer message responses
- Bookings — Appointment bookings
- Conversions — Won appointments
- No-shows — Missed appointments

Data Storage:
- PostgreSQL for transactional data
- ClickHouse for time-series analytics
- Redis for real-time features
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models.lead import Lead
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.message import Message


class DataCollector:
    """Collects training data for ML models."""

    def __init__(self, db: Session):
        self.db = db

    def collect_lead_outcomes(
        self,
        tenant_id: UUID,
        days_back: int = 90,
    ) -> List[Dict]:
        """
        Collect lead outcome data for training.

        Returns list of leads with their outcomes:
        - replied: bool
        - booked: bool
        - converted: bool (won)
        - no_show: bool
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        leads = (
            self.db.query(Lead)
            .filter(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= cutoff,
                Lead.deleted_at.is_(None),
            )
            .all()
        )

        outcomes = []
        for lead in leads:
            # Check if replied
            conversation = (
                self.db.query(Conversation)
                .filter(Conversation.lead_id == lead.id)
                .first()
            )
            replied = conversation is not None and conversation.message_count > 1

            # Check if booked
            appointment = (
                self.db.query(Appointment)
                .filter(Appointment.lead_id == lead.id)
                .first()
            )
            booked = appointment is not None

            # Check if converted (won)
            converted = (
                appointment is not None
                and appointment.disposition == "won"
            )

            # Check if no-show
            no_show = (
                appointment is not None
                and appointment.status == "no_show"
            )

            outcomes.append({
                "lead_id": str(lead.id),
                "source": lead.source,
                "state": lead.state,
                "lead_score": lead.lead_score,
                "lifecycle_stage": lead.lifecycle_stage,
                "replied": replied,
                "booked": booked,
                "converted": converted,
                "no_show": no_show,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
                "first_contact_at": lead.last_contacted_at.isoformat() if lead.last_contacted_at else None,
                "response_time_hours": self._calculate_response_time(lead, conversation),
            })

        return outcomes

    def collect_conversation_features(
        self,
        tenant_id: UUID,
        days_back: int = 90,
    ) -> List[Dict]:
        """
        Collect conversation features for training.

        Returns conversations with:
        - message_count
        - customer_sentiment
        - intent_distribution
        - objection_types
        - booking_outcome
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        conversations = (
            self.db.query(Conversation)
            .filter(
                Conversation.tenant_id == tenant_id,
                Conversation.created_at >= cutoff,
            )
            .all()
        )

        features = []
        for conv in conversations:
            # Get messages
            messages = (
                self.db.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at)
                .all()
            )

            # Calculate features
            customer_messages = [m for m in messages if m.sender == "customer"]
            ai_messages = [m for m in messages if m.sender == "ai"]

            # Intent distribution
            intents = {}
            for m in messages:
                if m.intent:
                    intents[m.intent] = intents.get(m.intent, 0) + 1

            # Sentiment distribution
            sentiments = {}
            for m in messages:
                if m.sentiment:
                    sentiments[m.sentiment] = sentiments.get(m.sentiment, 0) + 1

            features.append({
                "conversation_id": str(conv.id),
                "lead_id": str(conv.lead_id),
                "message_count": conv.message_count,
                "customer_message_count": len(customer_messages),
                "ai_message_count": len(ai_messages),
                "intents": intents,
                "sentiments": sentiments,
                "final_state": conv.status,
                "final_intent": conv.intent,
                "final_sentiment": conv.sentiment,
                "duration_hours": self._calculate_conversation_duration(conv),
            })

        return features

    def collect_appointment_features(
        self,
        tenant_id: UUID,
        days_back: int = 90,
    ) -> List[Dict]:
        """
        Collect appointment features for training.

        Returns appointments with:
        - booking_source
        - time_features
        - agent_features
        - outcome
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        appointments = (
            self.db.query(Appointment)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= cutoff,
            )
            .all()
        )

        features = []
        for appt in appointments:
            lead = self.db.query(Lead).filter(Lead.id == appt.lead_id).first()

            features.append({
                "appointment_id": str(appt.id),
                "lead_id": str(appt.lead_id),
                "agent_id": str(appt.agent_id),
                "booking_source": appt.booking_source or "ai",
                "ai_confidence": appt.ai_confidence,
                "lead_source": lead.source if lead else None,
                "lead_score": lead.lead_score if lead else None,
                "hour_of_day": appt.start_time.hour if appt.start_time else None,
                "day_of_week": appt.start_time.weekday() if appt.start_time else None,
                "days_ahead": (appt.start_time - appt.created_at).days if appt.start_time and appt.created_at else None,
                "status": appt.status,
                "disposition": appt.disposition,
                "call_duration": appt.call_duration_seconds,
                "created_at": appt.created_at.isoformat() if appt.created_at else None,
            })

        return features

    def _calculate_response_time(self, lead: Lead, conversation: Optional[Conversation]) -> Optional[float]:
        """Calculate time from first contact to first reply in hours."""
        if not conversation or not lead.last_contacted_at:
            return None

        first_customer_msg = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.sender == "customer",
            )
            .order_by(Message.created_at)
            .first()
        )

        if not first_customer_msg:
            return None

        delta = first_customer_msg.created_at - lead.last_contacted_at
        return delta.total_seconds() / 3600

    def _calculate_conversation_duration(self, conversation: Conversation) -> Optional[float]:
        """Calculate conversation duration in hours."""
        if not conversation.created_at or not conversation.last_message_at:
            return None

        delta = conversation.last_message_at - conversation.created_at
        return delta.total_seconds() / 3600


def collect_training_data(
    db: Session,
    tenant_id: UUID,
    days_back: int = 90,
) -> Dict:
    """
    Collect all training data for a tenant.

    Returns:
        Dict with lead_outcomes, conversation_features, appointment_features
    """
    collector = DataCollector(db)

    return {
        "lead_outcomes": collector.collect_lead_outcomes(tenant_id, days_back),
        "conversation_features": collector.collect_conversation_features(tenant_id, days_back),
        "appointment_features": collector.collect_appointment_features(tenant_id, days_back),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "days_back": days_back,
    }
