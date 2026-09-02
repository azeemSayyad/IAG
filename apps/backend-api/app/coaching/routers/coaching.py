"""
AI Sales Coaching Router (Phase 42)

Exposes agent performance analysis, coaching insights, rankings, and real-time coaching.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.core.permissions import require_role
from app.models.user import User
from app.models.agent import Agent
from app.coaching.performance import PerformanceAnalyzer
from app.coaching.insights import CoachingInsightsGenerator
from app.coaching.ranking import AgentRankingSystem
from app.coaching.ai_coach import generate_ai_insights
from app.core.redis import redis_service

router = APIRouter(prefix="/coaching", tags=["coaching"])

# Coach Mode loads on every workspace page, so we cache the AI-generated
# (Ollama) coaching per agent for an hour — the model is hit at most once an
# hour per agent instead of on every page load, which keeps the RunPod/Ollama
# box light and the banner fast. Metrics move slowly, so an hour is plenty.
_AI_COACH_TTL = 3600


async def _ai_insights_for(agent_id: str, generator: CoachingInsightsGenerator, days: int):
    """AI coaching insights (cached), or None to fall back to the rule-based set."""
    cache_key = f"coach:ai:{agent_id}:{days}"
    try:
        cached = redis_service.get_cache(cache_key)
    except Exception:
        cached = None
    if isinstance(cached, list) and cached:
        return cached
    report = generator.analyzer.analyze_agent(agent_id, days)
    agent_name = getattr(report, "agent_name", None) or "the agent"
    insights = await generate_ai_insights(report.metrics, agent_name)
    if insights:
        try:
            redis_service.set_cache(cache_key, insights, ttl=_AI_COACH_TTL)
        except Exception:
            pass
    return insights


def _summary_from_dicts(agent_id: str, insights: list) -> dict:
    """Build the coaching-summary shape from a list of insight dicts (AI path)."""
    high = [i for i in insights if i.get("priority") == "high"]
    medium = [i for i in insights if i.get("priority") == "medium"]
    return {
        "agent_id": str(agent_id),
        "total_insights": len(insights),
        "high_priority": len(high),
        "medium_priority": len(medium),
        "top_focus_areas": [i["title"] for i in high[:3]],
        "quick_wins": [i["action_items"][0] for i in insights if i.get("action_items")][:3],
        "insights": insights,
        "source": "ai",
    }


def resolve_agent_id(agent_id: str, db: Session, current_user: User) -> str:
    """Resolve the special value 'me' to the current user's agent id; otherwise validate the UUID."""
    if agent_id == "me":
        agent = db.query(Agent).filter(Agent.user_id == current_user.id).first()
        if not agent:
            raise HTTPException(status_code=404, detail="No agent profile for current user")
        return str(agent.id)
    try:
        return str(UUID(agent_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid agent_id")


# --- Dependency injection factories (request-scoped) ---

def get_performance_analyzer(db: Session = Depends(get_db)):
    return PerformanceAnalyzer(db)


def get_insights_generator(db: Session = Depends(get_db)):
    return CoachingInsightsGenerator(db)


def get_ranking_system(db: Session = Depends(get_db)):
    return AgentRankingSystem(db)


# --- Performance Analysis ---

@router.get("/performance/{agent_id}", status_code=status.HTTP_200_OK)
def get_agent_performance(
    agent_id: str,
    days: int = Query(30, ge=7, le=365),
    analyzer: PerformanceAnalyzer = Depends(get_performance_analyzer),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get comprehensive performance analysis for an agent. Accepts 'me' for the current user's agent."""
    agent_id = resolve_agent_id(agent_id, db, current_user)
    report = analyzer.analyze_agent(str(agent_id), days)
    if not report:
        raise HTTPException(status_code=404, detail="Agent not found or no data")
    return report.to_dict() if hasattr(report, "to_dict") else report


@router.get("/performance/team/summary", status_code=status.HTTP_200_OK)
def get_team_performance_summary(
    days: int = Query(30, ge=7, le=365),
    analyzer: PerformanceAnalyzer = Depends(get_performance_analyzer),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get team-wide performance summary."""
    summary = analyzer.get_team_summary(tenant_id, days)
    return summary


# --- Coaching Insights ---

@router.get("/insights/{agent_id}", status_code=status.HTTP_200_OK)
async def get_coaching_insights(
    agent_id: str,
    days: int = Query(30, ge=1, le=120),
    generator: CoachingInsightsGenerator = Depends(get_insights_generator),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Personalized coaching insights for an agent, focused on converting leads
    into closed deals. Uses Ollama AI when available (cached per agent), and
    falls back to the deterministic rule-based insights otherwise. Accepts 'me'."""
    agent_id = resolve_agent_id(agent_id, db, current_user)
    ai = await _ai_insights_for(agent_id, generator, days)
    if ai:
        return {"insights": ai, "source": "ai"}
    insights = generator.generate_insights(str(agent_id), days)
    serialized = [i.to_dict() if hasattr(i, "to_dict") else i for i in insights]
    return {"insights": serialized, "source": "rules"}


@router.get("/insights/{agent_id}/summary", status_code=status.HTTP_200_OK)
async def get_coaching_summary(
    agent_id: str,
    days: int = Query(30, ge=1, le=120),
    generator: CoachingInsightsGenerator = Depends(get_insights_generator),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Coaching summary (top focus areas + quick wins), conversion-focused.
    Uses Ollama AI when available (cached), else the rule-based summary. Accepts 'me'."""
    agent_id = resolve_agent_id(agent_id, db, current_user)
    ai = await _ai_insights_for(agent_id, generator, days)
    if ai:
        return _summary_from_dicts(agent_id, ai)
    return generator.get_coaching_summary(str(agent_id), days)


# --- Agent Rankings ---

@router.get("/rankings/leaderboard", status_code=status.HTTP_200_OK)
def get_leaderboard(
    days: int = Query(30, ge=7, le=365),
    ranking: AgentRankingSystem = Depends(get_ranking_system),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get overall agent leaderboard."""
    leaderboard = ranking.get_overall_leaderboard(tenant_id, days)
    return {"leaderboard": leaderboard}


@router.get("/rankings/top-closers", status_code=status.HTTP_200_OK)
def get_top_closers(
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(10, ge=1, le=50),
    ranking: AgentRankingSystem = Depends(get_ranking_system),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get top closing agents by win rate."""
    closers = ranking.get_top_closers(tenant_id, days, limit)
    return {"top_closers": closers}


@router.get("/rankings/fastest-closers", status_code=status.HTTP_200_OK)
def get_fastest_closers(
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(10, ge=1, le=50),
    ranking: AgentRankingSystem = Depends(get_ranking_system),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get fastest closing agents by average call duration."""
    closers = ranking.get_fastest_closers(tenant_id, days, limit)
    return {"fastest_closers": closers}


@router.get("/rankings/best-show-rates", status_code=status.HTTP_200_OK)
def get_best_show_rates(
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(10, ge=1, le=50),
    ranking: AgentRankingSystem = Depends(get_ranking_system),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agents with best show rates (lowest no-show)."""
    agents = ranking.get_best_show_rates(tenant_id, days, limit)
    return {"best_show_rates": agents}


@router.get("/rankings/badges/{agent_id}", status_code=status.HTTP_200_OK)
def get_agent_badges(
    agent_id: str,
    days: int = Query(30, ge=7, le=365),
    ranking: AgentRankingSystem = Depends(get_ranking_system),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get achievement badges for an agent. Accepts 'me' for the current user's agent."""
    agent_id = resolve_agent_id(agent_id, db, current_user)
    badges = ranking.get_agent_badges(str(agent_id), days)
    return {"badges": badges}


# --- Real-time Coaching (HTTP endpoint for triggering) ---

@router.post("/realtime/coach/{agent_id}", status_code=status.HTTP_200_OK)
async def trigger_coaching(
    agent_id: UUID,
    segment_text: str,
    speaker: str = Query("customer", pattern="^(customer|agent)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Manually trigger real-time coaching for a transcript segment."""
    from app.coaching.realtime import RealTimeCoach
    coach = RealTimeCoach(db)
    cues = coach.process_transcript_segment(
        agent_id=str(agent_id),
        text=segment_text,
        speaker=speaker,
        tenant_id=tenant_id,
    )
    return {"cues": [c.to_dict() for c in cues] if cues else []}
