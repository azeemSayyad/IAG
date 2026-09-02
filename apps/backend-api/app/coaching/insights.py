"""
Coaching Insights Generator (Phase 42.2)

Generates actionable coaching insights:
- Performance gap analysis
- Specific improvement areas
- Benchmarking against team
- Personalized coaching tips
- Progress tracking

Insights are generated from performance data and
formatted for agent consumption.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.coaching.performance import PerformanceAnalyzer, AgentPerformanceReport

logger = logging.getLogger(__name__)


class CoachingInsight:
    """A single coaching insight."""

    def __init__(
        self,
        category: str,
        priority: str,
        title: str,
        description: str,
        evidence: str,
        action_items: List[str],
        impact: str,
    ):
        self.category = category  # performance, skill, behavior, strategy
        self.priority = priority  # high, medium, low
        self.title = title
        self.description = description
        self.evidence = evidence
        self.action_items = action_items
        self.impact = impact
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "category": self.category,
            "priority": self.priority,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "action_items": self.action_items,
            "impact": self.impact,
            "generated_at": self.generated_at,
        }


class CoachingInsightsGenerator:
    """
    Generates personalized coaching insights for agents.

    Features:
    - Performance gap identification
    - Skill-based coaching
    - Behavioral recommendations
    - Strategic suggestions
    - Team benchmarking
    """

    def __init__(self, db: Session):
        self.db = db
        self.analyzer = PerformanceAnalyzer(db)

    def generate_insights(
        self,
        agent_id: UUID,
        period_days: int = 30,
    ) -> List[CoachingInsight]:
        """
        Generate coaching insights for an agent.

        Args:
            agent_id: Agent UUID
            period_days: Analysis period

        Returns:
            List of CoachingInsight objects
        """
        report = self.analyzer.analyze_agent(agent_id, period_days)
        insights = []

        # Performance insights
        insights.extend(self._performance_insights(report))

        # Skill insights
        insights.extend(self._skill_insights(report))

        # Behavioral insights
        insights.extend(self._behavioral_insights(report))

        # Strategic insights
        insights.extend(self._strategic_insights(report))

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        insights.sort(key=lambda i: priority_order.get(i.priority, 3))

        return insights

    def _performance_insights(self, report: AgentPerformanceReport) -> List[CoachingInsight]:
        """Generate performance-based insights."""
        insights = []
        m = report.metrics

        # Win rate insight
        win_rate = m.get("win_rate", 0)
        if win_rate < 0.2:
            insights.append(CoachingInsight(
                category="performance",
                priority="high",
                title="Low Conversion Rate",
                description=f"Your win rate is {win_rate:.0%}, which is below the team target of 20%.",
                evidence=f"Won {m.get('won_deals', 0)} out of {m.get('completed_calls', 0)} completed calls.",
                action_items=[
                    "Review your qualification criteria — are you talking to the right leads?",
                    "Practice trial closes throughout the call, not just at the end",
                    "Ask for the sale directly: 'Would you like to move forward with this coverage?'",
                ],
                impact="Improving win rate by 10% could result in 2-3 more deals per month.",
            ))

        # No-show rate insight
        no_show = m.get("no_show_rate", 0)
        if no_show > 0.15:
            insights.append(CoachingInsight(
                category="performance",
                priority="high",
                title="High No-Show Rate",
                description=f"Your no-show rate is {no_show:.0%}, meaning {no_show:.0%} of booked leads don't show up.",
                evidence=f"{m.get('total_appointments', 0)} appointments booked, significant no-shows.",
                action_items=[
                    "Send a confirmation message 1 hour before the call",
                    "Build excitement: 'I have some great options to share with you!'",
                    "Offer easy rescheduling: 'If something comes up, just let me know'",
                ],
                impact="Reducing no-shows by 10% is equivalent to booking 2 more calls per week.",
            ))

        return insights

    def _skill_insights(self, report: AgentPerformanceReport) -> List[CoachingInsight]:
        """Generate skill-based insights."""
        insights = []
        m = report.metrics

        # Objection handling
        handle_rate = m.get("objection_handle_rate", 0)
        common = m.get("common_objections", [])

        if handle_rate < 0.5 and common:
            top_obj = common[0].get("type", "unknown") if common else "unknown"
            insights.append(CoachingInsight(
                category="skill",
                priority="high",
                title="Objection Handling Needs Improvement",
                description=f"You handle {handle_rate:.0%} of objections effectively. Your most common objection is '{top_obj}'.",
                evidence=f"{m.get('objection_count', 0)} objections raised, {m.get('objections_handled', 0)} handled.",
                action_items=[
                    f"Practice responses for '{top_obj}' objections",
                    "Use the feel-felt-found method: 'I understand how you feel. Many of our customers felt the same way. Here's what they found...'",
                    "Acknowledge the concern before responding",
                ],
                impact="Better objection handling can improve win rate by 15-20%.",
            ))

        # Talk ratio
        talk_ratio = m.get("avg_talk_ratio", 0.5)
        if talk_ratio > 0.65:
            insights.append(CoachingInsight(
                category="skill",
                priority="medium",
                title="Talking Too Much",
                description=f"Your talk ratio is {talk_ratio:.0%} — you're doing most of the talking.",
                evidence=f"Average {m.get('avg_agent_words', 0)} words per call vs customer's {m.get('avg_customer_words', 0)}.",
                action_items=[
                    "Ask open-ended questions: 'What's most important to you in coverage?'",
                    "Pause for 3 seconds after the customer finishes speaking",
                    "Summarize what you heard before responding",
                ],
                impact="Customers who feel heard are 2x more likely to buy.",
            ))

        return insights

    def _behavioral_insights(self, report: AgentPerformanceReport) -> List[CoachingInsight]:
        """Generate behavioral insights."""
        insights = []
        m = report.metrics

        # Call duration
        avg_duration = m.get("avg_call_duration_minutes", 0)
        if avg_duration < 5:
            insights.append(CoachingInsight(
                category="behavior",
                priority="medium",
                title="Calls Are Too Short",
                description=f"Average call duration is {avg_duration:.1f} minutes. Effective calls typically last 10-15 minutes.",
                evidence="Short calls often mean insufficient discovery and rapport building.",
                action_items=[
                    "Spend 2-3 minutes on rapport before discussing insurance",
                    "Ask at least 3 discovery questions about their needs",
                    "Explain coverage options in detail — don't rush",
                ],
                impact="Longer, more thorough calls have 30% higher conversion rates.",
            ))

        # Response time
        response_time = m.get("avg_first_response_seconds", 0)
        if response_time > 300:
            insights.append(CoachingInsight(
                category="behavior",
                priority="medium",
                title="Slow Response Time",
                description=f"Average response time is {response_time/60:.0f} minutes. Fast responders win more deals.",
                evidence="Leads expect quick responses — within 5 minutes is ideal.",
                action_items=[
                    "Set up notifications for new lead messages",
                    "Use quick reply templates for initial responses",
                    "Respond within 2 minutes during business hours",
                ],
                impact="Responding within 5 minutes increases conversion by 40%.",
            ))

        return insights

    def _strategic_insights(self, report: AgentPerformanceReport) -> List[CoachingInsight]:
        """Generate strategic insights."""
        insights = []
        m = report.metrics

        # Disposition analysis
        dispositions = m.get("dispositions", {})
        total = sum(dispositions.values())

        if total > 0:
            follow_up_rate = dispositions.get("follow_up", 0) / total
            if follow_up_rate > 0.4:
                insights.append(CoachingInsight(
                    category="strategy",
                    priority="medium",
                    title="High Follow-Up Rate",
                    description=f"{follow_up_rate:.0%} of your calls result in follow-up rather than close.",
                    evidence="Many leads need multiple touches — but some should close on first call.",
                    action_items=[
                        "Identify why leads aren't closing on first call",
                        "Try a direct close: 'Based on what we discussed, shall we get you started?'",
                        "Offer a compelling reason to act now: special pricing, limited availability",
                    ],
                    impact="Converting 20% of follow-ups to immediate closes doubles your output.",
                ))

        return insights

    def get_coaching_summary(
        self,
        agent_id: UUID,
        period_days: int = 30,
    ) -> Dict[str, Any]:
        """Get a summary of coaching insights."""
        insights = self.generate_insights(agent_id, period_days)

        high_priority = [i for i in insights if i.priority == "high"]
        medium_priority = [i for i in insights if i.priority == "medium"]

        return {
            "agent_id": str(agent_id),
            "total_insights": len(insights),
            "high_priority": len(high_priority),
            "medium_priority": len(medium_priority),
            "top_focus_areas": [i.title for i in high_priority[:3]],
            "quick_wins": [i.action_items[0] for i in insights if i.action_items][:3],
            "insights": [i.to_dict() for i in insights],
        }
