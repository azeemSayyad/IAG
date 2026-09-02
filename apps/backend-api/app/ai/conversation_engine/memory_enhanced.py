"""
Enhanced AI Memory System (Step 36.3)

Extends the existing memory engine with:
- Memory Snapshots — periodic conversation summaries
- Lead Context Store — cross-conversation lead memory
- Embedding Preparation — ready for Phase 37 vector search

Memory Layers:
1. Short-term (conversation-level) — current conversation state
2. Medium-term (lead-level) — lead preferences and history
3. Long-term (embedding-level) — semantic memory (Phase 37)
"""

import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.intent.services.memory import MemoryEngine


class MemorySnapshot:
    """A point-in-time snapshot of conversation memory."""

    def __init__(
        self,
        conversation_id: str,
        lead_id: str,
        summary: str,
        objections: List[Dict],
        sentiment: Dict,
        preferences: Dict,
        message_count: int,
        booking_state: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ):
        self.conversation_id = conversation_id
        self.lead_id = lead_id
        self.summary = summary
        self.objections = objections
        self.sentiment = sentiment
        self.preferences = preferences
        self.message_count = message_count
        self.booking_state = booking_state
        self.created_at = created_at or datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        return {
            "conversation_id": self.conversation_id,
            "lead_id": self.lead_id,
            "summary": self.summary,
            "objections": self.objections,
            "sentiment": self.sentiment,
            "preferences": self.preferences,
            "message_count": self.message_count,
            "booking_state": self.booking_state,
            "created_at": self.created_at.isoformat(),
        }

    def to_embedding_text(self) -> str:
        """Convert snapshot to text suitable for embedding generation."""
        parts = [
            f"Lead: {self.lead_id}",
            f"Summary: {self.summary}",
            f"Sentiment: {self.sentiment.get('current', 'neutral')}",
        ]

        if self.objections:
            obj_texts = [o.get("type", "") for o in self.objections]
            parts.append(f"Objections: {', '.join(obj_texts)}")

        if self.booking_state:
            parts.append(f"Booking state: {self.booking_state}")

        return " | ".join(parts)


class LeadContext:
    """Cross-conversation lead memory and context."""

    def __init__(self, lead_id: str):
        self.lead_id = lead_id
        self.total_conversations: int = 0
        self.total_messages: int = 0
        self.objection_history: List[Dict] = []
        self.sentiment_history: List[Dict] = []
        self.preferences: Dict = {}
        self.booking_history: List[Dict] = []
        self.communication_style: str = "unknown"
        self.best_contact_time: Optional[str] = None
        self.response_pattern: str = "unknown"  # fast, slow, inconsistent
        self.engagement_score: float = 0.5
        self.last_updated: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "lead_id": self.lead_id,
            "total_conversations": self.total_conversations,
            "total_messages": self.total_messages,
            "objection_count": len(self.objection_history),
            "preferences": self.preferences,
            "communication_style": self.communication_style,
            "best_contact_time": self.best_contact_time,
            "response_pattern": self.response_pattern,
            "engagement_score": round(self.engagement_score, 3),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    def to_prompt_context(self) -> str:
        """Convert lead context to text for LLM prompt injection."""
        parts = []

        if self.communication_style != "unknown":
            parts.append(f"Communication style: {self.communication_style}")

        if self.response_pattern != "unknown":
            parts.append(f"Response pattern: {self.response_pattern}")

        if self.best_contact_time:
            parts.append(f"Preferred contact time: {self.best_contact_time}")

        if self.preferences:
            pref_text = ", ".join(f"{k}={v}" for k, v in self.preferences.items())
            parts.append(f"Preferences: {pref_text}")

        if self.objection_history:
            recent_obj = self.objection_history[-3:]
            obj_types = [o.get("type", "") for o in recent_obj]
            parts.append(f"Recent objections: {', '.join(obj_types)}")

        if self.total_conversations > 1:
            parts.append(f"Previous conversations: {self.total_conversations}")

        return " | ".join(parts) if parts else ""


