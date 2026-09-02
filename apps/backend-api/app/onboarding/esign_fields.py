"""Map collected onboarding form data -> SignWell template_fields.

The api_id keys here MUST match exactly the field names placed in the SignWell
templates (set during template setup). Values are plain strings/booleans;
SignWell fills text fields and ticks checkboxes accordingly. Signature and
"Date Signed" fields are completed by the agent / SignWell and are NOT sent.
"""
from __future__ import annotations

from typing import Optional


def _field(api_id: str, value) -> dict:
    return {"api_id": api_id, "value": value}


def _ymd(raw: Optional[str]) -> str:
    """Normalize a date to plain ISO YYYY-MM-DD. The SignWell client later
    upgrades it to a full ISO8601 datetime only for fields that are actually a
    `date` type (text fields keep YYYY-MM-DD). Form sends ISO; MM/DD/YYYY handled
    defensively."""
    if not raw:
        return ""
    s = str(raw).strip()
    if "-" in s and len(s) >= 10 and len(s.split("-")[0]) == 4:
        return s[:10]  # already yyyy-mm-dd
    if "/" in s:       # mm/dd/yyyy -> yyyy-mm-dd
        parts = s.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            m, d, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return s


def _full_name(d: dict) -> str:
    parts = [d.get("first_name"), d.get("middle_name"), d.get("last_name")]
    return " ".join(p.strip() for p in parts if p and str(p).strip())


def _primary_id(d: dict) -> dict:
    docs = d.get("identity_documents") or []
    return docs[0] if docs and isinstance(docs[0], dict) else {}


def _city_state_zip(city: Optional[str], state: Optional[str], zip_code: Optional[str]) -> str:
    left = ", ".join(p.strip() for p in (city, state) if p and str(p).strip())
    return f"{left} {zip_code}".strip() if zip_code else left


def build_template_fields(doc_type: str, data: dict) -> list[dict]:
    """Return the SignWell template_fields list for the given document type."""
    if doc_type == "w9":
        return _w9_fields(data)
    return _agreement_fields(data)


def _agreement_fields(d: dict) -> list[dict]:
    pid = _primary_id(d)
    bank = d.get("bank_info") or {}
    ec = d.get("emergency_contact") or {}
    full = _full_name(d)
    gender = (d.get("gender") or "").strip().lower()
    marital = (d.get("marital_status") or "").strip().lower()
    id_type = (pid.get("type") or "drivers_license").strip().lower()

    fields = [
        # Personal
        _field("first_name", d.get("first_name") or ""),
        _field("middle_name", d.get("middle_name") or ""),
        _field("last_name", d.get("last_name") or ""),
        _field("email", d.get("email") or ""),
        _field("phone", d.get("phone") or ""),
        _field("dob", _ymd(d.get("date_of_birth"))),
        _field("ssn", d.get("ssn") or ""),
        _field("drivers_license", d.get("drivers_license_number") or ""),
        # Contact
        _field("street_address", d.get("street_address") or ""),
        _field("city", d.get("city") or ""),
        _field("state", d.get("state") or ""),
        _field("zip", d.get("zip") or ""),
        # Identity (primary document)
        _field("id_number", pid.get("id_number") or ""),
        _field("id_issuing_state", pid.get("issuing_state") or ""),
        _field("id_issue_date", _ymd(pid.get("issue_date"))),
        _field("id_expiration_date", _ymd(pid.get("expiration_date"))),
        _field("id_copy_attached", True),
        # Gender checkboxes
        _field("gender_male", gender == "male"),
        _field("gender_female", gender == "female"),
        _field("gender_other", gender not in ("male", "female") and bool(gender)),
        # Marital checkboxes
        _field("marital_single", marital == "single"),
        _field("marital_married", marital == "married"),
        _field("marital_other", marital not in ("single", "married") and bool(marital)),
        # ID-type checkboxes
        _field("idtype_dl", id_type == "drivers_license"),
        _field("idtype_stateid", id_type == "state_id"),
        _field("idtype_passport", id_type == "passport"),
        # Bank
        _field("bank_name", bank.get("bank_name") or ""),
        _field("account_holder_name", full),
        _field("routing_number", bank.get("routing_number") or ""),
        _field("account_number", bank.get("account_number") or ""),
        _field("account_type", bank.get("account_type") or ""),
        _field("branch_location", bank.get("branch_location") or ""),
        # Emergency contact
        _field("ec_name", ec.get("contact_name") or ""),
        _field("ec_relationship", ec.get("relationship") or ""),
        _field("ec_phone", ec.get("phone") or ""),
        _field("ec_email", ec.get("email") or ""),
        _field("ec_street_address", ec.get("street_address") or ""),
        _field("ec_city", ec.get("city") or ""),
        _field("ec_state", ec.get("state") or ""),
        # Signature block (printed name only; signature + date done by agent)
        _field("printed_name", full),
    ]
    return fields


def _w9_fields(d: dict) -> list[dict]:
    full = _full_name(d)
    return [
        _field("w9_name", full),
        _field("w9_class_individual", True),  # agents are independent contractors
        _field("w9_address", d.get("street_address") or ""),
        _field("w9_city_state_zip", _city_state_zip(d.get("city"), d.get("state"), d.get("zip"))),
        _field("w9_ssn", d.get("ssn") or ""),
    ]
