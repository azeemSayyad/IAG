"""
Context Builder (Step 36.1)

Assembles full context for LLM from all available sources:
- Lead profile
- Conversation history
- Campaign settings
- Memory (objections, sentiment, preferences)
- RAG results (when available)
- Agent info
- Booking state
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.models.campaign import Campaign
from app.models.agent import Agent
from app.intent.services.memory import MemoryEngine


class ConversationContext:
    """Holds all context needed for AI response generation."""

    def __init__(self):
        self.lead: Optional[Lead] = None
        self.conversation: Optional[Conversation] = None
        self.messages: List[Dict] = []
        self.campaign: Optional[Campaign] = None
        self.agent: Optional[Agent] = None
        self.appointment: Optional[Appointment] = None
        self.memory: Dict = {}
        self.objections: List[Dict] = []
        self.sentiment: Dict = {"current": "neutral", "score": 0.5, "trend": "stable"}
        self.preferences: Dict = {}
        self.rag_results: List[Dict] = []
        self.booking_state: Optional[str] = None
        self.conversation_state: str = "unknown"
        self.message_count: int = 0
        self.hours_since_last_message: float = 0
        self.lead_tier: str = "cold"

    def to_dict(self) -> Dict:
        """Serialize context for logging/debugging."""
        return {
            "lead_id": str(self.lead.id) if self.lead else None,
            "lead_name": f"{self.lead.first_name} {self.lead.last_name}" if self.lead else None,
            "lead_score": self.lead.lead_score if self.lead else 0,
            "conversation_id": str(self.conversation.id) if self.conversation else None,
            "message_count": self.message_count,
            "campaign_name": self.campaign.name if self.campaign else None,
            "campaign_tone": self.campaign.tone if self.campaign else "friendly",
            "sentiment": self.sentiment,
            "objections_count": len(self.objections),
            "booking_state": self.booking_state,
            "conversation_state": self.conversation_state,
            "rag_results_count": len(self.rag_results),
            "hours_since_last_message": round(self.hours_since_last_message, 1),
            "lead_tier": self.lead_tier,
        }


class ContextBuilder:
    """Builds comprehensive context for AI response generation."""

    def __init__(self, db: Session):
        self.db = db
        self.memory_engine = MemoryEngine(db)

    def build(
        self,
        lead_id: UUID,
        conversation_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        include_rag: bool = False,
    ) -> ConversationContext:
        """
        Build full context from all available sources.

        Args:
            lead_id: Lead UUID
            conversation_id: Optional conversation UUID
            campaign_id: Optional campaign UUID
            include_rag: Whether to include RAG results (Phase 37)

        Returns:
            ConversationContext with all available data
        """
        ctx = ConversationContext()

        # 1. Load lead
        ctx.lead = self.db.query(Lead).filter(
            Lead.id == lead_id,
            Lead.deleted_at.is_(None),
        ).first()

        if not ctx.lead:
            return ctx

        # 2. Determine lead tier
        ctx.lead_tier = self._calculate_tier(ctx.lead)

        # 3. Load or find conversation
        if conversation_id:
            ctx.conversation = self.db.query(Conversation).filter(
                Conversation.id == conversation_id,
            ).first()
        else:
            ctx.conversation = (
                self.db.query(Conversation)
                .filter(
                    Conversation.lead_id == lead_id,
                    Conversation.status.in_(["active", "initiated", "booking"]),
                )
                .order_by(Conversation.created_at.desc())
                .first()
            )

        # 4. Load conversation messages
        if ctx.conversation:
            ctx.messages = self._load_messages(ctx.conversation.id)
            ctx.message_count = len(ctx.messages)

            # Calculate time since last message
            if ctx.messages:
                last_msg_time = ctx.messages[-1].get("created_at")
                if last_msg_time:
                    if isinstance(last_msg_time, str):
                        last_msg_time = datetime.fromisoformat(last_msg_time.replace("Z", "+00:00"))
                    delta = datetime.now(timezone.utc) - last_msg_time
                    ctx.hours_since_last_message = delta.total_seconds() / 3600

        # 5. Load campaign
        if campaign_id:
            ctx.campaign = self.db.query(Campaign).filter(
                Campaign.id == campaign_id,
                Campaign.deleted_at.is_(None),
            ).first()
        elif ctx.lead and ctx.lead.campaign_id:
            ctx.campaign = self.db.query(Campaign).filter(
                Campaign.id == ctx.lead.campaign_id,
                Campaign.deleted_at.is_(None),
            ).first()

        # 6. Load memory
        if ctx.conversation:
            ctx.memory = self.memory_engine.get_memory(ctx.conversation)
            ctx.objections = self.memory_engine.get_objections(ctx.conversation)
            ctx.sentiment = self.memory_engine.get_sentiment(ctx.conversation)
            ctx.preferences = self.memory_engine.get_preferences(ctx.conversation)

            # Get booking state from memory
            ctx.booking_state = ctx.memory.get("booking_state")
            ctx.conversation_state = ctx.conversation.status

        # 7. Load existing appointment
        ctx.appointment = (
            self.db.query(Appointment)
            .filter(
                Appointment.lead_id == lead_id,
                Appointment.status.in_(["pending", "confirmed"]),
            )
            .order_by(Appointment.start_time.desc())
            .first()
        )

        # 8. Load agent (if appointment exists)
        if ctx.appointment:
            ctx.agent = self.db.query(Agent).filter(
                Agent.id == ctx.appointment.agent_id,
            ).first()

        # 9. RAG results (placeholder for Phase 37)
        if include_rag:
            ctx.rag_results = self._retrieve_rag_results(ctx)

        return ctx

    def _load_messages(self, conversation_id: UUID, limit: int = 20) -> List[Dict]:
        """Load recent messages for a conversation."""
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
            .all()
        )

        return [
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "message_type": m.message_type,
                "intent": m.intent,
                "sentiment": m.sentiment,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]

    def _calculate_tier(self, lead: Lead) -> str:
        """Calculate lead tier from score."""
        score = lead.lead_score or 0
        if score >= 80:
            return "hot"
        elif score >= 60:
            return "warm"
        elif score >= 40:
            return "cool"
        else:
            return "cold"

    def _retrieve_rag_results(self, ctx: ConversationContext) -> List[Dict]:
        """
        Retrieve RAG results from vector store.

        Placeholder for Phase 37 — returns empty list.
        Will be connected to Qdrant/pgvector.
        """
        # TODO: Phase 37 — connect to vector store
        return []

    def build_summary(self, ctx: ConversationContext) -> str:
        """Build a text summary of the context for debugging."""
        parts = []

        if ctx.lead:
            parts.append(f"Lead: {ctx.lead.first_name} {ctx.lead.last_name} "
                         f"(score={ctx.lead.lead_score}, tier={ctx.lead_tier}, "
                         f"source={ctx.lead.source}, state={ctx.lead.state})")

        if ctx.conversation:
            parts.append(f"Conversation: {ctx.message_count} messages, "
                         f"state={ctx.conversation_state}, "
                         f"booking_state={ctx.booking_state}")

        if ctx.campaign:
            parts.append(f"Campaign: {ctx.campaign.name} (tone={ctx.campaign.tone})")

        if ctx.objections:
            obj_types = [o.get("type", "unknown") for o in ctx.objections]
            parts.append(f"Objections: {', '.join(obj_types)}")

        parts.append(f"Sentiment: {ctx.sentiment.get('current', 'neutral')} "
                     f"(score={ctx.sentiment.get('score', 0.5)}, "
                     f"trend={ctx.sentiment.get('trend', 'stable')})")

        if ctx.appointment:
            parts.append(f"Appointment: {ctx.appointment.status} at "
                         f"{ctx.appointment.start_time}")

        return " | ".join(parts)
