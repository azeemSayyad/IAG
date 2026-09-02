"""
Agent Ranking System (Phase 42.4)

Ranks agents across multiple dimensions:
- Top closers (highest win rate)
- Fastest closers (shortest time to close)
- Best show rates (lowest no-show rate)
- Best objection handlers
- Highest customer engagement
- Overall leaderboard

Rankings are computed from production data and
updated periodically.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.user import User
from app.coaching.performance import PerformanceAnalyzer

logger = logging.getLogger(__name__)


class AgentRanking:
    """Represents an agent's ranking position."""

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        rank: int,
        score: float,
        metric_value: Any,
        tier: str,
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.rank = rank
        self.score = score
        self.metric_value = metric_value
        self.tier = tier  # gold, silver, bronze, standard
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "rank": self.rank,
            "score": round(self.score, 3),
            "metric_value": self.metric_value,
            "tier": self.tier,
            "generated_at": self.generated_at,
        }


class AgentRankingSystem:
    """
    Ranks agents across multiple performance dimensions.

    Features:
    - Multi-dimensional rankings
    - Overall leaderboard
    - Tier assignment
    - Historical tracking
    """

    def __init__(self, db: Session):
        self.db = db
        self.analyzer = PerformanceAnalyzer(db)

    def get_overall_leaderboard(
        self,
        tenant_id: str,
        period_days: int = 30,
        limit: int = 20,
    ) -> List[AgentRanking]:
        """
        Generate overall agent leaderboard.

        Combines multiple metrics into a single score.

        Args:
            tenant_id: Tenant ID
            period_days: Analysis period
            limit: Max agents to return

        Returns:
            List of AgentRanking objects
        """
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        if not agents:
            return []

        # Calculate scores for each agent
        scored_agents = []
        for agent in agents:
            report = self.analyzer.analyze_agent(agent.id, period_days)
            score = self._calculate_overall_score(report.metrics)

            user = self.db.query(User).filter(User.id == agent.user_id).first()
            name = f"{user.first_name} {user.last_name}" if user else "Unknown"

            scored_agents.append({
                "agent_id": str(agent.id),
                "agent_name": name,
                "score": score,
                "metrics": report.metrics,
            })

        # Sort by score
        scored_agents.sort(key=lambda x: x["score"], reverse=True)

        # Create rankings
        rankings = []
        for i, agent in enumerate(scored_agents[:limit]):
            tier = self._assign_tier(i, len(scored_agents))
            rankings.append(AgentRanking(
                agent_id=agent["agent_id"],
                agent_name=agent["agent_name"],
                rank=i + 1,
                score=agent["score"],
                metric_value=agent["metrics"].get("win_rate", 0),
                tier=tier,
            ))

        return rankings

    def get_ranking_by_metric(
        self,
        tenant_id: str,
        metric: str,
        period_days: int = 30,
        limit: int = 10,
    ) -> List[AgentRanking]:
        """
        Rank agents by a specific metric.

        Args:
            tenant_id: Tenant ID
            metric: Metric to rank by
            period_days: Analysis period
            limit: Max agents

        Returns:
            List of AgentRanking objects
        """
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        scored_agents = []
        for agent in agents:
            report = self.analyzer.analyze_agent(agent.id, period_days)
            value = report.metrics.get(metric, 0)

            user = self.db.query(User).filter(User.id == agent.user_id).first()
            name = f"{user.first_name} {user.last_name}" if user else "Unknown"

            scored_agents.append({
                "agent_id": str(agent.id),
                "agent_name": name,
                "value": value,
            })

        # Sort by metric value (descending)
        scored_agents.sort(key=lambda x: x["value"], reverse=True)

        rankings = []
        for i, agent in enumerate(scored_agents[:limit]):
            tier = self._assign_tier(i, len(scored_agents))
            rankings.append(AgentRanking(
                agent_id=agent["agent_id"],
                agent_name=agent["agent_name"],
                rank=i + 1,
                score=agent["value"],
                metric_value=agent["value"],
                tier=tier,
            ))

        return rankings

    def get_top_closers(self, tenant_id: str, period_days: int = 30) -> List[AgentRanking]:
        """Get top agents by win rate."""
        return self.get_ranking_by_metric(tenant_id, "win_rate", period_days)

    def get_fastest_closers(self, tenant_id: str, period_days: int = 30) -> List[AgentRanking]:
        """Get agents with shortest average call duration."""
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        scored_agents = []
        for agent in agents:
            report = self.analyzer.analyze_agent(agent.id, period_days)
            duration = report.metrics.get("avg_call_duration_minutes", 0)

            if duration > 0:
                user = self.db.query(User).filter(User.id == agent.user_id).first()
                name = f"{user.first_name} {user.last_name}" if user else "Unknown"
                scored_agents.append({
                    "agent_id": str(agent.id),
                    "agent_name": name,
                    "value": duration,
                })

        # Sort by duration (ascending - faster is better)
        scored_agents.sort(key=lambda x: x["value"])

        rankings = []
        for i, agent in enumerate(scored_agents[:10]):
            tier = self._assign_tier(i, len(scored_agents))
            rankings.append(AgentRanking(
                agent_id=agent["agent_id"],
                agent_name=agent["agent_name"],
                rank=i + 1,
                score=1.0 / (agent["value"] + 1),  # Inverse score
                metric_value=f"{agent['value']:.1f} min",
                tier=tier,
            ))

        return rankings

    def get_best_show_rates(self, tenant_id: str, period_days: int = 30) -> List[AgentRanking]:
        """Get agents with lowest no-show rates."""
        return self.get_ranking_by_metric(tenant_id, "no_show_rate", period_days, limit=10)

    def get_best_objection_handlers(self, tenant_id: str, period_days: int = 30) -> List[AgentRanking]:
        """Get agents with highest objection handling rates."""
        return self.get_ranking_by_metric(tenant_id, "objection_handle_rate", period_days)

    def get_agent_badges(self, agent_id: UUID, period_days: int = 30) -> List[Dict]:
        """
        Get achievement badges for an agent.

        Badges:
        - Top Closer: Win rate > 30%
        - Speed Demon: Avg call < 10 min with > 20% win rate
        - Objection Master: Handle rate > 80%
        - Customer Favorite: Engagement score > 0.8
        - Compliance Champion: No violations
        - Consistency King: All metrics above average
        """
        report = self.analyzer.analyze_agent(agent_id, period_days)
        m = report.metrics
        badges = []

        if m.get("win_rate", 0) > 0.3:
            badges.append({
                "badge": "Top Closer",
                "icon": "🏆",
                "description": f"Win rate of {m['win_rate']:.0%}",
            })

        if m.get("avg_call_duration_minutes", 999) < 10 and m.get("win_rate", 0) > 0.2:
            badges.append({
                "badge": "Speed Demon",
                "icon": "⚡",
                "description": "Quick, effective calls",
            })

        if m.get("objection_handle_rate", 0) > 0.8:
            badges.append({
                "badge": "Objection Master",
                "icon": "🛡️",
                "description": f"Handles {m['objection_handle_rate']:.0%} of objections",
            })

        if m.get("engagement_score", 0) > 0.8:
            badges.append({
                "badge": "Customer Favorite",
                "icon": "⭐",
                "description": "High customer engagement",
            })

        if m.get("compliance_score", 0) > 0.95:
            badges.append({
                "badge": "Compliance Champion",
                "icon": "✅",
                "description": "Excellent compliance record",
            })

        return badges

    def _calculate_overall_score(self, metrics: Dict) -> float:
        """Calculate overall performance score."""
        weights = {
            "win_rate": 0.30,
            "objection_handle_rate": 0.20,
            "engagement_score": 0.15,
            "completion_rate": 0.15,
            "no_show_rate": 0.10,  # Inverse (lower is better)
            "compliance_score": 0.10,
        }

        score = 0
        for metric, weight in weights.items():
            value = metrics.get(metric, 0)
            if metric == "no_show_rate":
                value = 1 - value  # Inverse
            score += value * weight

        return round(score, 3)

    def _assign_tier(self, rank: int, total: int) -> str:
        """Assign tier based on rank."""
        if total == 0:
            return "standard"

        percentile = rank / total

        if percentile <= 0.1:
            return "gold"
        elif percentile <= 0.3:
            return "silver"
        elif percentile <= 0.5:
            return "bronze"
        else:
            return "standard"
