"""
ML Router

Endpoints:
- GET /ml/predict/{lead_id} — Predict lead scores
- GET /ml/predict/batch — Batch predict scores
- GET /ml/timing/outreach — Best outreach time
- GET /ml/timing/appointments — Best appointment time
- GET /ml/timing/lead/{lead_id} — Best time for specific lead
- GET /ml/agents/ranking — Agent rankings
- GET /ml/agents/{agent_id}/metrics — Agent metrics
- GET /ml/agents/{agent_id}/trends — Agent trends
- GET /ml/agents/best/{lead_id} — Best agent for lead
- GET /ml/optimization — Optimization recommendations
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.models.lead import Lead
from app.ml.predictive_scoring import predict_lead_scores, batch_predict
from app.ml.time_prediction import (
    get_best_outreach_time,
    get_best_appointment_time,
    predict_best_time_for_lead,
)
from app.ml.agent_performance import (
    calculate_agent_metrics,
    rank_agents,
    find_best_agent_for_lead,
    get_agent_trends,
)
from app.ml.prompt_optimization import get_optimization_recommendations

router = APIRouter(prefix="/ml", tags=["ml"])


@router.get("/predict/{lead_id}")
def predict_scores(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Predict booking and conversion scores for a lead."""
    return predict_lead_scores(db, lead_id)


@router.get("/predict/batch")
def batch_predict_scores(
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Batch predict scores for all active leads."""
    results = batch_predict(db, tenant_id, limit)
    return {"predictions": results, "total": len(results)}


@router.get("/timing/outreach")
def best_outreach_time(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get the best time to send outreach messages."""
    return get_best_outreach_time(db, tenant_id)


@router.get("/timing/appointments")
def best_appointment_time(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get the best time to schedule appointments."""
    return get_best_appointment_time(db, tenant_id)


@router.get("/timing/lead/{lead_id}")
def best_time_for_lead(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get the best outreach time for a specific lead."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        return {"error": "Lead not found"}
    return predict_best_time_for_lead(db, lead)


@router.get("/agents/ranking")
def agent_rankings(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agent performance rankings."""
    rankings = rank_agents(db, tenant_id, days)
    return {"rankings": rankings, "total": len(rankings)}


@router.get("/agents/{agent_id}/metrics")
def agent_metrics(
    agent_id: UUID,
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get detailed metrics for an agent."""
    return calculate_agent_metrics(db, agent_id, days)


@router.get("/agents/{agent_id}/trends")
def agent_trends(
    agent_id: UUID,
    weeks: int = Query(12, ge=1, le=52),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get agent performance trends over time."""
    trends = get_agent_trends(db, agent_id, weeks)
    return {"trends": trends, "total": len(trends)}


@router.get("/agents/best/{lead_id}")
def best_agent_for_lead(
    lead_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Find the best agent for a specific lead."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        return {"error": "Lead not found"}
    result = find_best_agent_for_lead(db, lead)
    if not result:
        return {"error": "No agents available"}
    return result


@router.get("/optimization")
def optimization_recommendations(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get optimization recommendations based on data analysis."""
    return get_optimization_recommendations(db, tenant_id)
