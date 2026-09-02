"""
Missed Appointment Flow (Step 8.3)

Handles missed appointments:
1. Missed Appointment → Wait 30 min → AI Follow-up
2. No Reply within 24h → Offer Reschedule
3. Still No Reply (48h) → Final Reschedule Offer
4. No Reply → Move to Nurture Campaign
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.ai.services.communication_provider import send_sms_to_lead
from app.ai.services.humanizer import humanize_message
from app.ai.services.queue import enqueue_followup
from app.core.redis import redis_service
from app.core.audit import log_ai_action


# Missed appointment flow steps
MISSED_STEPS = [
    {
        "step": 1,
        "delay_minutes": 30,
        "message": "Hey {name}! We missed you today. Want to reschedule?",
        "description": "Initial follow-up (30 min)",
    },
    {
        "step": 2,
        "delay_hours": 24,
        "message": "Hi {name}! Your spot is still available. Want to book a new time?",
        "description": "Reschedule offer (24h)",
    },
    {
        "step": 3,
        "delay_hours": 48,
        "message": "Hi {name}, last chance to reschedule. Let me know!",
        "description": "Final reschedule offer (48h)",
    },
]


def check_missed_appointments(db: Session, tenant_id: str = None) -> List[Dict]:
    """
    Find appointments that were missed (no_show status).

    Returns:
        List of missed appointments that need follow-up.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=72)  # Don't follow up after 72 hours

    query = (
        db.query(Appointment)
        .filter(
            Appointment.status == "no_show",
            Appointment.start_time > cutoff,
            Appointment.start_time < now,
        )
    )

    if tenant_id:
        query = query.filter(Appointment.tenant_id == tenant_id)

    appointments = query.all()

    missed = []
    for apt in appointments:
        lead = db.query(Lead).filter(Lead.id == apt.lead_id, Lead.deleted_at.is_(None)).first()
        if not lead:
            continue

        # Check follow-up count
        followup_count = redis_service.client.get(f"missed:count:{apt.id}") or 0
        followup_count = int(followup_count)

        if followup_count < len(MISSED_STEPS):
            step = MISSED_STEPS[followup_count]
            time_since_missed = (now - apt.start_time).total_seconds() / 60

            # Check if enough time has passed
            delay_minutes = step.get("delay_minutes", 0) + step.get("delay_hours", 0) * 60
            if time_since_missed >= delay_minutes:
                missed.append({
                    "appointment": apt,
                    "lead": lead,
                    "step": step,
                    "followup_count": followup_count,
                    "minutes_since_missed": round(time_since_missed),
                })

    return missed


def process_missed_appointment_followup(
    db: Session,
    appointment: Appointment,
    lead: Lead,
    step: Dict,
) -> Dict:
    """
    Process a missed appointment follow-up.

    Returns:
        Dict with success status and details.
    """
    tenant_id = str(appointment.tenant_id)
    lead_id = str(lead.id)

    # Queue-Only Mode: no automated missed-appointment follow-ups while booking
    # autopilot is paused.
    from app.core.sending import is_autopilot_paused
    if is_autopilot_paused(tenant_id):
        return {"success": False, "skipped": "autopilot_paused", "lead_id": lead_id}

    # Format message
    message = step["message"].format(name=lead.first_name)
    message = humanize_message(message)

    # Send SMS
    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
    )

    if result.get("success"):
        # Increment follow-up count
        redis_service.client.incr(f"missed:count:{appointment.id}")
        redis_service.client.expire(f"missed:count:{appointment.id}", 86400 * 7)  # 7 days

        # Audit
        log_ai_action(
            tenant_id=tenant_id,
            action=f"missed_followup_{step['step']}",
            resource_type="appointment",
            resource_id=str(appointment.id),
            details={"step": step["step"], "lead_id": lead_id},
        )

    return {
        "success": result.get("success", False),
        "step": step["step"],
        "message": message[:50],
    }


def process_all_missed_appointments(db: Session, tenant_id: str = None) -> Dict:
    """
    Process all missed appointments that need follow-up.

    Returns:
        Dict with counts of processed/failed.
    """
    missed = check_missed_appointments(db, tenant_id)

    processed = 0
    failed = 0

    for entry in missed:
        result = process_missed_appointment_followup(
            db,
            entry["appointment"],
            entry["lead"],
            entry["step"],
        )
        if result["success"]:
            processed += 1
        else:
            failed += 1

    return {
        "total_checked": len(missed),
        "processed": processed,
        "failed": failed,
    }


def mark_as_no_show(db: Session, appointment_id: UUID, tenant_id: str) -> Dict:
    """
    Mark an appointment as no-show and trigger follow-up flow.
    """
    appointment = (
        db.query(Appointment)
        .filter(
            Appointment.id == appointment_id,
            Appointment.tenant_id == tenant_id,
        )
        .first()
    )

    if not appointment:
        return {"success": False, "error": "Appointment not found"}

    if appointment.status != "confirmed":
        return {"success": False, "error": f"Cannot mark {appointment.status} as no_show"}

    # Update status
    appointment.status = "no_show"
    appointment.updated_at = datetime.now(timezone.utc)

    # Update lead
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        lead.status = "no_show"

    db.commit()

    # Audit
    log_ai_action(
        tenant_id=tenant_id,
        action="appointment_no_show",
        resource_type="appointment",
        resource_id=str(appointment.id),
    )

    return {"success": True, "appointment_id": str(appointment.id)}


def get_missed_appointment_status(appointment_id: str) -> Dict:
    """Get the missed appointment follow-up status."""
    count = redis_service.client.get(f"missed:count:{appointment_id}") or 0
    count = int(count)

    return {
        "appointment_id": appointment_id,
        "followup_count": count,
        "max_followups": len(MISSED_STEPS),
        "next_step": MISSED_STEPS[count] if count < len(MISSED_STEPS) else None,
    }
