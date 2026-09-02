"""
Post-Call Automation (Step 7.5)

Triggers different workflows based on disposition:
- won → onboarding flow
- lost → nurture flow
- follow_up → schedule follow-up
- no_answer → retry flow
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.ai.services.prompts import get_followup_message
from app.ai.services.communication_provider import send_sms_to_lead
from app.ai.services.queue import enqueue_followup
from app.core.audit import log_ai_action


def process_post_call(
    db: Session,
    appointment: Appointment,
    disposition: str,
) -> Dict:
    """
    Process post-call automation based on disposition.

    Returns:
        Dict with triggered workflow details.
    """
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if not lead:
        return {"success": False, "error": "Lead not found"}

    tenant_id = str(appointment.tenant_id)
    lead_id = str(lead.id)

    if disposition == "won":
        return _process_won(db, tenant_id, lead_id, lead, appointment)
    elif disposition == "lost":
        return _process_lost(db, tenant_id, lead_id, lead)
    elif disposition == "follow_up":
        return _process_follow_up(db, tenant_id, lead_id, lead, appointment)
    elif disposition == "no_answer":
        return _process_no_answer(db, tenant_id, lead_id, lead)
    else:
        return {"success": False, "error": f"Unknown disposition: {disposition}"}


def _process_won(
    db: Session,
    tenant_id: str,
    lead_id: str,
    lead: Lead,
    appointment: Appointment,
) -> Dict:
    """
    Process won disposition:
    1. Send thank you message
    2. Trigger onboarding flow
    """
    # Send thank you message
    message = f"Thank you, {lead.first_name}! We're excited to have you. Our team will reach out with next steps."

    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="post_call_won",
        resource_type="lead",
        resource_id=lead_id,
        details={"appointment_id": str(appointment.id)},
    )

    return {
        "success": True,
        "workflow": "onboarding",
        "message_sent": result.get("success", False),
    }


def _process_lost(
    db: Session,
    tenant_id: str,
    lead_id: str,
    lead: Lead,
) -> Dict:
    """
    Process lost disposition:
    1. Move to nurture campaign
    2. Schedule long-term follow-up
    """
    # Schedule nurture follow-up (7 days)
    enqueue_followup(
        tenant_id=tenant_id,
        lead_id=lead_id,
        phone=lead.phone,
        first_name=lead.first_name,
        followup_number=1,
        delay_hours=168,  # 7 days
    )

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="post_call_lost",
        resource_type="lead",
        resource_id=lead_id,
        details={"workflow": "nurture"},
    )

    return {
        "success": True,
        "workflow": "nurture",
        "followup_scheduled": True,
    }


def _process_follow_up(
    db: Session,
    tenant_id: str,
    lead_id: str,
    lead: Lead,
    appointment: Appointment,
) -> Dict:
    """
    Process follow_up disposition:
    1. Send follow-up message
    2. Schedule follow-up call
    """
    # Send follow-up message
    message = get_followup_message(lead.first_name, followup_number=1)

    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    # Schedule follow-up (24 hours)
    enqueue_followup(
        tenant_id=tenant_id,
        lead_id=lead_id,
        phone=lead.phone,
        first_name=lead.first_name,
        followup_number=1,
        delay_hours=24,
    )

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="post_call_followup",
        resource_type="lead",
        resource_id=lead_id,
        details={"appointment_id": str(appointment.id)},
    )

    return {
        "success": True,
        "workflow": "schedule_followup",
        "message_sent": result.get("success", False),
        "followup_scheduled": True,
    }


def _process_no_answer(
    db: Session,
    tenant_id: str,
    lead_id: str,
    lead: Lead,
) -> Dict:
    """
    Process no_answer disposition:
    1. Send missed call message
    2. Schedule retry
    """
    # Send missed call message
    message = f"Hi {lead.first_name}, we tried calling but couldn't reach you. Would you like to reschedule?"

    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    # Schedule retry (1 hour)
    enqueue_followup(
        tenant_id=tenant_id,
        lead_id=lead_id,
        phone=lead.phone,
        first_name=lead.first_name,
        followup_number=1,
        delay_hours=1,
    )

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="post_call_no_answer",
        resource_type="lead",
        resource_id=lead_id,
        details={"workflow": "retry"},
    )

    return {
        "success": True,
        "workflow": "retry",
        "message_sent": result.get("success", False),
        "retry_scheduled": True,
    }
