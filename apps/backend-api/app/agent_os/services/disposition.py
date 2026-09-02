"""
Disposition System (Step 7.4)

Call disposition statuses:
- won — Deal closed, policy sold
- lost — Customer declined, not interested
- follow_up — Needs more time, call back later
- no_answer — Customer didn't answer

Each disposition triggers different post-call workflows.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.lead import Lead
from app.models.conversation import Conversation
from app.core.audit import log_ai_action


# Disposition definitions
DISPOSITIONS = {
    "won": {
        "label": "Won",
        "description": "Deal closed, policy sold",
        "color": "#10b981",
        "next_action": "onboarding",
        "lead_status": "completed",
    },
    "lost": {
        "label": "Lost",
        "description": "Customer declined, not interested",
        "color": "#ef4444",
        "next_action": "nurture",
        "lead_status": "unqualified",
    },
    "follow_up": {
        "label": "Follow Up",
        "description": "Needs more time, call back later",
        "color": "#f59e0b",
        "next_action": "schedule_followup",
        "lead_status": "nurture",
    },
    "no_answer": {
        "label": "No Answer",
        "description": "Customer didn't answer",
        "color": "#6b7280",
        "next_action": "retry",
        "lead_status": "no_show",
    },
}


def get_dispositions() -> List[Dict]:
    """
    Get all available dispositions.
    """
    return [
        {"key": key, **value}
        for key, value in DISPOSITIONS.items()
    ]


def set_disposition(
    db: Session,
    appointment_id: UUID,
    tenant_id: str,
    disposition: str,
    notes: str = None,
    call_duration_seconds: int = None,
) -> Dict:
    """
    Set disposition for a completed call.

    Args:
        appointment_id: Appointment ID
        tenant_id: Tenant ID
        disposition: Disposition key (won, lost, follow_up, no_answer)
        notes: Agent notes about the call
        call_duration_seconds: Duration of the call in seconds

    Returns:
        Dict with success status and next action
    """
    if disposition not in DISPOSITIONS:
        return {"success": False, "error": f"Invalid disposition: {disposition}"}

    # Get appointment
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

    if appointment.status not in ("confirmed", "pending"):
        return {"success": False, "error": f"Cannot set disposition for {appointment.status} appointment"}

    # Update appointment
    appointment.status = "completed"
    appointment.disposition = disposition
    appointment.notes = notes
    appointment.call_duration_seconds = call_duration_seconds
    appointment.updated_at = datetime.now(timezone.utc)

    # Update lead status
    lead = db.query(Lead).filter(Lead.id == appointment.lead_id).first()
    if lead:
        lead.status = DISPOSITIONS[disposition]["lead_status"]

    # Update conversation
    conversation = db.query(Conversation).filter(
        Conversation.lead_id == appointment.lead_id
    ).first()
    if conversation:
        conversation.status = "closed"

    db.commit()

    # Audit log
    log_ai_action(
        tenant_id=tenant_id,
        action="disposition_set",
        resource_type="appointment",
        resource_id=str(appointment_id),
        details={
            "disposition": disposition,
            "notes": notes[:200] if notes else None,
            "call_duration": call_duration_seconds,
        },
    )

    return {
        "success": True,
        "disposition": disposition,
        "next_action": DISPOSITIONS[disposition]["next_action"],
        "lead_status": DISPOSITIONS[disposition]["lead_status"],
    }


def get_disposition_stats(
    db: Session,
    tenant_id: str,
    agent_id: UUID = None,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict:
    """
    Get disposition statistics.
    """
    query = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.status == "completed",
            Appointment.disposition.isnot(None),
        )
    )

    if agent_id:
        query = query.filter(Appointment.agent_id == agent_id)
    if start_date:
        query = query.filter(Appointment.start_time >= start_date)
    if end_date:
        query = query.filter(Appointment.start_time < end_date)

    appointments = query.all()

    stats = {key: 0 for key in DISPOSITIONS}
    total = len(appointments)

    for apt in appointments:
        if apt.disposition in stats:
            stats[apt.disposition] += 1

    # Calculate rates
    rates = {}
    for key, count in stats.items():
        rates[key] = round(count / total * 100, 1) if total > 0 else 0

    return {
        "total": total,
        "counts": stats,
        "rates": rates,
    }