class EnhancedMemoryEngine:
    """
    Enhanced memory system with snapshots and lead context.

    Extends MemoryEngine with:
    - Snapshot creation and retrieval
    - Cross-conversation lead context
    - Embedding preparation
    """

    def __init__(self, db: Session):
        self.db = db
        self.base_memory = MemoryEngine(db)

    # --- Memory Snapshots ---

    def create_snapshot(self, conversation: Conversation) -> MemorySnapshot:
        """
        Create a memory snapshot from the current conversation state.

        Snapshots are stored in conversation.ai_context["snapshots"].
        """
        memory = self.base_memory.get_memory(conversation)

        snapshot = MemorySnapshot(
            conversation_id=str(conversation.id),
            lead_id=str(conversation.lead_id),
            summary=self._generate_summary(conversation),
            objections=self.base_memory.get_objections(conversation),
            sentiment=self.base_memory.get_sentiment(conversation),
            preferences=self.base_memory.get_preferences(conversation),
            message_count=conversation.message_count,
            booking_state=memory.get("booking_state"),
        )

        # Store in conversation context
        snapshots = memory.get("snapshots", [])
        snapshots.append(snapshot.to_dict())
        # Keep last 5 snapshots
        memory["snapshots"] = snapshots[-5:]
        memory["last_snapshot_at"] = datetime.now(timezone.utc).isoformat()

        self.base_memory.update_memory(conversation, memory)

        return snapshot

    def get_snapshots(self, conversation: Conversation) -> List[Dict]:
        """Get all snapshots for a conversation."""
        memory = self.base_memory.get_memory(conversation)
        return memory.get("snapshots", [])

    def should_create_snapshot(self, conversation: Conversation) -> bool:
        """
        Determine if a new snapshot should be created.

        Triggers:
        - Every 10 messages
        - After 1 hour of inactivity
        - On state transition
        """
        memory = self.base_memory.get_memory(conversation)
        snapshots = memory.get("snapshots", [])

        # No snapshots yet with messages
        if not snapshots and conversation.message_count >= 5:
            return True

        # Every 10 messages
        if snapshots:
            last_snapshot_msgs = snapshots[-1].get("message_count", 0)
            if conversation.message_count - last_snapshot_msgs >= 10:
                return True

        # After inactivity
        last_snapshot_at = memory.get("last_snapshot_at")
        if last_snapshot_at:
            try:
                last_time = datetime.fromisoformat(last_snapshot_at)
                if datetime.now(timezone.utc) - last_time > timedelta(hours=1):
                    return True
            except (ValueError, TypeError):
                pass

        return False

    # --- Lead Context Store ---

    def get_lead_context(self, lead_id: UUID) -> LeadContext:
        """
        Get cross-conversation context for a lead.

        Aggregates data from all conversations with this lead.
        """
        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return LeadContext(str(lead_id))

        # Get all conversations for this lead
        conversations = (
            self.db.query(Conversation)
            .filter(Conversation.lead_id == lead_id)
            .order_by(Conversation.created_at.desc())
            .all()
        )

        ctx = LeadContext(str(lead_id))
        ctx.total_conversations = len(conversations)

        # Aggregate from conversations
        all_objections = []
        all_sentiments = []
        total_messages = 0

        for conv in conversations:
            memory = self.base_memory.get_memory(conv)
            total_messages += conv.message_count

            # Aggregate objections
            objections = memory.get("objections", [])
            all_objections.extend(objections)

            # Aggregate sentiment
            sentiment_history = memory.get("sentiment_history", [])
            all_sentiments.extend(sentiment_history)

            # Merge preferences
            prefs = memory.get("preferences", {})
            for k, v in prefs.items():
                if k not in ctx.preferences:
                    ctx.preferences[k] = v

        ctx.total_messages = total_messages
        ctx.objection_history = all_objections[-20:]  # Keep last 20
        ctx.sentiment_history = all_sentiments[-50:]  # Keep last 50

        # Analyze communication style
        ctx.communication_style = self._analyze_communication_style(conversations)
        ctx.response_pattern = self._analyze_response_pattern(conversations)
        ctx.engagement_score = self._calculate_engagement_score(conversations)

        ctx.last_updated = datetime.now(timezone.utc)

        return ctx

    def update_lead_context(
        self,
        lead_id: UUID,
        updates: Dict[str, Any],
    ) -> None:
        """
        Update lead-level context.

        Stores in the lead's custom_fields or a dedicated context store.
        """
        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return

        # Store in custom_fields
        custom = lead.custom_fields or {}
        ai_context = custom.get("ai_context", {})
        ai_context.update(updates)
        ai_context["last_updated"] = datetime.now(timezone.utc).isoformat()
        custom["ai_context"] = ai_context
        lead.custom_fields = custom
        self.db.commit()

    # --- Embedding Preparation ---

    def prepare_embedding_data(
        self,
        conversation: Conversation,
    ) -> Optional[Dict]:
        """
        Prepare conversation data for embedding generation.

        Returns data that can be passed to the vector store (Phase 37).
        """
        memory = self.base_memory.get_memory(conversation)
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
            .limit(50)
            .all()
        )

        if not messages:
            return None

        # Create embedding text
        message_texts = []
        for msg in messages:
            prefix = "Customer" if msg.sender == "customer" else "Agent"
            message_texts.append(f"{prefix}: {msg.content[:200]}")

        embedding_text = "\n".join(message_texts)

        # Create unique ID
        content_hash = hashlib.md5(embedding_text.encode()).hexdigest()[:12]

        return {
            "id": f"conv_{conversation.id}_{content_hash}",
            "text": embedding_text,
            "metadata": {
                "conversation_id": str(conversation.id),
                "lead_id": str(conversation.lead_id),
                "tenant_id": str(conversation.tenant_id),
                "message_count": len(messages),
                "objections": [o.get("type") for o in memory.get("objections", [])],
                "sentiment": memory.get("sentiment", {}).get("current", "neutral"),
                "status": conversation.status,
                "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            },
        }

    def prepare_objection_embedding(
        self,
        objection_type: str,
        objection_text: str,
        response_text: str,
        was_successful: bool,
    ) -> Dict:
        """
        Prepare an objection/response pair for embedding.

        Used to build a knowledge base of successful objection handling.
        """
        embedding_text = (
            f"Objection: {objection_text}\n"
            f"Response: {response_text}\n"
            f"Type: {objection_type}\n"
            f"Successful: {was_successful}"
        )

        content_hash = hashlib.md5(embedding_text.encode()).hexdigest()[:12]

        return {
            "id": f"obj_{objection_type}_{content_hash}",
            "text": embedding_text,
            "metadata": {
                "objection_type": objection_type,
                "was_successful": was_successful,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    # --- Private Helpers ---

    def _generate_summary(self, conversation: Conversation) -> str:
        """Generate a brief summary of the conversation."""
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
            .limit(10)
            .all()
        )

        if not messages:
            return "No messages yet"

        customer_msgs = [m for m in messages if m.sender == "customer"]
        ai_msgs = [m for m in messages if m.sender == "ai"]

        summary_parts = [
            f"{len(messages)} messages exchanged",
        ]

        if customer_msgs:
            last_customer = customer_msgs[-1].content[:100]
            summary_parts.append(f"Last customer message: '{last_customer}'")

        memory = self.base_memory.get_memory(conversation)
        if memory.get("objections"):
            obj_types = [o.get("type") for o in memory["objections"]]
            summary_parts.append(f"Objections: {', '.join(obj_types)}")

        return " | ".join(summary_parts)

    def _analyze_communication_style(self, conversations: List[Conversation]) -> str:
        """Analyze lead's communication style from message history."""
        total_chars = 0
        total_customer_msgs = 0
        has_emoji = False
        has_slang = False

        slang_words = {"u", "r", "ur", "lol", "omg", "tbh", "imo", "idk", "pls", "plz", "thx"}

        for conv in conversations[:5]:  # Last 5 conversations
            messages = (
                self.db.query(Message)
                .filter(
                    Message.conversation_id == conv.id,
                    Message.sender == "customer",
                )
                .limit(20)
                .all()
            )

            for msg in messages:
                total_customer_msgs += 1
                total_chars += len(msg.content)

                if any(ord(c) > 127 for c in msg.content):
                    has_emoji = True

                words = set(msg.content.lower().split())
                if words & slang_words:
                    has_slang = True

        if total_customer_msgs == 0:
            return "unknown"

        avg_length = total_chars / total_customer_msgs

        if avg_length < 30 and has_slang:
            return "casual"
        elif avg_length > 100:
            return "detailed"
        elif has_emoji:
            return "expressive"
        else:
            return "standard"

    def _analyze_response_pattern(self, conversations: List[Conversation]) -> str:
        """Analyze how quickly the lead typically responds."""
        response_times = []

        for conv in conversations[:5]:
            messages = (
                self.db.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at)
                .all()
            )

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
            return "unknown"

        avg_seconds = sum(response_times) / len(response_times)

        if avg_seconds < 300:  # < 5 minutes
            return "fast"
        elif avg_seconds < 3600:  # < 1 hour
            return "moderate"
        else:
            return "slow"

    def _calculate_engagement_score(self, conversations: List[Conversation]) -> float:
        """Calculate engagement score (0-1) from conversation history."""
        if not conversations:
            return 0.5

        scores = []

        for conv in conversations[:5]:
            # Message count score
            msg_score = min(1.0, conv.message_count / 10)

            # Status score
            status_scores = {
                "booked": 1.0,
                "active": 0.7,
                "initiated": 0.3,
                "stopped": 0.1,
                "closed": 0.5,
            }
            status_score = status_scores.get(conv.status, 0.5)

            scores.append((msg_score + status_score) / 2)

        return sum(scores) / len(scores)
