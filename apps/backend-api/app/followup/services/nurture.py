"""
Cold Lead Nurturing (Step 8.4)

Long-term nurture campaigns for cold leads:
- Weekly touchpoints
- Monthly check-ins
- Re-engagement on trigger events
- Seasonal campaigns
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.ai.services.prompts import get_outreach_message
from app.ai.services.communication_provider import send_sms_to_lead
from app.ai.services.humanizer import humanize_message
from app.core.redis import redis_service
from app.core.audit import log_ai_action


# Nurture campaign schedule
NURTURE_SCHEDULE = [
    {
        "step": 1,
        "delay_days": 7,
        "tone": "friendly",
        "description": "Week 1 - Gentle check-in",
    },
    {
        "step": 2,
        "delay_days": 14,
        "tone": "professional",
        "description": "Week 2 - Value proposition",
    },
    {
        "step": 3,
        "delay_days": 30,
        "tone": "friendly",
        "description": "Month 1 - Re-engagement",
    },
    {
        "step": 4,
        "delay_days": 60,
        "tone": "professional",
        "description": "Month 2 - New offer",
    },
    {
        "step": 5,
        "delay_days": 90,
        "tone": "urgent",
        "description": "Month 3 - Final attempt",
    },
]


def get_nurture_leads(db: Session, tenant_id: str = None) -> List[Dict]:
    """
    Get leads that should be in nurture campaigns.

    Criteria:
    - Status is 'nurture' or 'unqualified'
    - Last contact was more than 7 days ago
    - Haven't exceeded max nurture steps
    """
    now = datetime.now(timezone.utc)

    query = (
        db.query(Lead)
        .filter(
            Lead.status.in_(["nurture", "unqualified", "cold"]),
            Lead.deleted_at.is_(None),
        )
    )

    if tenant_id:
        query = query.filter(Lead.tenant_id == tenant_id)

    leads = query.all()

    nurture_leads = []
    for lead in leads:
        # Check nurture step count
        nurture_count = redis_service.client.get(f"nurture:count:{lead.id}") or 0
        nurture_count = int(nurture_count)

        if nurture_count >= len(NURTURE_SCHEDULE):
            continue

        # Check if enough time has passed since last contact
        last_contact = lead.last_contacted_at or lead.created_at
        days_since = (now - last_contact).total_seconds() / 86400

        step = NURTURE_SCHEDULE[nurture_count]
        if days_since >= step["delay_days"]:
            nurture_leads.append({
                "lead": lead,
                "step": step,
                "nurture_count": nurture_count,
                "days_since_contact": round(days_since),
            })

    return nurture_leads


def process_nurture_lead(
    db: Session,
    lead: Lead,
    step: Dict,
) -> Dict:
    """
    Process a nurture follow-up for a lead.
    """
    tenant_id = str(lead.tenant_id)
    lead_id = str(lead.id)

    # Queue-Only Mode: no automated nurture drips while booking autopilot is paused.
    from app.core.sending import is_autopilot_paused
    if is_autopilot_paused(tenant_id):
        return {"success": False, "skipped": "autopilot_paused", "lead_id": lead_id}

    # Get message with appropriate tone
    message = get_outreach_message(
        first_name=lead.first_name,
        tone=step["tone"],
        source=lead.source,
        tenant_id=lead.tenant_id,
    )

    # Humanize
    message = humanize_message(message, tone=step["tone"])

    # Send SMS
    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    if result.get("success"):
        # Increment nurture count
        redis_service.client.incr(f"nurture:count:{lead.id}")
        redis_service.client.expire(f"nurture:count:{lead.id}", 86400 * 180)  # 180 days

        # Update lead
        lead.last_contacted_at = datetime.now(timezone.utc)
        lead.status = "contacted"  # Move back to contacted
        db.commit()

        # Audit
        log_ai_action(
            tenant_id=tenant_id,
            action=f"nurture_step_{step['step']}",
            resource_type="lead",
            resource_id=lead_id,
            details={"step": step["step"], "tone": step["tone"]},
        )

    return {
        "success": result.get("success", False),
        "step": step["step"],
        "message": message[:50],
    }


def process_all_nurture_leads(db: Session, tenant_id: str = None) -> Dict:
    """
    Process all leads that need nurture follow-up.
    """
    nurture_leads = get_nurture_leads(db, tenant_id)

    processed = 0
    failed = 0

    for entry in nurture_leads:
        result = process_nurture_lead(db, entry["lead"], entry["step"])
        if result["success"]:
            processed += 1
        else:
            failed += 1

    return {
        "total_checked": len(nurture_leads),
        "processed": processed,
        "failed": failed,
    }


def move_to_nurture(db: Session, lead_id: UUID, tenant_id: str) -> Dict:
    """
    Move a lead to nurture campaign.
    """
    lead = (
        db.query(Lead)
        .filter(
            Lead.id == lead_id,
            Lead.tenant_id == tenant_id,
        )
        .first()
    )

    if not lead:
        return {"success": False, "error": "Lead not found"}

    lead.status = "nurture"
    db.commit()

    # Reset nurture counter
    redis_service.client.delete(f"nurture:count:{lead.id}")

    # Audit
    log_ai_action(
        tenant_id=tenant_id,
        action="moved_to_nurture",
        resource_type="lead",
        resource_id=str(lead.id),
    )

    return {"success": True, "lead_id": str(lead.id)}


def get_nurture_status(lead_id: str) -> Dict:
    """Get the nurture campaign status for a lead."""
    count = redis_service.client.get(f"nurture:count:{lead_id}") or 0
    count = int(count)

    return {
        "lead_id": lead_id,
        "nurture_step": count,
        "max_steps": len(NURTURE_SCHEDULE),
        "next_step": NURTURE_SCHEDULE[count] if count < len(NURTURE_SCHEDULE) else None,
        "completed": count >= len(NURTURE_SCHEDULE),
    }


def re_engage_lead(db: Session, lead_id: UUID, tenant_id: str, reason: str = None) -> Dict:
    """
    Re-engage a nurtured lead (e.g., they showed interest again).
    """
    lead = (
        db.query(Lead)
        .filter(
            Lead.id == lead_id,
            Lead.tenant_id == tenant_id,
        )
        .first()
    )

    if not lead:
        return {"success": False, "error": "Lead not found"}

    # Reset nurture counter
    redis_service.client.delete(f"nurture:count:{lead.id}")

    # Update status
    lead.status = "replied"
    db.commit()

    # Audit
    log_ai_action(
        tenant_id=tenant_id,
        action="lead_re_engaged",
        resource_type="lead",
        resource_id=str(lead.id),
        details={"reason": reason},
    )

    return {"success": True, "lead_id": str(lead.id)}
