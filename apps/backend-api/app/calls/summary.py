"""
AI Call Summary Generator (Phase 41.5)

Generates structured call summaries:
- Key discussion points
- Objections raised and handled
- Customer sentiment
- Probability to close
- Recommended next steps

Uses transcript text and analysis data to create
agent-friendly summaries.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.calls.models import CallTranscript, CallAnalysis, CallRecording

logger = logging.getLogger(__name__)


class CallSummary:
    """Represents a generated call summary."""

    def __init__(
        self,
        appointment_id: str,
        lead_name: str,
        duration_minutes: float,
        overall_sentiment: str,
        key_points: List[str],
        objections: List[Dict],
        next_steps: List[str],
        probability_to_close: float,
        agent_performance: Dict,
        compliance_notes: List[str],
    ):
        self.appointment_id = appointment_id
        self.lead_name = lead_name
        self.duration_minutes = duration_minutes
        self.overall_sentiment = overall_sentiment
        self.key_points = key_points
        self.objections = objections
        self.next_steps = next_steps
        self.probability_to_close = probability_to_close
        self.agent_performance = agent_performance
        self.compliance_notes = compliance_notes
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "appointment_id": self.appointment_id,
            "lead_name": self.lead_name,
            "duration_minutes": self.duration_minutes,
            "overall_sentiment": self.overall_sentiment,
            "key_points": self.key_points,
            "objections": self.objections,
            "next_steps": self.next_steps,
            "probability_to_close": self.probability_to_close,
            "agent_performance": self.agent_performance,
            "compliance_notes": self.compliance_notes,
            "generated_at": self.generated_at,
        }

    def to_text(self) -> str:
        """Generate human-readable summary text."""
        parts = [
            f"CALL SUMMARY — {self.lead_name}",
            f"Duration: {self.duration_minutes:.1f} minutes",
            f"Sentiment: {self.overall_sentiment}",
            f"Close Probability: {self.probability_to_close:.0%}",
            "",
        ]

        if self.key_points:
            parts.append("KEY POINTS:")
            for point in self.key_points:
                parts.append(f"  • {point}")
            parts.append("")

        if self.objections:
            parts.append("OBJECTIONS:")
            for obj in self.objections:
                status = "✓ Handled" if obj.get("handled") else "✗ Unhandled"
                parts.append(f"  • {obj.get('type', 'unknown')}: {obj.get('text', '')[:100]} [{status}]")
            parts.append("")

        if self.next_steps:
            parts.append("NEXT STEPS:")
            for step in self.next_steps:
                parts.append(f"  • {step}")
            parts.append("")

        if self.compliance_notes:
            parts.append("COMPLIANCE NOTES:")
            for note in self.compliance_notes:
                parts.append(f"  ⚠ {note}")

        return "\n".join(parts)


class CallSummaryGenerator:
    """
    Generates AI-powered call summaries.

    Combines transcript text and analysis data to create
    comprehensive, agent-friendly summaries.
    """

    def __init__(self, db: Session = None):
        self.db = db

    def generate_summary(self, appointment_id: UUID) -> Optional[CallSummary]:
        """
        Generate a summary for a call appointment.

        Args:
            appointment_id: Appointment UUID

        Returns:
            CallSummary object
        """
        # Get analysis
        analysis = self.db.query(CallAnalysis).filter(
            CallAnalysis.appointment_id == appointment_id,
        ).first()

        if not analysis:
            logger.warning(f"No analysis found for appointment {appointment_id}")
            return None

        # Get transcript
        transcript = self.db.query(CallTranscript).filter(
            CallTranscript.id == analysis.transcript_id,
        ).first()

        # Get recording
        recording = self.db.query(CallRecording).filter(
            CallRecording.id == analysis.recording_id,
        ).first()

        # Get lead name
        from app.models.lead import Lead
        lead = self.db.query(Lead).filter(Lead.id == analysis.lead_id).first()
        lead_name = f"{lead.first_name} {lead.last_name}" if lead else "Unknown"

        # Calculate duration
        duration_minutes = (recording.duration_seconds / 60) if recording and recording.duration_seconds else 0

        # Generate agent performance assessment
        agent_performance = self._assess_agent_performance(analysis, transcript)

        # Generate compliance notes
        compliance_notes = self._generate_compliance_notes(analysis)

        return CallSummary(
            appointment_id=str(appointment_id),
            lead_name=lead_name,
            duration_minutes=duration_minutes,
            overall_sentiment=analysis.overall_sentiment,
            key_points=analysis.key_points or [],
            objections=analysis.objections_detected or [],
            next_steps=analysis.next_steps or [],
            probability_to_close=analysis.probability_to_close,
            agent_performance=agent_performance,
            compliance_notes=compliance_notes,
        )

    def generate_quick_summary(self, appointment_id: UUID) -> Optional[str]:
        """Generate a quick one-paragraph summary."""
        summary = self.generate_summary(appointment_id)
        if not summary:
            return None

        parts = [
            f"Call with {summary.lead_name} ({summary.duration_minutes:.0f} min).",
            f"Sentiment: {summary.overall_sentiment}.",
        ]

        if summary.objections:
            handled = sum(1 for o in summary.objections if o.get("handled"))
            parts.append(f"{len(summary.objections)} objections raised, {handled} handled.")

        parts.append(f"Close probability: {summary.probability_to_close:.0%}.")

        if summary.next_steps:
            parts.append(f"Next: {summary.next_steps[0][:100]}")

        return " ".join(parts)

    def _assess_agent_performance(
        self,
        analysis: CallAnalysis,
        transcript: Optional[CallTranscript],
    ) -> Dict[str, Any]:
        """Assess agent performance from analysis."""
        assessment = {
            "overall_score": 0.0,
            "strengths": [],
            "areas_for_improvement": [],
        }

        scores = []

        # Talk ratio (ideal: 40-60% agent)
        if transcript:
            talk_ratio = transcript.talk_ratio
            if 0.4 <= talk_ratio <= 0.6:
                assessment["strengths"].append("Good talk/listen ratio")
                scores.append(0.9)
            elif talk_ratio > 0.7:
                assessment["areas_for_improvement"].append("Talking too much — listen more")
                scores.append(0.5)
            else:
                assessment["areas_for_improvement"].append("Not talking enough — lead the conversation")
                scores.append(0.6)

        # Objection handling
        if analysis.objection_count > 0:
            handle_rate = analysis.objections_handled / analysis.objection_count
            if handle_rate > 0.8:
                assessment["strengths"].append("Excellent objection handling")
                scores.append(0.9)
            elif handle_rate > 0.5:
                scores.append(0.7)
            else:
                assessment["areas_for_improvement"].append("Improve objection handling skills")
                scores.append(0.4)

        # Engagement
        if analysis.engagement_score > 0.7:
            assessment["strengths"].append("High customer engagement")
            scores.append(0.9)
        elif analysis.engagement_score < 0.4:
            assessment["areas_for_improvement"].append("Increase customer engagement")
            scores.append(0.5)

        # Compliance
        if analysis.compliance_score > 0.9:
            assessment["strengths"].append("Compliant communication")
            scores.append(0.9)
        elif analysis.compliance_score < 0.7:
            assessment["areas_for_improvement"].append("Review compliance guidelines")
            scores.append(0.4)

        # Calculate overall
        assessment["overall_score"] = round(sum(scores) / len(scores), 3) if scores else 0.5

        return assessment

    def _generate_compliance_notes(self, analysis: CallAnalysis) -> List[str]:
        """Generate compliance notes from analysis."""
        notes = []

        violations = analysis.compliance_violations or []
        for violation in violations:
            vtype = violation.get("type", "unknown")
            text = violation.get("text", "")[:100]

            if vtype == "medical_advice":
                notes.append(f"Medical advice detected: '{text}' — Do not provide medical guidance")
            elif vtype == "legal_advice":
                notes.append(f"Legal advice detected: '{text}' — Do not provide legal guidance")
            elif vtype == "guaranteed_outcomes":
                notes.append(f"Guarantee detected: '{text}' — Avoid guaranteeing outcomes")
            elif vtype == "false_urgency":
                notes.append(f"False urgency: '{text}' — Use genuine urgency only")
            elif vtype == "competitor_bashing":
                notes.append(f"Competitor bashing: '{text}' — Focus on your value, not competitor weakness")

        if analysis.compliance_score < 0.8:
            notes.append(f"Overall compliance score: {analysis.compliance_score:.0%} — Review call for issues")

        return notes
