"""
Memory Engine (Step 5.5)

Stores and retrieves conversation context:
- Objections raised by the customer
- Sentiment trend over time
- Customer preferences
- Conversation history summary
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.lead import Lead


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class MemoryEngine:
    """Manages conversation memory and context."""

    def __init__(self, db: Session):
        self.db = db

    def get_memory(self, conversation: Conversation) -> Dict[str, Any]:
        """Get the full memory context for a conversation."""
        return conversation.ai_context or {}

    def update_memory(self, conversation: Conversation, updates: Dict[str, Any]) -> None:
        """Update memory context for a conversation."""
        memory = conversation.ai_context or {}
        memory.update(updates)
        memory["last_updated"] = datetime.now(timezone.utc).isoformat()
        conversation.ai_context = memory
        self.db.commit()

    # --- Objection Tracking ---

    def add_objection(self, conversation: Conversation, objection_type: str, text: str) -> None:
        """Record an objection raised by the customer."""
        memory = self.get_memory(conversation)
        objections = memory.get("objections", [])
        objections.append({
            "type": objection_type,
            "text": text[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 10 objections
        memory["objections"] = objections[-10:]
        self.update_memory(conversation, memory)

    def get_objections(self, conversation: Conversation) -> List[Dict]:
        """Get all objections for a conversation."""
        memory = self.get_memory(conversation)
        return memory.get("objections", [])

    def has_objection(self, conversation: Conversation, objection_type: str) -> bool:
        """Check if a specific objection type was raised."""
        objections = self.get_objections(conversation)
        return any(o["type"] == objection_type for o in objections)

    # --- Sentiment Tracking ---

    def update_sentiment(self, conversation: Conversation, sentiment: str, score: float = 0.5) -> None:
        """Update sentiment for a conversation."""
        memory = self.get_memory(conversation)
        sentiment_history = memory.get("sentiment_history", [])
        sentiment_history.append({
            "sentiment": sentiment,
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 20 sentiment entries
        memory["sentiment_history"] = sentiment_history[-20:]
        memory["current_sentiment"] = sentiment
        memory["sentiment_score"] = score
        self.update_memory(conversation, memory)

    def get_sentiment(self, conversation: Conversation) -> Dict[str, Any]:
        """Get current sentiment and history."""
        memory = self.get_memory(conversation)
        return {
            "current": memory.get("current_sentiment", "neutral"),
            "score": memory.get("sentiment_score", 0.5),
            "history": memory.get("sentiment_history", [])[-5:],
        }

    def get_sentiment_trend(self, conversation: Conversation) -> str:
        """
        Get sentiment trend: improving, stable, or declining.
        """
        memory = self.get_memory(conversation)
        history = memory.get("sentiment_history", [])
        if len(history) < 2:
            return "stable"

        recent = history[-3:]
        scores = [h.get("score", 0.5) for h in recent]
        avg_recent = sum(scores) / len(scores)

        older = history[-6:-3] if len(history) >= 6 else history[:len(history)//2]
        if older:
            avg_older = sum(h.get("score", 0.5) for h in older) / len(older)
            if avg_recent > avg_older + 0.1:
                return "improving"
            elif avg_recent < avg_older - 0.1:
                return "declining"

        return "stable"

    # --- Preferences ---

    def set_preference(self, conversation: Conversation, key: str, value: Any) -> None:
        """Store a customer preference."""
        memory = self.get_memory(conversation)
        preferences = memory.get("preferences", {})
        preferences[key] = value
        memory["preferences"] = preferences
        self.update_memory(conversation, memory)

    def get_preferences(self, conversation: Conversation) -> Dict[str, Any]:
        """Get all customer preferences."""
        memory = self.get_memory(conversation)
        return memory.get("preferences", {})

    def get_preference(self, conversation: Conversation, key: str, default: Any = None) -> Any:
        """Get a specific customer preference."""
        preferences = self.get_preferences(conversation)
        return preferences.get(key, default)

    # --- Message History ---

    def add_to_history(self, conversation: Conversation, role: str, content: str) -> None:
        """Add a message to conversation history summary."""
        memory = self.get_memory(conversation)
        history = memory.get("message_summary", [])
        history.append({
            "role": role,
            "content": content[:100],  # Truncate for memory
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 10 messages
        memory["message_summary"] = history[-10:]
        self.update_memory(conversation, memory)

    def get_history(self, conversation: Conversation) -> List[Dict]:
        """Get conversation history summary."""
        memory = self.get_memory(conversation)
        return memory.get("message_summary", [])

    # --- Context Building ---

    def build_context(self, conversation: Conversation) -> str:
        """
        Build a text context summary for LLM prompts.
        """
        memory = self.get_memory(conversation)
        parts = []

        # Objections
        objections = memory.get("objections", [])
        if objections:
            obj_types = [o["type"] for o in objections]
            parts.append(f"Customer has raised objections about: {', '.join(set(obj_types))}")

        # Sentiment
        sentiment = memory.get("current_sentiment", "neutral")
        parts.append(f"Customer sentiment: {sentiment}")

        # Preferences
        preferences = memory.get("preferences", {})
        if preferences:
            prefs = [f"{k}: {v}" for k, v in preferences.items()]
            parts.append(f"Preferences: {', '.join(prefs)}")

        # Trend
        trend = self.get_sentiment_trend(conversation)
        if trend != "stable":
            parts.append(f"Sentiment trend: {trend}")

        return "\n".join(parts) if parts else "No prior context."


def analyze_sentiment(text: str) -> Tuple[str, float]:
    """
    Simple sentiment analysis based on keyword matching.

    Returns:
        Tuple of (sentiment, score)
        score: 0.0 (very negative) to 1.0 (very positive)
    """
    lower = text.lower()
    # Normalize contractions
    lower = lower.replace("i'm", "i am").replace("i've", "i have").replace("don't", "do not")
    lower = lower.replace("can't", "cannot").replace("won't", "will not").replace("isn't", "is not")
    words = lower.split()

    positive_words = {"yes", "yeah", "yep", "sure", "great", "perfect", "awesome", "love", "interested", "good", "wonderful", "excellent", "amazing", "fantastic", "happy", "glad", "absolutely", "definitely", "excited", "eager", "ready", "am", "like", "want"}
    negative_words = {"no", "nah", "nope", "not", "never", "stop", "hate", "terrible", "awful", "bad", "worst", "annoying", "frustrated", "angry", "disappointed", "unhappy", "unsubscribe", "remove", "cancel"}
    neutral_words = {"maybe", "perhaps", "possibly", "think", "consider", "unsure", "okay", "ok", "fine"}

    pos_count = sum(1 for w in words if w in positive_words)
    neg_count = sum(1 for w in words if w in negative_words)
    neu_count = sum(1 for w in words if w in neutral_words)

    total = pos_count + neg_count + neu_count
    if total == 0:
        return Sentiment.NEUTRAL.value, 0.5

    score = (pos_count - neg_count) / total
    normalized_score = (score + 1) / 2  # Normalize to 0-1

    if normalized_score > 0.6:
        return Sentiment.POSITIVE.value, normalized_score
    elif normalized_score < 0.4:
        return Sentiment.NEGATIVE.value, normalized_score
    else:
        return Sentiment.NEUTRAL.value, normalized_score


# Type hint fix
