"""
Agent Performance Analysis (Phase 42.1)

Analyzes agent performance across multiple dimensions:
- Conversion rate (leads → bookings → wins)
- Objection handling effectiveness
- Talk ratio (agent vs customer)
- Call outcomes and dispositions
- Response time and follow-up speed
- Customer satisfaction signals

Metrics are computed from production data and stored
for coaching and ranking.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.calls.models import CallAnalysis, CallTranscript

logger = logging.getLogger(__name__)


class AgentPerformanceReport:
    """Complete performance report for an agent."""

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        period_days: int,
        metrics: Dict[str, Any],
        strengths: List[str],
        weaknesses: List[str],
        recommendations: List[str],
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.period_days = period_days
        self.metrics = metrics
        self.strengths = strengths
        self.weaknesses = weaknesses
        self.recommendations = recommendations
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "period_days": self.period_days,
            "metrics": self.metrics,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


class PerformanceAnalyzer:
    """
    Analyzes agent performance from production data.

    Features:
    - Multi-dimensional performance metrics
    - Trend analysis
    - Comparative analysis
    - Strength/weakness identification
    """

    def __init__(self, db: Session):
        self.db = db

    def analyze_agent(
        self,
        agent_id: UUID,
        period_days: int = 30,
    ) -> AgentPerformanceReport:
        """
        Generate comprehensive performance report for an agent.

        Args:
            agent_id: Agent UUID
            period_days: Analysis period in days

        Returns:
            AgentPerformanceReport
        """
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            return self._empty_report(str(agent_id))

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        # Get appointments in period
        appointments = self.db.query(Appointment).filter(
            Appointment.agent_id == agent_id,
            Appointment.created_at >= period_start,
        ).all()

        # Calculate metrics
        conversion = self._calculate_conversion_metrics(appointments)
        objection = self._calculate_objection_metrics(agent_id, period_start)
        talk = self._calculate_talk_metrics(agent_id, period_start)
        outcomes = self._calculate_outcome_metrics(appointments)
        responsiveness = self._calculate_responsiveness(agent_id, period_start)

        # Combine metrics
        metrics = {
            **conversion,
            **objection,
            **talk,
            **outcomes,
            **responsiveness,
        }

        # Identify strengths and weaknesses
        strengths = self._identify_strengths(metrics)
        weaknesses = self._identify_weaknesses(metrics)
        recommendations = self._generate_recommendations(metrics, weaknesses)

        from app.models.user import User
        user = self.db.query(User).filter(User.id == agent.user_id).first()
        agent_name = f"{user.first_name} {user.last_name}" if user else "Unknown"

        return AgentPerformanceReport(
            agent_id=str(agent_id),
            agent_name=agent_name,
            period_days=period_days,
            metrics=metrics,
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=recommendations,
        )

    def _calculate_conversion_metrics(self, appointments: List) -> Dict[str, float]:
        """Calculate conversion-related metrics."""
        total = len(appointments)
        completed = sum(1 for a in appointments if a.status == "completed")
        won = sum(1 for a in appointments if a.disposition == "won")
        lost = sum(1 for a in appointments if a.disposition == "lost")

        return {
            "total_appointments": total,
            "completed_calls": completed,
            "won_deals": won,
            "lost_deals": lost,
            "completion_rate": round(completed / total, 3) if total > 0 else 0,
            "win_rate": round(won / completed, 3) if completed > 0 else 0,
            "loss_rate": round(lost / completed, 3) if completed > 0 else 0,
        }

    def _calculate_objection_metrics(self, agent_id: UUID, since: datetime) -> Dict[str, Any]:
        """Calculate objection handling metrics."""
        analyses = self.db.query(CallAnalysis).filter(
            CallAnalysis.agent_id == agent_id,
            CallAnalysis.created_at >= since,
        ).all()

        if not analyses:
            return {
                "objection_count": 0,
                "objections_handled": 0,
                "objection_handle_rate": 0,
                "common_objections": [],
            }

        total_objections = sum(a.objection_count or 0 for a in analyses)
        total_handled = sum(a.objections_handled or 0 for a in analyses)

        # Find common objection types
        objection_types = {}
        for a in analyses:
            for obj in (a.objections_detected or []):
                otype = obj.get("type", "unknown")
                objection_types[otype] = objection_types.get(otype, 0) + 1

        common = sorted(objection_types.items(), key=lambda x: x[1], reverse=True)[:3]

        return {
            "objection_count": total_objections,
            "objections_handled": total_handled,
            "objection_handle_rate": round(total_handled / total_objections, 3) if total_objections > 0 else 0,
            "common_objections": [{"type": t, "count": c} for t, c in common],
        }

    def _calculate_talk_metrics(self, agent_id: UUID, since: datetime) -> Dict[str, float]:
        """Calculate talk ratio metrics."""
        transcripts = self.db.query(CallTranscript).filter(
            CallTranscript.agent_id == agent_id,
            CallTranscript.created_at >= since,
        ).all()

        if not transcripts:
            return {
                "avg_talk_ratio": 0.5,
                "avg_agent_words": 0,
                "avg_customer_words": 0,
                "total_calls_analyzed": 0,
            }

        talk_ratios = [t.talk_ratio for t in transcripts if t.talk_ratio]
        agent_words = [t.agent_words or 0 for t in transcripts]
        customer_words = [t.customer_words or 0 for t in transcripts]

        return {
            "avg_talk_ratio": round(sum(talk_ratios) / len(talk_ratios), 3) if talk_ratios else 0.5,
            "avg_agent_words": round(sum(agent_words) / len(agent_words)) if agent_words else 0,
            "avg_customer_words": round(sum(customer_words) / len(customer_words)) if customer_words else 0,
            "total_calls_analyzed": len(transcripts),
        }

    def _calculate_outcome_metrics(self, appointments: List) -> Dict[str, Any]:
        """Calculate call outcome metrics."""
        dispositions = {}
        for a in appointments:
            if a.disposition:
                dispositions[a.disposition] = dispositions.get(a.disposition, 0) + 1

        # Calculate average call duration
        durations = [a.call_duration_seconds for a in appointments if a.call_duration_seconds]
        avg_duration = sum(durations) / len(durations) if durations else 0

        # No-show rate
        total = len(appointments)
        no_shows = sum(1 for a in appointments if a.status in ("no_show", "missed"))

        return {
            "dispositions": dispositions,
            "avg_call_duration_seconds": round(avg_duration, 1),
            "avg_call_duration_minutes": round(avg_duration / 60, 1),
            "no_show_rate": round(no_shows / total, 3) if total > 0 else 0,
        }

    def _calculate_responsiveness(self, agent_id: UUID, since: datetime) -> Dict[str, float]:
        """Calculate agent responsiveness metrics."""
        # Get agent's conversations
        conversations = self.db.query(Conversation).join(Appointment).filter(
            Appointment.agent_id == agent_id,
            Conversation.created_at >= since,
        ).all()

        if not conversations:
            return {
                "avg_first_response_seconds": 0,
                "avg_response_time_seconds": 0,
                "response_rate": 0,
            }

        response_times = []
        for conv in conversations:
            messages = self.db.query(Message).filter(
                Message.conversation_id == conv.id,
            ).order_by(Message.created_at).all()

            last_customer = None
            for msg in messages:
                if msg.sender == "customer" and msg.created_at:
                    last_customer = msg.created_at
                elif msg.sender == "ai" and last_customer and msg.created_at:
                    delta = (msg.created_at - last_customer).total_seconds()
                    if 0 < delta < 3600:
                        response_times.append(delta)
                    last_customer = None

        return {
            "avg_first_response_seconds": round(sum(response_times) / len(response_times), 1) if response_times else 0,
            "avg_response_time_seconds": round(sum(response_times) / len(response_times), 1) if response_times else 0,
            "response_rate": round(len(response_times) / len(conversations), 3) if conversations else 0,
        }

    def _identify_strengths(self, metrics: Dict) -> List[str]:
        """Identify agent strengths from metrics."""
        strengths = []

        if metrics.get("win_rate", 0) > 0.3:
            strengths.append(f"High win rate ({metrics['win_rate']:.0%})")

        if metrics.get("objection_handle_rate", 0) > 0.7:
            strengths.append(f"Excellent objection handling ({metrics['objection_handle_rate']:.0%})")

        talk_ratio = metrics.get("avg_talk_ratio", 0.5)
        if 0.4 <= talk_ratio <= 0.6:
            strengths.append("Balanced talk/listen ratio")

        if metrics.get("no_show_rate", 1) < 0.1:
            strengths.append("Low no-show rate")

        if metrics.get("avg_call_duration_minutes", 0) > 10:
            strengths.append("Engaging conversations (long call duration)")

        return strengths

    def _identify_weaknesses(self, metrics: Dict) -> List[str]:
        """Identify agent weaknesses from metrics."""
        weaknesses = []

        if metrics.get("win_rate", 1) < 0.15:
            weaknesses.append(f"Low win rate ({metrics['win_rate']:.0%})")

        if metrics.get("objection_handle_rate", 1) < 0.4:
            weaknesses.append(f"Poor objection handling ({metrics['objection_handle_rate']:.0%})")

        talk_ratio = metrics.get("avg_talk_ratio", 0.5)
        if talk_ratio > 0.7:
            weaknesses.append("Talking too much — needs to listen more")
        elif talk_ratio < 0.3:
            weaknesses.append("Not talking enough — needs to lead conversation")

        if metrics.get("no_show_rate", 0) > 0.2:
            weaknesses.append(f"High no-show rate ({metrics['no_show_rate']:.0%})")

        if metrics.get("avg_call_duration_minutes", 0) < 3:
            weaknesses.append("Calls too short — not engaging enough")

        return weaknesses

    def _generate_recommendations(self, metrics: Dict, weaknesses: List[str]) -> List[str]:
        """Generate coaching recommendations."""
        recommendations = []

        for weakness in weaknesses:
            if "win rate" in weakness.lower():
                recommendations.append("Focus on qualifying leads better before calls")
                recommendations.append("Practice closing techniques and trial closes")

            if "objection" in weakness.lower():
                recommendations.append("Review common objection responses")
                recommendations.append("Practice the feel-felt-found method")

            if "talking too much" in weakness.lower():
                recommendations.append("Ask more open-ended questions")
                recommendations.append("Practice active listening — pause after customer speaks")

            if "not talking enough" in weakness.lower():
                recommendations.append("Take more initiative in guiding the conversation")
                recommendations.append("Prepare key talking points before each call")

            if "no-show" in weakness.lower():
                recommendations.append("Send confirmation messages before appointments")
                recommendations.append("Build excitement about the call value")

            if "calls too short" in weakness.lower():
                recommendations.append("Take time to build rapport before discussing coverage")
                recommendations.append("Ask discovery questions to understand customer needs")

        return recommendations[:5]  # Top 5 recommendations

    def _empty_report(self, agent_id: str) -> AgentPerformanceReport:
        """Return empty report for agents with no data."""
        return AgentPerformanceReport(
            agent_id=agent_id,
            agent_name="Unknown",
            period_days=0,
            metrics={},
            strengths=[],
            weaknesses=["No data available"],
            recommendations=["Complete more calls to generate performance insights"],
        )

    def get_team_summary(self, tenant_id: str, period_days: int = 30) -> Dict[str, Any]:
        """Get team-wide performance summary."""
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        reports = []
        for agent in agents:
            report = self.analyze_agent(agent.id, period_days)
            reports.append(report)

        if not reports:
            return {"agents": 0, "summary": "No active agents"}

        # Team averages
        win_rates = [r.metrics.get("win_rate", 0) for r in reports]
        objection_rates = [r.metrics.get("objection_handle_rate", 0) for r in reports]

        return {
            "agents": len(reports),
            "period_days": period_days,
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 3) if win_rates else 0,
            "avg_objection_rate": round(sum(objection_rates) / len(objection_rates), 3) if objection_rates else 0,
            "top_performer": max(reports, key=lambda r: r.metrics.get("win_rate", 0)).agent_id if reports else None,
            "needs_coaching": [r.agent_id for r in reports if r.weaknesses],
        }
