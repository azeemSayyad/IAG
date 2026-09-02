"""
Event Trigger System (Step 3.5)

Triggers:
- Lead Created → Score lead → Assign Campaign → Queue AI Outreach
- Lead Updated → Re-score if needed
- Lead Replied → Update engagement score
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.core.redis import redis_service
from app.core.audit import log_ai_action


def on_lead_created(db: Session, lead: Lead) -> Dict[str, Any]:
    """
    Triggered when a new lead is created.

    Actions:
    1. Score the lead
    2. Assign to campaign (if source matches)
    3. Queue AI outreach
    """
    from app.ingestion.services.scoring import calculate_lead_score, get_score_tier

    # Customer-facing display timezone: Eastern for everyone EXCEPT Texas leads,
    # who see Central time in their SMS. State-based only (no ZIP/Geoapify lookup).
    from app.core.timezones import lead_display_timezone
    lead.timezone = lead_display_timezone(getattr(lead, "state", None))

    # Calculate lead score
    lead_data = {
        "source": lead.source,
        "state": lead.state,
        "email": lead.email,
        "phone": lead.phone,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "city": lead.city,
        "zip_code": lead.zip_code,
    }
    score = calculate_lead_score(lead_data, created_at=lead.created_at)
    tier = get_score_tier(score)

    # Update lead score
    lead.lead_score = score
    db.commit()

    # NOTE: the Appointment Capacity Engine (hold + paced release) applies ONLY to
    # large bulk imports (>500 rows, handled in bulk_import_leads_from_csv). This
    # per-row path (<=500 rows, plus single/API leads) always blasts outreach
    # immediately and books normally — small batches don't need pacing.

    # AI auto-distribution: hand the lead to an eligible (compliance-licensed,
    # capacity-available) agent. Best-effort — never block lead creation.
    assigned_agent_id = None
    try:
        from app.leads.services.distribution import auto_assign_lead
        agent = auto_assign_lead(db, lead, commit=True)
        if agent is not None:
            assigned_agent_id = str(agent.id)
            log_ai_action(
                tenant_id=str(lead.tenant_id),
                action="lead_assigned",
                resource_type="lead",
                resource_id=str(lead.id),
                details={"agent_id": assigned_agent_id, "auto": True},
            )
    except Exception:
        pass

    # Queue AI outreach via Redis
    outreach_job = {
        "lead_id": str(lead.id),
        "tenant_id": str(lead.tenant_id),
        "lead_name": f"{lead.first_name} {lead.last_name}",
        "phone": lead.phone,
        "source": lead.source,
        "score": score,
        "tier": tier,
        "kind": "first_template",   # the ONLY message allowed past the send chokepoint
    }
    redis_service.enqueue_sms(outreach_job)

    # Audit log
    log_ai_action(
        tenant_id=str(lead.tenant_id),
        action="outreach_queued",
        resource_type="lead",
        resource_id=str(lead.id),
        details={"score": score, "tier": tier},
    )

    return {"score": score, "tier": tier, "outreach_queued": True}


def on_lead_updated(db: Session, lead: Lead, changed_fields: list) -> Optional[Dict[str, Any]]:
    """
    Triggered when a lead is updated.

    Re-scores if scoring-relevant fields changed.
    """
    # Re-derive display timezone if the state changed (Texas -> Central, else Eastern).
    if "state" in changed_fields:
        from app.core.timezones import lead_display_timezone
        lead.timezone = lead_display_timezone(getattr(lead, "state", None))
        db.commit()

    scoring_fields = {"source", "state", "email", "phone"}
    if not scoring_fields.intersection(changed_fields):
        return None

    from app.ingestion.services.scoring import calculate_lead_score, get_score_tier

    lead_data = {
        "source": lead.source,
        "state": lead.state,
        "email": lead.email,
        "phone": lead.phone,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "city": lead.city,
        "zip_code": lead.zip_code,
    }
    score = calculate_lead_score(lead_data, created_at=lead.created_at)
    tier = get_score_tier(score)

    lead.lead_score = score
    db.commit()

    return {"score": score, "tier": tier}


def on_lead_replied(db: Session, lead: Lead, conversation: Conversation) -> Dict[str, Any]:
    """
    Triggered when a lead replies to AI outreach.

    Updates engagement metrics and re-scores.
    """
    from app.ingestion.services.scoring import calculate_lead_score, get_score_tier

    # Update lead status
    if lead.status in ("new", "contacted"):
        lead.status = "replied"
    lead.last_replied_at = datetime.now(timezone.utc)
    db.commit()

    # Re-score with engagement
    lead_data = {
        "source": lead.source,
        "state": lead.state,
        "email": lead.email,
        "phone": lead.phone,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
    }
    score = calculate_lead_score(
        lead_data,
        created_at=lead.created_at,
        message_count=conversation.message_count,
        has_replied=True,
    )
    tier = get_score_tier(score)

    lead.lead_score = score
    db.commit()

    # Audit log
    log_ai_action(
        tenant_id=str(lead.tenant_id),
        action="lead_replied",
        resource_type="lead",
        resource_id=str(lead.id),
        details={"new_score": score, "new_tier": tier},
    )

    return {"score": score, "tier": tier, "status": lead.status}
