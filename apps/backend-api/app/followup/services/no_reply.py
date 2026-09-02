"""
No Reply Flow (Step 8.2)

Handles leads that don't reply to outreach:
1. No Reply within 24h → Retry with different tone
2. Still No Reply (48h) → Retry with social proof
3. Still No Reply (72h) → Final attempt
4. No Reply → Move to nurture campaign
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.ai.services.prompts import get_followup_message, get_outreach_message
from app.ai.services.communication_provider import send_sms_to_lead
from app.ai.services.humanizer import humanize_message
from app.core.redis import redis_service
from app.core.audit import log_ai_action


# No reply flow configuration
NO_REPLY_STEPS = [
    {
        "step": 1,
        "delay_hours": 24,
        "tone": "friendly",
        "message_type": "no_reply_1",
        "description": "First follow-up (24h)",
    },
    {
        "step": 2,
        "delay_hours": 24,
        "tone": "professional",
        "message_type": "no_reply_2",
        "description": "Second follow-up (48h) - social proof",
    },
    {
        "step": 3,
        "delay_hours": 24,
        "tone": "urgent",
        "message_type": "no_reply_3",
        "description": "Final attempt (72h)",
    },
]


def check_no_reply_leads(db: Session, tenant_id: str = None) -> List[Dict]:
    """
    Find leads that haven't replied within the expected timeframe.

    Returns:
        List of leads that need follow-up.
    """
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    query = (
        db.query(Lead)
        .filter(
            Lead.deleted_at.is_(None),   # never follow up with removed/soft-deleted leads
            Lead.status.in_(["new", "contacted"]),
            Lead.last_contacted_at.isnot(None),
            Lead.last_contacted_at < cutoff_24h,
        )
    )

    if tenant_id:
        query = query.filter(Lead.tenant_id == tenant_id)

    leads = query.all()

    no_reply_leads = []
    for lead in leads:
        # Check if there are any customer replies
        conversation = (
            db.query(Conversation)
            .filter(Conversation.lead_id == lead.id)
            .first()
        )

        if not conversation:
            continue

        # Check last customer message
        last_customer_msg = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.sender == "customer",
            )
            .order_by(Message.created_at.desc())
            .first()
        )

        # If no customer message or customer message is older than last AI message
        if not last_customer_msg or last_customer_msg.created_at < lead.last_contacted_at:
            hours_since_contact = (now - lead.last_contacted_at).total_seconds() / 3600

            # Determine which step to execute
            followup_count = redis_service.client.get(f"no_reply:count:{lead.id}") or 0
            followup_count = int(followup_count)

            if followup_count < len(NO_REPLY_STEPS):
                step = NO_REPLY_STEPS[followup_count]
                if hours_since_contact >= step["delay_hours"]:
                    no_reply_leads.append({
                        "lead": lead,
                        "step": step,
                        "followup_count": followup_count,
                        "hours_since_contact": round(hours_since_contact, 1),
                    })

    return no_reply_leads


def process_no_reply_followup(
    db: Session,
    lead: Lead,
    step: Dict,
) -> Dict:
    """
    Process a no-reply follow-up for a lead.

    Returns:
        Dict with success status and details.
    """
    tenant_id = str(lead.tenant_id)
    lead_id = str(lead.id)

    # Queue-Only Mode: no automated no-reply nudges while booking autopilot is
    # paused (first template only, then total system silence).
    from app.core.sending import is_autopilot_paused
    if is_autopilot_paused(tenant_id):
        return {"success": False, "skipped": "autopilot_paused", "lead_id": lead_id}

    # Get message
    if step["message_type"] == "no_reply_1":
        message = get_followup_message(lead.first_name, followup_number=1)
    elif step["message_type"] == "no_reply_2":
        message = get_followup_message(lead.first_name, followup_number=2)
    elif step["message_type"] == "no_reply_3":
        message = get_followup_message(lead.first_name, followup_number=3)
    else:
        message = get_outreach_message(lead.first_name, tone=step["tone"], tenant_id=lead.tenant_id)

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
        # Increment follow-up count
        redis_service.client.incr(f"no_reply:count:{lead.id}")
        redis_service.client.expire(f"no_reply:count:{lead.id}", 86400 * 7)  # 7 days

        # Update lead
        lead.last_contacted_at = datetime.now(timezone.utc)
        db.commit()

        # Audit
        log_ai_action(
            tenant_id=tenant_id,
            action=f"no_reply_followup_{step['step']}",
            resource_type="lead",
            resource_id=lead_id,
            details={"step": step["step"], "message": message[:100]},
        )

    return {
        "success": result.get("success", False),
        "step": step["step"],
        "message": message[:50],
    }


def process_all_no_reply_leads(db: Session, tenant_id: str = None) -> Dict:
    """
    Process all leads that need no-reply follow-up.

    Returns:
        Dict with counts of processed/failed.
    """
    no_reply_leads = check_no_reply_leads(db, tenant_id)

    processed = 0
    failed = 0

    for entry in no_reply_leads:
        result = process_no_reply_followup(db, entry["lead"], entry["step"])
        if result["success"]:
            processed += 1
        else:
            failed += 1

    return {
        "total_checked": len(no_reply_leads),
        "processed": processed,
        "failed": failed,
    }


def reset_no_reply_counter(lead_id: str):
    """Reset the no-reply counter for a lead (e.g., after they reply)."""
    redis_service.client.delete(f"no_reply:count:{lead_id}")


def get_no_reply_status(lead_id: str) -> Dict:
    """Get the no-reply follow-up status for a lead."""
    count = redis_service.client.get(f"no_reply:count:{lead_id}") or 0
    count = int(count)

    return {
        "lead_id": lead_id,
        "followup_count": count,
        "max_followups": len(NO_REPLY_STEPS),
        "next_step": NO_REPLY_STEPS[count] if count < len(NO_REPLY_STEPS) else None,
    }
