"""
Real-Time Coaching Engine (Phase 42.3)

Provides live coaching during calls:
- Suggested rebuttals for objections
- Next question recommendations
- Closing technique suggestions
- Compliance warnings
- Sentiment alerts

Coaching is triggered by real-time transcript analysis
and delivered via WebSocket to the agent's UI.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

try:
    from app.realtime.websocket import socketio_server
except ImportError:
    socketio_server = None
from app.intent.services.objections import detect_objection, ObjectionType

logger = logging.getLogger(__name__)


# Coaching cue types
CUE_REBUTTAL = "rebuttal"
CUE_QUESTION = "question"
CUE_CLOSING = "closing"
CUE_COMPLIANCE = "compliance"
CUE_SENTIMENT = "sentiment"
CUE_RAPPORT = "rapport"


# Objection rebuttal templates
REBUTTAL_TEMPLATES = {
    ObjectionType.PRICING: [
        "I understand cost is important. Let me show you how our coverage actually saves money long-term.",
        "Many customers felt the same way initially. When they saw the full value, the price made sense.",
        "We have flexible plans that can fit most budgets. Would you like to see options at different price points?",
    ],
    ObjectionType.TRUST: [
        "That's a fair concern. We've been in business for over 20 years with an A+ rating.",
        "I completely understand wanting to verify. Can I share some customer testimonials?",
        "We're fully licensed and insured. I can send you our credentials for your review.",
    ],
    ObjectionType.TIMING: [
        "I understand timing is important. Insurance needs don't wait though — let's find a time that works.",
        "Would it help if I sent you information to review at your own pace?",
        "Many customers find that a quick 10-minute call is all they need to get started.",
    ],
    ObjectionType.ALREADY_COVERED: [
        "That's great! Many customers find they can get better coverage or save money with a review.",
        "Would you like a free comparison? It takes just 5 minutes and there's no obligation.",
        "Coverage needs change over time. When was your last policy review?",
    ],
    ObjectionType.NEED_TO_THINK: [
        "Absolutely, it's a big decision. What specific questions can I answer to help?",
        "I understand. Would it help if I sent you a summary of what we discussed?",
        "Take your time. Can I follow up in a few days to see if you have any questions?",
    ],
    ObjectionType.NOT_INTERESTED: [
        "I respect that. Just so I understand, is it the timing or the coverage that's not right?",
        "No problem at all. Would you like me to send you some information for future reference?",
        "I appreciate your honesty. If anything changes, we're here to help.",
    ],
}

# Closing techniques
CLOSING_TECHNIQUES = {
    "trial_close": [
        "Based on what we discussed, does this coverage seem like a good fit?",
        "How does this plan sound so far?",
        "Is this the kind of protection you're looking for?",
    ],
    "assumptive_close": [
        "Shall we go ahead and get your coverage started today?",
        "I can have your policy set up within 24 hours. Would you like to proceed?",
        "Let me get your information to start the application.",
    ],
    "urgency_close": [
        "This rate is available for a limited time. Shall we lock it in?",
        "The sooner you're covered, the sooner you're protected.",
        "Would you like to start coverage today or tomorrow?",
    ],
    "alternative_close": [
        "Would you prefer the basic plan or the comprehensive coverage?",
        "Should we start with liability only or full coverage?",
        "Would monthly or annual billing work better for you?",
    ],
}

# Compliance warnings
COMPLIANCE_WARNINGS = {
    "medical_advice": "⚠ COMPLIANCE: Do not provide medical advice or recommendations.",
    "legal_advice": "⚠ COMPLIANCE: Do not provide legal advice or recommendations.",
    "guarantee": "⚠ COMPLIANCE: Avoid guaranteeing specific outcomes or savings.",
    "false_urgency": "⚠ COMPLIANCE: Use only genuine urgency, not false deadlines.",
    "competitor_bashing": "⚠ COMPLIANCE: Focus on your value, not competitor weaknesses.",
}


class CoachingCue:
    """A real-time coaching cue for an agent."""

    def __init__(
        self,
        cue_type: str,
        priority: str,
        title: str,
        suggestion: str,
        context: str = "",
    ):
        self.cue_type = cue_type
        self.priority = priority  # high, medium, low
        self.title = title
        self.suggestion = suggestion
        self.context = context
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "cue_type": self.cue_type,
            "priority": self.priority,
            "title": self.title,
            "suggestion": self.suggestion,
            "context": self.context,
            "timestamp": self.timestamp,
        }


class RealTimeCoach:
    """
    Provides real-time coaching during live calls.

    Features:
    - Objection detection and rebuttal suggestions
    - Closing technique recommendations
    - Compliance warnings
    - Sentiment monitoring
    - Question suggestions
    """

    def __init__(self, db: Session):
        self.db = db
        self._recent_cues: Dict[str, List[Dict]] = {}  # agent_id -> recent cues

    def process_transcript_segment(
        self,
        agent_id: str,
        tenant_id: str,
        speaker: str,
        text: str,
        conversation_context: List[Dict] = None,
    ) -> List[CoachingCue]:
        """
        Process a transcript segment and generate coaching cues.

        Args:
            agent_id: Agent ID
            tenant_id: Tenant ID
            speaker: "agent" or "customer"
            text: Segment text
            conversation_context: Recent conversation segments

        Returns:
            List of CoachingCue objects
        """
        cues = []

        if speaker == "customer":
            # Check for objections
            objection_cues = self._handle_customer_objection(text)
            cues.extend(objection_cues)

            # Check sentiment
            sentiment_cues = self._check_customer_sentiment(text)
            cues.extend(sentiment_cues)

        elif speaker == "agent":
            # Check compliance
            compliance_cues = self._check_compliance(text)
            cues.extend(compliance_cues)

            # Check for closing opportunities
            if conversation_context:
                closing_cues = self._suggest_closing(conversation_context, text)
                cues.extend(closing_cues)

        # Deduplicate (don't repeat same cue within 60s)
        cues = self._deduplicate_cues(agent_id, cues)

        # Send via WebSocket
        for cue in cues:
            self._send_coaching_cue(agent_id, tenant_id, cue)

        return cues

    def _handle_customer_objection(self, text: str) -> List[CoachingCue]:
        """Handle customer objections with rebuttal suggestions."""
        cues = []

        objection_type, confidence = detect_objection(text)

        if objection_type and objection_type.value != "unknown" and confidence > 0.6:
            templates = REBUTTAL_TEMPLATES.get(objection_type, [])
            if templates:
                import random
                rebuttal = random.choice(templates)

                cues.append(CoachingCue(
                    cue_type=CUE_REBUTTAL,
                    priority="high",
                    title=f"Objection: {objection_type.value}",
                    suggestion=rebuttal,
                    context=f"Customer said: '{text[:100]}'",
                ))

        return cues

    def _check_customer_sentiment(self, text: str) -> List[CoachingCue]:
        """Check customer sentiment and alert if negative."""
        cues = []

        negative_indicators = [
            r"\b(?:frustrated|annoyed|angry|upset|disappointed)\b",
            r"\b(?:waste of time|rip off|scam|unprofessional)\b",
            r"\b(?:cancel|stop|don't call|remove me)\b",
        ]

        for pattern in negative_indicators:
            if re.search(pattern, text, re.IGNORECASE):
                cues.append(CoachingCue(
                    cue_type=CUE_SENTIMENT,
                    priority="high",
                    title="Negative Sentiment Detected",
                    suggestion="Acknowledge their frustration and offer to help. Consider escalating if needed.",
                    context=f"Customer said: '{text[:100]}'",
                ))
                break

        return cues

    def _check_compliance(self, text: str) -> List[CoachingCue]:
        """Check agent speech for compliance violations."""
        cues = []

        compliance_checks = {
            "medical_advice": [
                r"\b(?:you should|I recommend)\s+(?:take|use|stop)\s+(?:medication|medicine)\b",
                r"\b(?:diagnos|treat|cure|prescri)\w*\b",
            ],
            "guarantee": [
                r"\bguarantee[ds]?\b.*\b(?:save|cover|pay|protect)\b",
                r"\b(?:will definitely|certainly will|absolutely will)\b",
            ],
            "false_urgency": [
                r"\b(?:last chance|final offer|expires today)\b",
            ],
        }

        for violation_type, patterns in compliance_checks.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    warning = COMPLIANCE_WARNINGS.get(violation_type, "Compliance issue detected.")
                    cues.append(CoachingCue(
                        cue_type=CUE_COMPLIANCE,
                        priority="high",
                        title=f"Compliance Warning: {violation_type}",
                        suggestion=warning,
                        context=f"You said: '{text[:100]}'",
                    ))
                    break

        return cues

    def _suggest_closing(self, context: List[Dict], current_text: str) -> List[CoachingCue]:
        """Suggest closing techniques based on conversation flow."""
        cues = []

        # Count customer engagement signals
        engagement_count = 0
        for seg in context:
            if seg.get("speaker") == "customer":
                text = seg.get("text", "").lower()
                if any(w in text for w in ["yes", "sure", "sounds good", "interested", "tell me more"]):
                    engagement_count += 1

        # If customer is engaged, suggest closing
        if engagement_count >= 2:
            import random
            technique = random.choice(list(CLOSING_TECHNIQUES.keys()))
            suggestion = random.choice(CLOSING_TECHNIQUES[technique])

            cues.append(CoachingCue(
                cue_type=CUE_CLOSING,
                priority="medium",
                title="Closing Opportunity",
                suggestion=suggestion,
                context=f"Customer has shown {engagement_count} engagement signals.",
            ))

        return cues

    def _deduplicate_cues(self, agent_id: str, cues: List[CoachingCue]) -> List[CoachingCue]:
        """Remove duplicate cues within 60 seconds."""
        if agent_id not in self._recent_cues:
            self._recent_cues[agent_id] = []

        now = datetime.now(timezone.utc)
        recent = self._recent_cues[agent_id]

        # Clean old cues (older than 60s)
        recent = [r for r in recent if (now - datetime.fromisoformat(r["time"])).seconds < 60]
        self._recent_cues[agent_id] = recent

        # Filter duplicates
        unique_cues = []
        for cue in cues:
            is_dup = any(
                r["type"] == cue.cue_type and r["title"] == cue.title
                for r in recent
            )
            if not is_dup:
                unique_cues.append(cue)
                recent.append({
                    "type": cue.cue_type,
                    "title": cue.title,
                    "time": now.isoformat(),
                })

        return unique_cues

    def _send_coaching_cue(self, agent_id: str, tenant_id: str, cue: CoachingCue) -> None:
        """Send coaching cue to agent via WebSocket."""
        try:
            if socketio_server:
                socketio_server.emit(
                    "coaching:cue",
                    cue.to_dict(),
                    room=f"agent:{agent_id}",
                )
        except Exception as e:
            logger.warning(f"Failed to send coaching cue: {e}")

    def get_suggested_questions(
        self,
        conversation_context: List[Dict],
    ) -> List[str]:
        """Generate suggested questions based on conversation context."""
        suggestions = []

        # Analyze what's been discussed
        topics_covered = set()
        for seg in conversation_context:
            text = seg.get("text", "").lower()
            if "coverage" in text or "policy" in text:
                topics_covered.add("coverage")
            if "price" in text or "cost" in text or "premium" in text:
                topics_covered.add("pricing")
            if "family" in text or "spouse" in text or "children" in text:
                topics_covered.add("family")

        # Suggest questions for uncovered topics
        if "coverage" not in topics_covered:
            suggestions.append("What type of coverage are you most interested in?")

        if "pricing" not in topics_covered:
            suggestions.append("What's your monthly budget for insurance?")

        if "family" not in topics_covered:
            suggestions.append("Are you looking to cover just yourself or your family as well?")

        # Always suggest discovery
        suggestions.append("What's most important to you in an insurance plan?")

        return suggestions[:3]
