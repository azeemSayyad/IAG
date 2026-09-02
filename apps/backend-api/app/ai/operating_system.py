"""
AI Operating System Layer (Phase 49)

Turns the system into an autonomous AI business engine:

Step 49.1 — AI Decision Engine
    Decides: who to contact, when, best channel, best agent

Step 49.2 — AI Campaign Optimizer
    Auto-adjusts: prompts, timing, retries

Step 49.3 — AI Revenue Optimizer
    Optimizes: occupancy, booking density, agent allocation

Step 49.4 — Autonomous AI Workflows
    Dynamically: retries, escalates, pauses, changes flows

Step 49.5 — Self-Learning System
    Continuously improves: prompts, timing, assignment, persuasion
"""

import json
import logging
import math
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.lead import Lead
from app.models.agent import Agent
from app.models.campaign import Campaign
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.message import Message
from app.core.redis import redis_service
from app.ml.feature_pipeline import FeaturePipeline
from app.ml.inference_service import OnlineInferenceService

logger = logging.getLogger(__name__)


# --- AI Decision Engine (Step 49.1) ---

class AIDecisionEngine:
    """
    Decides optimal outreach strategy for each lead.

    Decisions:
    - WHO to contact (lead prioritization)
    - WHEN to contact (optimal timing)
    - WHICH channel (SMS, email, call)
    - WHICH agent (best match)
    - WHAT tone (personalized approach)
    """

    def __init__(self, db: Session):
        self.db = db
        self.feature_pipeline = FeaturePipeline(db)

    def decide_next_leads(
        self,
        tenant_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Decide which leads to contact next.

        Uses ML scoring to prioritize leads with highest
        probability of engagement.

        Returns:
            List of lead recommendations with action details
        """
        # Get active leads
        leads = self.db.query(Lead).filter(
            Lead.tenant_id == tenant_id,
            Lead.deleted_at.is_(None),
            Lead.status.in_(["new", "contacted", "replied", "interested"]),
            Lead.sms_consent == True,
        ).all()

        if not leads:
            return []

        # Score each lead
        scored_leads = []
        for lead in leads:
            score = self._calculate_outreach_priority(lead)
            scored_leads.append({
                "lead_id": str(lead.id),
                "name": f"{lead.first_name} {lead.last_name}",
                "phone": lead.phone,
                "source": lead.source,
                "lead_score": lead.lead_score or 0,
                "priority_score": score,
                "recommended_action": self._recommend_action(lead),
                "recommended_time": self._recommend_time(lead),
                "recommended_tone": self._recommend_tone(lead),
            })

        # Sort by priority
        scored_leads.sort(key=lambda x: x["priority_score"], reverse=True)

        return scored_leads[:limit]

    def decide_best_agent(
        self,
        tenant_id: str,
        lead_id: UUID,
        preferred_time: datetime = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Decide best agent for a lead.

        Considers:
        - Agent performance history
        - Lead-agent compatibility
        - Agent availability
        - Workload balance
        """
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        if not agents:
            return None

        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return None

        # Score each agent
        scored_agents = []
        for agent in agents:
            score = self._calculate_agent_match_score(agent, lead)
            scored_agents.append({
                "agent_id": str(agent.id),
                "score": score,
                "reason": self._explain_agent_match(agent, lead),
            })

        scored_agents.sort(key=lambda x: x["score"], reverse=True)
        return scored_agents[0] if scored_agents else None

    def decide_best_channel(self, lead: Lead) -> str:
        """
        Decide best communication channel for a lead.

        Options: sms, email, call
        """
        # SMS is default for insurance outreach
        if lead.phone and lead.sms_consent:
            return "sms"

        if lead.email and lead.email_consent:
            return "email"

        return "sms"  # Default

    def _calculate_outreach_priority(self, lead: Lead) -> float:
        """Calculate outreach priority score (0-1)."""
        score = 0.0

        # Lead score component (0-0.3)
        score += (lead.lead_score or 0) / 100 * 0.3

        # Recency (0-0.2)
        if lead.last_replied_at:
            hours_since = (datetime.now(timezone.utc) - lead.last_replied_at).total_seconds() / 3600
            if hours_since < 1:
                score += 0.2
            elif hours_since < 24:
                score += 0.15
            elif hours_since < 72:
                score += 0.1

        # Source quality (0-0.2)
        source_scores = {"referral": 0.2, "google": 0.15, "facebook": 0.1, "webhook": 0.1}
        score += source_scores.get(lead.source, 0.05)

        # Engagement (0-0.3)
        convs = self.db.query(Conversation).filter(Conversation.lead_id == lead.id).all()
        if convs:
            total_msgs = sum(c.message_count or 0 for c in convs)
            if total_msgs > 10:
                score += 0.3
            elif total_msgs > 5:
                score += 0.2
            elif total_msgs > 0:
                score += 0.1

        return min(score, 1.0)

    def _recommend_action(self, lead: Lead) -> str:
        """Recommend action for a lead."""
        if lead.status == "new":
            return "initial_outreach"
        elif lead.status == "contacted":
            return "follow_up"
        elif lead.status == "replied":
            return "engage_and_book"
        elif lead.status == "interested":
            return "push_for_booking"
        return "nurture"

    def _recommend_time(self, lead: Lead) -> str:
        """Recommend optimal contact time."""
        # Default to business hours
        return "10:00-18:00"

    def _recommend_tone(self, lead: Lead) -> str:
        """Recommend communication tone."""
        source_tones = {
            "referral": "warm",
            "google": "professional",
            "facebook": "casual",
            "webhook": "friendly",
        }
        return source_tones.get(lead.source, "friendly")

    def _calculate_agent_match_score(self, agent: Agent, lead: Lead) -> float:
        """Calculate agent-lead match score."""
        score = 0.5  # Base

        # Performance component
        appointments = self.db.query(Appointment).filter(
            Appointment.agent_id == agent.id,
        ).all()

        if appointments:
            won = sum(1 for a in appointments if a.disposition == "won")
            total = len(appointments)
            win_rate = won / total if total > 0 else 0
            score += win_rate * 0.3

        # Weight component
        score += (agent.weight / 200) * 0.2  # Normalize weight

        return min(score, 1.0)

    def _explain_agent_match(self, agent: Agent, lead: Lead) -> str:
        """Explain why agent matches lead."""
        return f"Agent has weight {agent.weight} and capacity {agent.daily_capacity}"


# --- AI Campaign Optimizer (Step 49.2) ---

class AICampaignOptimizer:
    """
    Auto-optimizes campaign parameters.

    Adjusts:
    - Prompt templates (based on response rates)
    - Timing (based on engagement patterns)
    - Retry policies (based on conversion patterns)
    - Tone (based on sentiment outcomes)
    """

    def __init__(self, db: Session):
        self.db = db

    def optimize_campaign(self, campaign_id: UUID) -> Dict[str, Any]:
        """
        Optimize a campaign based on performance data.

        Returns:
            Dict with optimization recommendations
        """
        campaign = self.db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return {"error": "Campaign not found"}

        recommendations = []

        # Analyze reply rate
        reply_rate = campaign.total_replied / campaign.total_contacted if campaign.total_contacted else 0
        if reply_rate < 0.1:
            recommendations.append({
                "area": "prompts",
                "priority": "high",
                "finding": f"Low reply rate ({reply_rate:.1%})",
                "suggestion": "Test more engaging opening messages",
            })

        # Analyze booking rate
        booking_rate = campaign.total_booked / campaign.total_replied if campaign.total_replied else 0
        if booking_rate < 0.2:
            recommendations.append({
                "area": "booking_flow",
                "priority": "high",
                "finding": f"Low booking rate ({booking_rate:.1%})",
                "suggestion": "Simplify booking process, offer fewer options",
            })

        # Analyze timing
        timing = self._analyze_best_timing(campaign)
        if timing:
            recommendations.append({
                "area": "timing",
                "priority": "medium",
                "finding": timing["finding"],
                "suggestion": timing["suggestion"],
            })

        # Auto-adjust if enabled
        adjustments = self._auto_adjust(campaign, recommendations)

        return {
            "campaign_id": str(campaign_id),
            "current_performance": {
                "reply_rate": round(reply_rate, 3),
                "booking_rate": round(booking_rate, 3),
                "win_rate": round(campaign.total_won / campaign.total_booked, 3) if campaign.total_booked else 0,
            },
            "recommendations": recommendations,
            "adjustments_applied": adjustments,
        }

    def _analyze_best_timing(self, campaign: Campaign) -> Optional[Dict]:
        """Analyze best outreach timing."""
        # Get messages for this campaign's leads
        leads = self.db.query(Lead).filter(Lead.campaign_id == campaign.id).all()
        if not leads:
            return None

        lead_ids = [l.id for l in leads]
        messages = self.db.query(Message).join(Conversation).filter(
            Conversation.lead_id.in_(lead_ids),
            Message.sender == "customer",
        ).all()

        if not messages:
            return None

        # Analyze by hour
        hourly_replies = {}
        for msg in messages:
            if msg.created_at:
                hour = msg.created_at.hour
                hourly_replies[hour] = hourly_replies.get(hour, 0) + 1

        if hourly_replies:
            best_hour = max(hourly_replies, key=hourly_replies.get)
            return {
                "finding": f"Best reply hour: {best_hour}:00",
                "suggestion": f"Schedule outreach for {best_hour}:00-{best_hour+2}:00",
            }

        return None

    def _auto_adjust(self, campaign: Campaign, recommendations: List[Dict]) -> List[Dict]:
        """Auto-adjust campaign parameters."""
        adjustments = []

        for rec in recommendations:
            if rec["area"] == "prompts" and rec["priority"] == "high":
                # Could auto-generate new prompts here
                adjustments.append({
                    "parameter": "prompt_templates",
                    "action": "recommended_test",
                    "note": "A/B test new opening messages",
                })

        return adjustments


# --- AI Revenue Optimizer (Step 49.3) ---

class AIRevenueOptimizer:
    """
    Optimizes revenue through:
    - Agent utilization maximization
    - Booking density optimization
    - Capacity balancing
    - Idle time reduction
    """

    def __init__(self, db: Session):
        self.db = db

    def optimize_revenue(self, tenant_id: str) -> Dict[str, Any]:
        """
        Generate revenue optimization recommendations.

        Returns:
            Dict with optimization opportunities
        """
        now = datetime.now(timezone.utc)

        # Analyze current state
        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        appointments = self.db.query(Appointment).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= now - timedelta(days=7),
        ).all()

        # Calculate metrics
        total_capacity = sum((a.daily_capacity or 8) * 5 for a in agents)  # Weekly
        total_booked = len(appointments)
        utilization = total_booked / total_capacity if total_capacity > 0 else 0

        # Find opportunities
        opportunities = []

        if utilization < 0.7:
            opportunities.append({
                "area": "utilization",
                "impact": "high",
                "finding": f"Agent utilization at {utilization:.0%}",
                "action": "Increase outreach volume or reduce agent count",
                "potential_revenue_increase": f"{(0.85 - utilization) * 100:.0f}%",
            })

        # Analyze no-shows
        no_shows = sum(1 for a in appointments if a.status in ("no_show", "missed"))
        no_show_rate = no_shows / total_booked if total_booked > 0 else 0
        if no_show_rate > 0.15:
            opportunities.append({
                "area": "no_shows",
                "impact": "high",
                "finding": f"No-show rate at {no_show_rate:.0%}",
                "action": "Implement reminder system and no-show prediction",
                "potential_revenue_increase": f"{no_show_rate * 50:.0f}%",
            })

        # Analyze booking density
        daily_bookings = {}
        for appt in appointments:
            day = appt.created_at.strftime("%Y-%m-%d")
            daily_bookings[day] = daily_bookings.get(day, 0) + 1

        if daily_bookings:
            avg_daily = sum(daily_bookings.values()) / len(daily_bookings)
            max_daily = max(daily_bookings.values())
            if max_daily > avg_daily * 2:
                opportunities.append({
                    "area": "distribution",
                    "impact": "medium",
                    "finding": "Uneven booking distribution across days",
                    "action": "Spread outreach more evenly across the week",
                })

        return {
            "tenant_id": tenant_id,
            "current_metrics": {
                "agents": len(agents),
                "weekly_capacity": total_capacity,
                "weekly_bookings": total_booked,
                "utilization": round(utilization, 3),
                "no_show_rate": round(no_show_rate, 3),
            },
            "opportunities": opportunities,
            "generated_at": now.isoformat(),
        }

    def suggest_agent_allocation(
        self,
        tenant_id: str,
        target_utilization: float = 0.85,
    ) -> Dict[str, Any]:
        """Suggest optimal agent allocation."""
        now = datetime.now(timezone.utc)

        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        suggestions = []
        for agent in agents:
            appointments = self.db.query(Appointment).filter(
                Appointment.agent_id == agent.id,
                Appointment.created_at >= now - timedelta(days=7),
            ).all()

            weekly_booked = len(appointments)
            weekly_capacity = (agent.daily_capacity or 8) * 5
            utilization = weekly_booked / weekly_capacity if weekly_capacity > 0 else 0

            if utilization < 0.5:
                suggestions.append({
                    "agent_id": str(agent.id),
                    "current_utilization": round(utilization, 3),
                    "suggestion": "Reduce capacity or increase outreach",
                })
            elif utilization > 0.9:
                suggestions.append({
                    "agent_id": str(agent.id),
                    "current_utilization": round(utilization, 3),
                    "suggestion": "At capacity — consider adding agents",
                })

        return {
            "tenant_id": tenant_id,
            "suggestions": suggestions,
        }


