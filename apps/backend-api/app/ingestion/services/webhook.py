"""
Webhook Import Service (Step 3.1)

Handles lead imports from external CRM webhooks.
"""

from typing import Dict, Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.ingestion.services.validation import validate_lead_row, normalize_row
from app.ingestion.services.deduplication import find_duplicate_lead, merge_lead_data
from app.ingestion.services.events import on_lead_created


# Supported webhook sources and their field mappings
WEBHOOK_FIELD_MAPPINGS = {
    "hubspot": {
        "first_name": "properties.firstname",
        "last_name": "properties.lastname",
        "phone": "properties.phone",
        "email": "properties.email",
        "state": "properties.state",
        "city": "properties.city",
    },
    "salesforce": {
        "first_name": "FirstName",
        "last_name": "LastName",
        "phone": "Phone",
        "email": "Email",
        "state": "State",
        "city": "City",
    },
    "zapier": {
        "first_name": "first_name",
        "last_name": "last_name",
        "phone": "phone",
        "email": "email",
        "state": "state",
        "city": "city",
    },
    "generic": {
        "first_name": "first_name",
        "last_name": "last_name",
        "phone": "phone",
        "email": "email",
        "state": "state",
        "city": "city",
    },
}


def extract_nested_value(data: dict, path: str) -> Optional[str]:
    """Extract a value from nested dict using dot notation."""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current if isinstance(current, str) else None


def map_webhook_data(payload: dict, source: str = "generic") -> dict:
    """
    Map webhook payload to standard lead fields.

    Supports: hubspot, salesforce, zapier, generic
    """
    mapping = WEBHOOK_FIELD_MAPPINGS.get(source, WEBHOOK_FIELD_MAPPINGS["generic"])

    lead_data = {}
    for field, path in mapping.items():
        value = extract_nested_value(payload, path)
        if value:
            lead_data[field] = value

    # Copy any unmapped fields to custom_fields
    mapped_paths = set(mapping.values())
    custom_fields = {}
    for key, value in payload.items():
        if key not in mapped_paths and isinstance(value, (str, int, float, bool)):
            custom_fields[key] = value

    lead_data["custom_fields"] = custom_fields
    return lead_data


def import_lead_from_webhook(
    db: Session,
    tenant_id: str,
    payload: dict,
    source: str = "generic",
    dedup_mode: str = "merge",
) -> Dict[str, Any]:
    """
    Import a single lead from a webhook payload.

    Args:
        db: Database session
        tenant_id: Tenant ID
        payload: Raw webhook payload
        source: Webhook source type
        dedup_mode: "skip" or "merge"

    Returns:
        Dict with lead_id, status, and any errors.
    """
    # Map webhook data to standard fields
    lead_data = map_webhook_data(payload, source)
    lead_data["source"] = source

    # Normalize
    lead_data = normalize_row(lead_data)

    # Validate
    validation = validate_lead_row(lead_data)
    if not validation.is_valid:
        return {
            "status": "error",
            "errors": validation.errors,
        }

    # Check for duplicates
    existing = find_duplicate_lead(
        db=db,
        tenant_id=tenant_id,
        phone=lead_data["phone"],
        email=lead_data.get("email"),
    )

    if existing:
        if dedup_mode == "merge":
            updates = merge_lead_data(existing, lead_data)
            for key, value in updates.items():
                setattr(existing, key, value)
            db.commit()
            return {
                "status": "merged",
                "lead_id": str(existing.id),
            }
        else:
            return {
                "status": "skipped",
                "reason": "duplicate",
                "existing_lead_id": str(existing.id),
            }

    # Create new lead
    lead = Lead(
        tenant_id=tenant_id,
        source=lead_data.get("source", source),
        first_name=lead_data["first_name"],
        last_name=lead_data["last_name"],
        phone=lead_data["phone"],
        email=lead_data.get("email"),
        state=lead_data.get("state"),
        city=lead_data.get("city"),
        zip_code=lead_data.get("zip_code"),
        custom_fields=lead_data.get("custom_fields", {}),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Trigger lead created event
    on_lead_created(db, lead)

    return {
        "status": "created",
        "lead_id": str(lead.id),
    }
