"""
Lead Deduplication Engine (Step 3.3)

Prevents:
- Duplicate leads (by phone or email)
- Duplicate outreach (by lead_id + campaign_id)
- Duplicate appointments (by lead_id + time range)
"""

from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.appointment import Appointment


def find_duplicate_lead(
    db: Session,
    tenant_id: str,
    phone: str,
    email: Optional[str] = None,
) -> Optional[Lead]:
    """
    Find an existing lead by phone or email within the same tenant.

    Returns the existing Lead if found, None otherwise.
    """
    # Check phone first (primary identifier)
    existing = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.phone == phone,
            Lead.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        return existing

    # Check email if provided
    if email:
        existing = (
            db.query(Lead)
            .filter(
                Lead.tenant_id == tenant_id,
                Lead.email == email,
                Lead.deleted_at.is_(None),
            )
            .first()
        )
        if existing:
            return existing

    return None


def merge_lead_data(existing: Lead, new_data: dict) -> dict:
    """
    Merge new lead data into existing lead.
    New data takes precedence for non-empty values.
    Empty values in new data are ignored.
    """
    updates = {}
    for key, value in new_data.items():
        if key in ("phone", "email", "first_name", "last_name"):
            # Don't overwrite identity fields
            continue
        if value is not None and value != "" and value != []:
            updates[key] = value

    # Update source to most recent
    if new_data.get("source"):
        updates["source"] = new_data["source"]

    return updates


def check_duplicate_outreach(
    db: Session,
    tenant_id: str,
    lead_id: UUID,
    campaign_id: Optional[UUID] = None,
) -> bool:
    """
    Check if a lead has already been contacted for a campaign.
    Prevents duplicate outreach.
    """
    from app.models.conversation import Conversation

    query = db.query(Conversation).filter(
        Conversation.tenant_id == tenant_id,
        Conversation.lead_id == lead_id,
    )

    if campaign_id:
        # Check by campaign in ai_context
        conversations = query.all()
        for conv in conversations:
            if conv.ai_context and conv.ai_context.get("campaign_id") == str(campaign_id):
                return True
        return False

    # If no campaign specified, check if any active conversation exists
    return query.filter(Conversation.status.in_(["active", "initiated", "booking"])).first() is not None


def check_duplicate_appointment(
    db: Session,
    tenant_id: str,
    lead_id: UUID,
    start_time,
    end_time,
) -> bool:
    """
    Check if a lead already has an appointment in the given time range.
    Prevents double-booking the same lead.
    """
    existing = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.lead_id == lead_id,
            Appointment.status.in_(["pending", "confirmed"]),
            Appointment.start_time < end_time,
            Appointment.end_time > start_time,
        )
        .first()
    )
    return existing is not None