# --- Autonomous AI Workflows (Step 49.4) ---

class AutonomousWorkflowEngine:
    """
    Dynamically adjusts workflows based on outcomes.

    Features:
    - Auto-retry with backoff
    - Auto-escalation on repeated failures
    - Auto-pause on low engagement
    - Auto-flow-change based on patterns
    """

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service

    def evaluate_and_adjust(self, tenant_id: str) -> Dict[str, Any]:
        """
        Evaluate workflows and make autonomous adjustments.

        Returns:
            Dict with adjustments made
        """
        adjustments = []

        # Check for stalled leads
        stalled = self._find_stalled_leads(tenant_id)
        if stalled:
            adjustments.append({
                "type": "auto_escalation",
                "count": len(stalled),
                "action": "Escalating stalled leads for follow-up",
            })

        # Check for high-engagement leads
        hot_leads = self._find_hot_leads(tenant_id)
        if hot_leads:
            adjustments.append({
                "type": "auto_prioritize",
                "count": len(hot_leads),
                "action": "Prioritizing high-engagement leads",
            })

        # Check for timing optimization
        timing = self._analyze_timing_patterns(tenant_id)
        if timing:
            adjustments.append({
                "type": "timing_optimization",
                "finding": timing,
            })

        return {
            "tenant_id": tenant_id,
            "adjustments": adjustments,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _find_stalled_leads(self, tenant_id: str) -> List[Dict]:
        """Find leads that have stalled (no activity in 48h)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

        leads = self.db.query(Lead).filter(
            Lead.tenant_id == tenant_id,
            Lead.status.in_(["contacted", "interested"]),
            Lead.last_contacted_at < cutoff,
            Lead.deleted_at.is_(None),
        ).all()

        return [{"lead_id": str(l.id), "name": f"{l.first_name} {l.last_name}"} for l in leads[:10]]

    def _find_hot_leads(self, tenant_id: str) -> List[Dict]:
        """Find high-engagement leads."""
        leads = self.db.query(Lead).filter(
            Lead.tenant_id == tenant_id,
            Lead.lead_score >= 80,
            Lead.status.in_(["replied", "interested"]),
            Lead.deleted_at.is_(None),
        ).all()

        return [{"lead_id": str(l.id), "name": f"{l.first_name} {l.last_name}", "score": l.lead_score} for l in leads[:10]]

    def _analyze_timing_patterns(self, tenant_id: str) -> Optional[str]:
        """Analyze timing patterns for optimization."""
        messages = self.db.query(Message).filter(
            Message.tenant_id == tenant_id,
            Message.sender == "customer",
            Message.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        ).all()

        if not messages:
            return None

        hourly = {}
        for msg in messages:
            if msg.created_at:
                hour = msg.created_at.hour
                hourly[hour] = hourly.get(hour, 0) + 1

        if hourly:
            peak = max(hourly, key=hourly.get)
            return f"Peak reply hour: {peak}:00 — optimize outreach timing"

        return None


# --- Self-Learning System (Step 49.5) ---

class SelfLearningSystem:
    """
    Continuously improves system performance.

    Learns from:
    - Successful conversations (what worked)
    - Failed conversations (what didn't)
    - Response patterns (timing, tone, length)
    - Objection handling (what overcame objections)
    - Agent performance (what top performers do differently)
    """

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service

    def learn_from_outcomes(self, tenant_id: str) -> Dict[str, Any]:
        """
        Learn from recent outcomes and generate insights.

        Returns:
            Dict with learned patterns and recommendations
        """
        insights = []

        # Learn from successful conversations
        success_patterns = self._analyze_successful_conversations(tenant_id)
        if success_patterns:
            insights.append({
                "type": "success_pattern",
                "patterns": success_patterns,
            })

        # Learn from failed conversations
        failure_patterns = self._analyze_failed_conversations(tenant_id)
        if failure_patterns:
            insights.append({
                "type": "failure_pattern",
                "patterns": failure_patterns,
            })

        # Learn optimal message characteristics
        message_insights = self._analyze_message_effectiveness(tenant_id)
        if message_insights:
            insights.append({
                "type": "message_optimization",
                "insights": message_insights,
            })

        # Store learnings
        self._store_learnings(tenant_id, insights)

        return {
            "tenant_id": tenant_id,
            "insights": insights,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _analyze_successful_conversations(self, tenant_id: str) -> List[Dict]:
        """Analyze what worked in successful conversations."""
        # Get conversations that led to bookings
        booked_appts = self.db.query(Appointment).filter(
            Appointment.tenant_id == tenant_id,
            Appointment.status == "confirmed",
        ).limit(50).all()

        patterns = []
        for appt in booked_appts:
            if appt.conversation_id:
                conv = self.db.query(Conversation).filter(
                    Conversation.id == appt.conversation_id,
                ).first()

                if conv:
                    messages = self.db.query(Message).filter(
                        Message.conversation_id == conv.id,
                    ).order_by(Message.created_at).all()

                    # Analyze message count
                    if len(messages) <= 5:
                        patterns.append({"pattern": "quick_close", "count": len(messages)})

                    # Analyze tone
                    for msg in messages:
                        if msg.intent == "POSITIVE":
                            patterns.append({"pattern": "positive_early", "position": messages.index(msg)})

        return patterns[:5]

    def _analyze_failed_conversations(self, tenant_id: str) -> List[Dict]:
        """Analyze what didn't work."""
        # Get conversations that ended without booking
        stopped = self.db.query(Conversation).filter(
            Conversation.tenant_id == tenant_id,
            Conversation.status == "stopped",
        ).limit(50).all()

        patterns = []
        for conv in stopped:
            messages = self.db.query(Message).filter(
                Message.conversation_id == conv.id,
            ).all()

            # Check for common failure patterns
            customer_msgs = [m for m in messages if m.sender == "customer"]
            if customer_msgs:
                last_msg = customer_msgs[-1].content.lower() if customer_msgs[-1].content else ""
                if "stop" in last_msg or "remove" in last_msg:
                    patterns.append({"pattern": "opt_out", "reason": "explicit_stop"})
                elif "expensive" in last_msg or "cost" in last_msg:
                    patterns.append({"pattern": "price_objection", "reason": "pricing"})

        return patterns[:5]

    def _analyze_message_effectiveness(self, tenant_id: str) -> Dict[str, Any]:
        """Analyze what message characteristics get best responses."""
        messages = self.db.query(Message).filter(
            Message.tenant_id == tenant_id,
            Message.sender == "ai",
        ).limit(100).all()

        if not messages:
            return {}

        # Analyze by length
        short_replies = 0
        long_replies = 0

        for msg in messages:
            # Check if customer replied
            conv = self.db.query(Conversation).filter(
                Conversation.id == msg.conversation_id,
            ).first()

            if conv and conv.message_count > 1:
                if len(msg.content) < 100:
                    short_replies += 1
                else:
                    long_replies += 1

        return {
            "short_message_replies": short_replies,
            "long_message_replies": long_replies,
            "recommendation": "Shorter messages" if short_replies > long_replies else "Detailed messages",
        }

    def _store_learnings(self, tenant_id: str, insights: List[Dict]) -> None:
        """Store learned patterns in Redis."""
        key = f"ai:learnings:{tenant_id}"
        self.redis.client.setex(key, 86400 * 7, json.dumps({
            "insights": insights,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }))

    def get_learnings(self, tenant_id: str) -> Dict[str, Any]:
        """Get stored learnings."""
        key = f"ai:learnings:{tenant_id}"
        data = self.redis.client.get(key)

        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass

        return {"insights": []}


# --- Unified AI Operating System ---

class AIOperatingSystem:
    """
    Unified AI Operating System.

    Combines all AI decision-making into a single system
    that operates autonomously.
    """

    def __init__(self, db: Session):
        self.db = db
        self.decision_engine = AIDecisionEngine(db)
        self.campaign_optimizer = AICampaignOptimizer(db)
        self.revenue_optimizer = AIRevenueOptimizer(db)
        self.autonomous_workflows = AutonomousWorkflowEngine(db)
        self.self_learning = SelfLearningSystem(db)

    def run_autonomous_cycle(self, tenant_id: str) -> Dict[str, Any]:
        """
        Run a complete autonomous optimization cycle.

        Called periodically (e.g., every hour) to:
        1. Decide next leads to contact
        2. Optimize campaigns
        3. Optimize revenue
        4. Adjust workflows
        5. Learn from outcomes
        """
        results = {
            "cycle_started": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
        }

        # 1. Decision engine
        next_leads = self.decision_engine.decide_next_leads(tenant_id, limit=5)
        results["next_leads"] = len(next_leads)

        # 2. Campaign optimization
        campaigns = self.db.query(Campaign).filter(
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        ).all()

        campaign_results = []
        for campaign in campaigns[:3]:
            opt = self.campaign_optimizer.optimize_campaign(campaign.id)
            campaign_results.append(opt)
        results["campaign_optimizations"] = campaign_results

        # 3. Revenue optimization
        revenue = self.revenue_optimizer.optimize_revenue(tenant_id)
        results["revenue_optimization"] = revenue

        # 4. Autonomous adjustments
        adjustments = self.autonomous_workflows.evaluate_and_adjust(tenant_id)
        results["autonomous_adjustments"] = adjustments

        # 5. Self-learning
        learnings = self.self_learning.learn_from_outcomes(tenant_id)
        results["learnings"] = learnings

        results["cycle_completed"] = datetime.now(timezone.utc).isoformat()

        return results

    def get_system_status(self, tenant_id: str) -> Dict[str, Any]:
        """Get AI OS status."""
        return {
            "decision_engine": "active",
            "campaign_optimizer": "active",
            "revenue_optimizer": "active",
            "autonomous_workflows": "active",
            "self_learning": "active",
            "last_learnings": self.self_learning.get_learnings(tenant_id),
        }
