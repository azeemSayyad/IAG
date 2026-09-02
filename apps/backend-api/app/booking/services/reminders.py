"""
Reminder Engine (Step 6.7)

Sends appointment reminders:
- 24 hours before
- 1 hour before
- 15 minutes before

Uses Redis queue for scheduling.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.agent import Agent
from app.ai.services.queue import enqueue_reminder
from app.ai.services.communication_provider import send_sms_to_lead
from app.core.audit import log_ai_action


# Reminder types and their timing
REMINDER_TYPES = {
    "24h": {"hours_before": 24, "message": "Reminder: You have an appointment tomorrow at {time}. Looking forward to speaking with you!"},
    "1h": {"hours_before": 1, "message": "Reminder: Your appointment is in 1 hour at {time}. See you soon!"},
    "15m": {"hours_before": 0.25, "message": "Your appointment starts in 15 minutes at {time}. Ready when you are!"},
}


def schedule_reminders(
    db: Session,
    appointment: Appointment,
) -> List[str]:
    """
    Schedule all reminders for an appointment.

    Returns:
        List of job IDs for scheduled reminders
    """
    job_ids = []

    for reminder_type, config in REMINDER_TYPES.items():
        job_id = enqueue_reminder(
            tenant_id=str(appointment.tenant_id),
            lead_id=str(appointment.lead_id),
            appointment_id=str(appointment.id),
            phone="",  # Will be populated when processing
            reminder_type=reminder_type,
            appointment_time=appointment.start_time.isoformat(),
        )
        job_ids.append(job_id)

    return job_ids


def send_reminder(
    db: Session,
    appointment: Appointment,
    reminder_type: str,
) -> Dict:
    """
    Send a reminder for an appointment.

    Returns:
        Dict with success status and details
    """
    # Get lead
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if not lead:
        return {"success": False, "error": "Lead not found"}

    # Check if reminder already sent
    if reminder_type == "24h" and appointment.reminder_24h_sent:
        return {"success": False, "error": "24h reminder already sent"}
    elif reminder_type == "1h" and appointment.reminder_1h_sent:
        return {"success": False, "error": "1h reminder already sent"}
    elif reminder_type == "15m" and appointment.reminder_15m_sent:
        return {"success": False, "error": "15m reminder already sent"}

    # Get reminder message
    config = REMINDER_TYPES.get(reminder_type)
    if not config:
        return {"success": False, "error": f"Unknown reminder type: {reminder_type}"}

    # Format time in the LEAD's local timezone (customer-facing display).
    from app.core.timezones import lead_zone
    start = appointment.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    local = start.astimezone(lead_zone(getattr(lead, "timezone", None)))
    display_time = local.strftime("%I:%M %p %Z").lstrip("0")
    message = config["message"].format(time=display_time)

    # Send SMS
    result = send_sms_to_lead(
        phone=lead.phone,
        message=message,
        tenant_id=str(appointment.tenant_id),
        lead_id=str(lead.id),
    )

    if result["success"]:
        # Mark reminder as sent
        if reminder_type == "24h":
            appointment.reminder_24h_sent = True
        elif reminder_type == "1h":
            appointment.reminder_1h_sent = True
        elif reminder_type == "15m":
            appointment.reminder_15m_sent = True

        db.commit()

        # Audit log
        log_ai_action(
            tenant_id=str(appointment.tenant_id),
            action=f"reminder_{reminder_type}_sent",
            resource_type="appointment",
            resource_id=str(appointment.id),
            details={"lead_id": str(lead.id), "message": message[:100]},
        )

    return result


def get_pending_reminders(
    db: Session,
    tenant_id: str = None,
) -> List[Dict]:
    """
    Get appointments that need reminders sent.
    """
    now = datetime.now(timezone.utc)

    query = db.query(Appointment).filter(
        Appointment.status == "confirmed",
    )

    if tenant_id:
        query = query.filter(Appointment.tenant_id == tenant_id)

    appointments = query.all()
    pending = []

    for apt in appointments:
        time_until = (apt.start_time - now).total_seconds() / 3600  # hours

        # 24h reminder (between 23-25 hours before)
        if not apt.reminder_24h_sent and 23 <= time_until <= 25:
            pending.append({
                "appointment_id": str(apt.id),
                "reminder_type": "24h",
                "time_until_hours": round(time_until, 1),
            })

        # 1h reminder (between 0.5-1.5 hours before)
        if not apt.reminder_1h_sent and 0.5 <= time_until <= 1.5:
            pending.append({
                "appointment_id": str(apt.id),
                "reminder_type": "1h",
                "time_until_hours": round(time_until, 1),
            })

        # 15m reminder (between 10-20 minutes before)
        if not apt.reminder_15m_sent and 10/60 <= time_until <= 20/60:
            pending.append({
                "appointment_id": str(apt.id),
                "reminder_type": "15m",
                "time_until_hours": round(time_until, 2),
            })

    return pending


def process_pending_reminders(db: Session) -> Dict:
    """
    Process all pending reminders.

    Returns:
        Dict with counts of sent/failed reminders
    """
    pending = get_pending_reminders(db)
    sent = 0
    failed = 0

    for reminder in pending:
        appointment = db.query(Appointment).filter(
            Appointment.id == reminder["appointment_id"]
        ).first()

        if appointment:
            # Queue-Only Mode: skip reminders while booking autopilot is paused.
            from app.core.sending import is_autopilot_paused
            if is_autopilot_paused(str(appointment.tenant_id)):
                continue
            result = send_reminder(db, appointment, reminder["reminder_type"])
            if result["success"]:
                sent += 1
            else:
                failed += 1

    return {
        "total_pending": len(pending),
        "sent": sent,
        "failed": failed,
    }
