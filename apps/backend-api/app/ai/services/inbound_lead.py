"""Inbound-capture helpers.

Make EVERY inbound SMS surface in the SMS Lead Pool:

1. ``find_lead_for_inbound`` — match an inbound reply to an existing lead with
   format-agnostic phone matching. Many leads were uploaded from messy CSVs in
   assorted formats (and have a NULL ``phone_normalized``), so the old exact
   ``phone ==`` compare silently dropped real replies — especially ones that
   arrive a day or more later. We match every common representation, then fall
   back to a digits-only national-number compare that ignores formatting.

2. ``create_inbound_lead`` — when the sender is genuinely unknown (never
   uploaded), auto-create a minimal lead so the message still lands in the Lead
   Pool. This is INBOUND CAPTURE ONLY: it never sends an SMS. A first_template
   (the only send the lockdown allows) is enqueued solely by the CSV/campaign/
   drip paths, never by creating a Lead row, and the lead is created already in
   ``replied`` status with no campaign_id / pacing_status so no drip picks it up.
"""

import logging
import re
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.ingestion.services.validation import normalize_phone, phone_match_forms

logger = logging.getLogger(__name__)


def find_lead_for_inbound(db: Session, from_number: str) -> Optional[Lead]:
    """Find the lead for an inbound reply, tolerant of phone-format differences."""
    if not from_number:
        return None
    forms = phone_match_forms(from_number)
    # Fast path: indexed match against the common stored representations.
    lead = (
        db.query(Lead)
        .filter(
            Lead.deleted_at.is_(None),
            or_(Lead.phone.in_(forms), Lead.phone_normalized.in_(forms)),
        )
        .order_by(Lead.created_at.desc())
        .first()
    )
    if lead:
        return lead
    # Robust path: compare the last 10 digits (national number) of the stored
    # phone, ignoring all formatting. Catches anything the fast path missed.
    digits = re.sub(r"\D", "", from_number)
    nat = digits[-10:]
    if len(nat) != 10:
        return None
    return (
        db.query(Lead)
        .filter(
            Lead.deleted_at.is_(None),
            func.right(func.regexp_replace(Lead.phone, r"[^0-9]", "", "g"), 10) == nat,
        )
        .order_by(Lead.created_at.desc())
        .first()
    )


def resolve_inbound_tenant(db: Session, to_did: Optional[str] = None) -> Optional[str]:
    """Pick the tenant for an inbound from an unknown number. One Sinch account =
    one inbox = in practice one tenant. Prefer an explicitly configured default
    (``INBOUND_DEFAULT_TENANT_ID``); otherwise the tenant that owns the most
    leads (the primary account)."""
    try:
        from app.core.config import settings
        configured = getattr(settings, "INBOUND_DEFAULT_TENANT_ID", None)
        if configured:
            return str(configured)
    except Exception:
        pass
    row = (
        db.query(Lead.tenant_id, func.count(Lead.id).label("c"))
        .filter(Lead.deleted_at.is_(None))
        .group_by(Lead.tenant_id)
        .order_by(func.count(Lead.id).desc())
        .first()
    )
    return str(row[0]) if row else None


def create_inbound_lead(db: Session, from_number: str, to_did: Optional[str] = None) -> Optional[Lead]:
    """Auto-create a minimal lead for an inbound reply from an unknown number so
    it surfaces in the Lead Pool. Inbound capture only — sends nothing."""
    if not from_number:
        return None
    tenant_id = resolve_inbound_tenant(db, to_did)
    if not tenant_id:
        logger.warning("inbound auto-create skipped: no tenant resolvable for %s", from_number)
        return None
    lead = Lead(
        tenant_id=tenant_id,
        source="inbound_sms",
        source_metadata={"auto_created": True, "via": "inbound_sms", "to_did": to_did},
        first_name="SMS",
        last_name="Lead",
        phone=from_number.strip(),
        phone_normalized=normalize_phone(from_number),
        # Created already-"replied" with no campaign_id / pacing_status: a bare
        # Lead row never enqueues a first_template, and no drip picks it up.
        status="replied",
    )
    db.add(lead)
    db.flush()
    logger.info("auto-created inbound lead %s (tenant %s) for %s", lead.id, tenant_id, from_number)
    return lead


def get_or_create_lead_for_inbound(
    db: Session, from_number: str, to_did: Optional[str] = None
) -> Optional[Lead]:
    """Match an inbound reply to a lead, or auto-create one when the sender is
    unknown, so EVERY inbound message lands in the Lead Pool."""
    return find_lead_for_inbound(db, from_number) or create_inbound_lead(db, from_number, to_did)
