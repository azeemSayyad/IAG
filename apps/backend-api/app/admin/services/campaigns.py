"""
Campaign Builder Service (Step 9.1)

Configure campaigns with:
- Prompts and AI tone
- Retry timing
- Booking logic
- Targeting rules
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.lead import Lead
from app.core.audit import log_create, log_update


def create_campaign(
    db: Session,
    tenant_id: str,
    data: Dict,
) -> Campaign:
    """
    Create a new campaign.
    """
    campaign = Campaign(
        tenant_id=tenant_id,
        name=data["name"],
        description=data.get("description"),
        tone=data.get("tone", "friendly"),
        prompt_template=data.get("prompt_template"),
        objection_prompts=data.get("objection_prompts", {}),
        max_retries=data.get("max_retries", 3),
        retry_delay_hours=data.get("retry_delay_hours", 24),
        retry_tones=data.get("retry_tones", ["friendly", "professional", "urgent"]),
        booking_enabled=data.get("booking_enabled", True),
        slot_duration_minutes=data.get("slot_duration_minutes", 15),
        max_days_ahead=data.get("max_days_ahead", 3),
        business_hours_start=data.get("business_hours_start", 10),
        business_hours_end=data.get("business_hours_end", 21),
        target_sources=data.get("target_sources", []),
        target_states=data.get("target_states", []),
        min_lead_score=data.get("min_lead_score", 0),
        max_lead_score=data.get("max_lead_score", 100),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    log_create(
        tenant_id=tenant_id,
        user_id=None,
        resource_type="campaign",
        resource_id=str(campaign.id),
        details={"name": campaign.name},
    )

    return campaign


def update_campaign(
    db: Session,
    campaign_id: UUID,
    tenant_id: str,
    data: Dict,
) -> Optional[Campaign]:
    """
    Update an existing campaign.
    """
    campaign = (
        db.query(Campaign)
        .filter(
            Campaign.id == campaign_id,
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        )
        .first()
    )

    if not campaign:
        return None

    # Update fields
    for key, value in data.items():
        if hasattr(campaign, key) and key not in ("id", "tenant_id", "created_at"):
            setattr(campaign, key, value)

    db.commit()
    db.refresh(campaign)

    log_update(
        tenant_id=tenant_id,
        user_id=None,
        resource_type="campaign",
        resource_id=str(campaign.id),
        details={"updated_fields": list(data.keys())},
    )

    return campaign


def get_campaign(
    db: Session,
    campaign_id: UUID,
    tenant_id: str,
) -> Optional[Campaign]:
    """
    Get a campaign by ID.
    """
    return (
        db.query(Campaign)
        .filter(
            Campaign.id == campaign_id,
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        )
        .first()
    )


def list_campaigns(
    db: Session,
    tenant_id: str,
    status: str = None,
) -> List[Campaign]:
    """
    List all campaigns for a tenant.
    """
    query = (
        db.query(Campaign)
        .filter(
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        )
    )

    if status:
        query = query.filter(Campaign.status == status)

    return query.order_by(Campaign.created_at.desc()).all()


def delete_campaign(
    db: Session,
    campaign_id: UUID,
    tenant_id: str,
) -> bool:
    """
    Soft delete a campaign.
    """
    campaign = get_campaign(db, campaign_id, tenant_id)
    if not campaign:
        return False

    campaign.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return True


def match_lead_to_campaign(
    db: Session,
    lead: Lead,
) -> Optional[Campaign]:
    """
    Find the best matching campaign for a lead based on targeting rules.
    """
    campaigns = (
        db.query(Campaign)
        .filter(
            Campaign.tenant_id == lead.tenant_id,
            Campaign.status == "active",
            Campaign.deleted_at.is_(None),
        )
        .all()
    )

    best_match = None
    best_score = 0

    for campaign in campaigns:
        score = 0

        # Check source targeting
        if campaign.target_sources:
            if lead.source in campaign.target_sources:
                score += 30
        else:
            score += 10  # No source filter = matches all

        # Check state targeting
        if campaign.target_states:
            if lead.state and lead.state.upper() in campaign.target_states:
                score += 30
        else:
            score += 10  # No state filter = matches all

        # Check lead score range
        if campaign.min_lead_score <= lead.lead_score <= campaign.max_lead_score:
            score += 20

        if score > best_score:
            best_score = score
            best_match = campaign

    return best_match


def update_campaign_stats(
    db: Session,
    campaign_id: UUID,
    stat_field: str,
) -> None:
    """
    Increment a campaign statistic.
    """
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign and hasattr(campaign, stat_field):
        current = getattr(campaign, stat_field, 0) or 0
        setattr(campaign, stat_field, current + 1)
        db.commit()


def get_campaign_performance(
    db: Session,
    campaign_id: UUID,
    tenant_id: str,
) -> Dict:
    """
    Get campaign performance metrics.
    """
    campaign = get_campaign(db, campaign_id, tenant_id)
    if not campaign:
        return {}

    contacted = campaign.total_contacted or 0
    replied = campaign.total_replied or 0
    booked = campaign.total_booked or 0
    completed = campaign.total_completed or 0
    won = campaign.total_won or 0

    return {
        "campaign_id": str(campaign.id),
        "name": campaign.name,
        "status": campaign.status,
        "metrics": {
            "total_leads": campaign.total_leads or 0,
            "total_contacted": contacted,
            "total_replied": replied,
            "total_booked": booked,
            "total_completed": completed,
            "total_won": won,
            "reply_rate": round(replied / contacted * 100, 1) if contacted > 0 else 0,
            "booking_rate": round(booked / contacted * 100, 1) if contacted > 0 else 0,
            "conversion_rate": round(won / completed * 100, 1) if completed > 0 else 0,
        },
        "config": {
            "tone": campaign.tone,
            "max_retries": campaign.max_retries,
            "retry_delay_hours": campaign.retry_delay_hours,
        },
    }
