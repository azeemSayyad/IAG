"""
Call Analysis Service (Phase 41.4)

Analyzes call transcripts for:
- Objection detection
- Sentiment analysis
- Compliance checking
- Interruption rate
- Engagement scoring

Uses NLP and pattern matching to extract insights.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.calls.models import CallTranscript, CallAnalysis
from app.intent.services.objections import detect_objection, ObjectionType

logger = logging.getLogger(__name__)


# Compliance violation patterns
COMPLIANCE_PATTERNS = {
    "medical_advice": [
        r"\b(?:you should|I recommend|you need to)\s+(?:take|use|stop)\s+(?:medication|medicine|drug)\b",
        r"\b(?:diagnos|treat|cure|prescri)\w*\b",
    ],
    "legal_advice": [
        r"\b(?:you should|I recommend)\s+(?:sue|file suit|take legal)\b",
        r"\b(?:legal advice|attorney|lawyer)\b.*\b(?:recommend|suggest)\b",
    ],
    "guaranteed_outcomes": [
        r"\bguarantee[ds]?\b.*\b(?:save|cover|pay|protect)\b",
        r"\b(?:will definitely|certainly will|absolutely will)\b",
    ],
    "false_urgency": [
        r"\b(?:last chance|final offer|expires today|act now)\b",
        r"\b(?:limited time|only today|don't wait)\b",
    ],
    "competitor_bashing": [
        r"\b(?:competitor|rival|other company)\b.*\b(?:bad|terrible|awful|scam)\b",
    ],
}

# Engagement indicators
ENGAGEMENT_INDICATORS = {
    "questions": [
        r"\?",
        r"\b(?:what|how|when|where|why|who|which|can you|could you)\b",
    ],
    "agreement": [
        r"\b(?:yes|yeah|sure|absolutely|definitely|exactly|right|correct)\b",
    ],
    "objection": [
        r"\b(?:but|however|although|concern|worried|not sure|expensive)\b",
    ],
    "interest": [
        r"\b(?:interested|tell me more|how does|what about|sounds good)\b",
    ],
}


class CallAnalyzer:
    """
    Analyzes call transcripts for insights.

    Features:
    - Objection detection and tracking
    - Sentiment analysis
    - Compliance checking
    - Engagement scoring
    - Interruption detection
    """

    def __init__(self, db: Session = None):
        self.db = db

    def analyze_transcript(self, transcript_id: UUID) -> Optional[CallAnalysis]:
        """
        Analyze a transcript and store results.

        Args:
            transcript_id: Transcript UUID

        Returns:
            CallAnalysis object
        """
        transcript = self.db.query(CallTranscript).filter(
            CallTranscript.id == transcript_id,
        ).first()

        if not transcript:
            return None

        segments = transcript.segments or []
        full_text = transcript.full_text or ""

        # 1. Detect objections
        objections = self._detect_objections(segments)

        # 2. Analyze sentiment
        sentiment = self._analyze_sentiment(segments)

        # 3. Check compliance
        compliance = self._check_compliance(segments)

        # 4. Calculate engagement
        engagement = self._calculate_engagement(segments, full_text)

        # 5. Detect interruptions
        interruptions = self._detect_interruptions(segments)

        # 6. Extract key points
        key_points = self._extract_key_points(segments)

        # 7. Calculate probability to close
        prob_close = self._calculate_close_probability(
            objections, sentiment, engagement, compliance
        )

        # Create analysis record
        analysis = CallAnalysis(
            tenant_id=transcript.tenant_id,
            transcript_id=transcript.id,
            recording_id=transcript.recording_id,
            appointment_id=transcript.appointment_id,
            lead_id=transcript.lead_id,
            agent_id=transcript.agent_id,

            # Objections
            objections_detected=objections["detected"],
            objection_count=objections["count"],
            objections_handled=objections["handled"],

            # Sentiment
            overall_sentiment=sentiment["overall"],
            sentiment_score=sentiment["score"],
            sentiment_timeline=sentiment["timeline"],

            # Engagement
            engagement_score=engagement["score"],
            interruption_count=interruptions,
            silence_periods=engagement["silence_periods"],
            questions_asked=engagement["questions_asked"],

            # Compliance
            compliance_violations=compliance["violations"],
            compliance_score=compliance["score"],

            # Summary
            key_points=key_points,
            next_steps=self._extract_next_steps(segments),
            probability_to_close=prob_close,
        )

        self.db.add(analysis)
        self.db.commit()
        self.db.refresh(analysis)

        logger.info(f"Analyzed transcript {transcript_id}: {objections['count']} objections, sentiment={sentiment['overall']}")
        return analysis

    def _detect_objections(self, segments: List[Dict]) -> Dict[str, Any]:
        """Detect objections in transcript segments."""
        detected = []
        handled = 0

        for i, seg in enumerate(segments):
            if seg.get("speaker") == "customer":
                text = seg.get("text", "")
                objection_type, confidence = detect_objection(text)

                if objection_type and objection_type.value != "unknown":
                    # Check if next agent segment addresses it
                    is_handled = False
                    if i + 1 < len(segments):
                        next_seg = segments[i + 1]
                        if next_seg.get("speaker") == "agent":
                            is_handled = True
                            handled += 1

                    detected.append({
                        "type": objection_type.value,
                        "text": text[:200],
                        "timestamp": seg.get("start_time", 0),
                        "handled": is_handled,
                    })

        return {
            "detected": detected,
            "count": len(detected),
            "handled": handled,
        }

    def _analyze_sentiment(self, segments: List[Dict]) -> Dict[str, Any]:
        """Analyze sentiment throughout the call."""
        positive_words = {"great", "good", "excellent", "perfect", "wonderful", "love", "like", "yes", "sure", "absolutely"}
        negative_words = {"bad", "terrible", "awful", "hate", "dislike", "no", "never", "worried", "concerned", "expensive"}

        timeline = []
        total_score = 0
        segment_count = 0

        for seg in segments:
            text = seg.get("text", "").lower()
            words = set(text.split())

            pos_count = len(words & positive_words)
            neg_count = len(words & negative_words)

            if pos_count + neg_count > 0:
                score = pos_count / (pos_count + neg_count)
            else:
                score = 0.5

            sentiment = "positive" if score > 0.6 else ("negative" if score < 0.4 else "neutral")

            timeline.append({
                "timestamp": seg.get("start_time", 0),
                "sentiment": sentiment,
                "score": round(score, 3),
                "speaker": seg.get("speaker", "unknown"),
            })

            total_score += score
            segment_count += 1

        overall_score = total_score / segment_count if segment_count > 0 else 0.5
        overall = "positive" if overall_score > 0.6 else ("negative" if overall_score < 0.4 else "neutral")

        return {
            "overall": overall,
            "score": round(overall_score, 3),
            "timeline": timeline,
        }

    def _check_compliance(self, segments: List[Dict]) -> Dict[str, Any]:
        """Check for compliance violations."""
        violations = []

        for seg in segments:
            if seg.get("speaker") == "agent":
                text = seg.get("text", "")

                for violation_type, patterns in COMPLIANCE_PATTERNS.items():
                    for pattern in patterns:
                        if re.search(pattern, text, re.IGNORECASE):
                            violations.append({
                                "type": violation_type,
                                "text": text[:200],
                                "timestamp": seg.get("start_time", 0),
                            })
                            break

        # Score: 1.0 = no violations, decreases with each violation
        score = max(0, 1.0 - len(violations) * 0.1)

        return {
            "violations": violations,
            "score": round(score, 3),
        }

    def _calculate_engagement(self, segments: List[Dict], full_text: str) -> Dict[str, Any]:
        """Calculate engagement metrics."""
        questions_asked = 0
        silence_periods = 0
        engagement_signals = 0

        for seg in segments:
            text = seg.get("text", "")

            # Count questions
            if "?" in text or re.search(r"\b(?:what|how|when|where|why|who|which)\b", text, re.IGNORECASE):
                questions_asked += 1

            # Count engagement signals
            for category, patterns in ENGAGEMENT_INDICATORS.items():
                for pattern in patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        engagement_signals += 1
                        break

        # Calculate score
        total_segments = len(segments)
        if total_segments > 0:
            score = min(1.0, engagement_signals / (total_segments * 0.3))
        else:
            score = 0.5

        return {
            "score": round(score, 3),
            "questions_asked": questions_asked,
            "silence_periods": silence_periods,
            "engagement_signals": engagement_signals,
        }

    def _detect_interruptions(self, segments: List[Dict]) -> int:
        """Detect interruptions in the call."""
        interruptions = 0

        for i in range(1, len(segments)):
            prev = segments[i - 1]
            curr = segments[i]

            # If speaker changes and there's time overlap
            if prev.get("speaker") != curr.get("speaker"):
                if curr.get("start_time", 0) < prev.get("end_time", 0):
                    interruptions += 1

        return interruptions

    def _extract_key_points(self, segments: List[Dict]) -> List[str]:
        """Extract key points from the call."""
        key_points = []

        # Look for agent statements with key information
        for seg in segments:
            if seg.get("speaker") == "agent":
                text = seg.get("text", "")

                # Coverage mentions
                if re.search(r"\b(?:coverage|policy|plan|protection)\b", text, re.IGNORECASE):
                    key_points.append(f"Coverage discussed: {text[:100]}")

                # Price mentions
                if re.search(r"\$\d+|\b(?:price|cost|rate|premium|month)\b", text, re.IGNORECASE):
                    key_points.append(f"Pricing mentioned: {text[:100]}")

                # Next steps
                if re.search(r"\b(?:follow up|call back|schedule|appointment|next step)\b", text, re.IGNORECASE):
                    key_points.append(f"Next step: {text[:100]}")

        return key_points[:10]  # Limit to 10 key points

    def _extract_next_steps(self, segments: List[Dict]) -> List[str]:
        """Extract next steps from the call."""
        next_steps = []

        for seg in segments:
            text = seg.get("text", "")

            if re.search(r"\b(?:will|going to|let me|I'll)\b.*\b(?:follow up|call|send|email|schedule)\b", text, re.IGNORECASE):
                next_steps.append(text[:150])

        return next_steps[:5]

    def _calculate_close_probability(
        self,
        objections: Dict,
        sentiment: Dict,
        engagement: Dict,
        compliance: Dict,
    ) -> float:
        """Calculate probability to close based on analysis."""
        score = 0.5  # Base

        # Sentiment factor
        score += (sentiment["score"] - 0.5) * 0.3

        # Engagement factor
        score += (engagement["score"] - 0.5) * 0.2

        # Objection handling factor
        if objections["count"] > 0:
            handle_rate = objections["handled"] / objections["count"]
            score += handle_rate * 0.2

        # Compliance factor
        score += (compliance["score"] - 0.5) * 0.1

        return round(max(0, min(1, score)), 3)

    def get_analysis(self, analysis_id: UUID) -> Optional[CallAnalysis]:
        """Get analysis by ID."""
        return self.db.query(CallAnalysis).filter(
            CallAnalysis.id == analysis_id,
        ).first()

    def get_analysis_for_appointment(self, appointment_id: UUID) -> Optional[CallAnalysis]:
        """Get analysis for an appointment."""
        return self.db.query(CallAnalysis).filter(
            CallAnalysis.appointment_id == appointment_id,
        ).first()
