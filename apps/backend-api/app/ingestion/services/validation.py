"""
Lead Validation Engine (Step 3.2)

Validates:
- Required fields present
- Email format valid
- Phone format valid
- Data types correct
"""

import re
from typing import Dict, List, Optional, Tuple


# Phone regex: supports US formats like (555) 123-4567, 555-123-4567, 5551234567, +15551234567
PHONE_REGEX = re.compile(r"^\+?1?\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$")

# Email regex: standard email validation
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class ValidationResult:
    def __init__(self):
        self.is_valid: bool = True
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def add_error(self, field: str, message: str):
        self.is_valid = False
        self.errors.append(f"{field}: {message}")

    def add_warning(self, field: str, message: str):
        self.warnings.append(f"{field}: {message}")

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_phone(phone: str) -> bool:
    """Validate US phone number format."""
    if not phone:
        return False
    cleaned = re.sub(r"[\s\-\(\)\.]", "", phone)
    return bool(PHONE_REGEX.match(phone)) or (cleaned.isdigit() and len(cleaned) >= 10)


def normalize_phone(phone: str) -> str:
    """Normalize phone number to digits only."""
    cleaned = re.sub(r"[^\d]", "", phone)
    if len(cleaned) == 10:
        return f"+1{cleaned}"
    elif len(cleaned) == 11 and cleaned.startswith("1"):
        return f"+{cleaned}"
    return phone


def phone_match_forms(phone: str) -> List[str]:
    """Every common stored representation of a US phone number, for
    format-agnostic inbound matching. Leads are uploaded from messy CSVs in
    assorted formats (and many have a NULL phone_normalized), so an exact string
    compare misses real replies — especially ones that arrive days later."""
    if not phone:
        return []
    raw = phone.strip()
    digits = re.sub(r"[^\d]", "", phone)
    nat = digits[-10:] if len(digits) >= 10 else digits
    forms = {raw}
    if len(nat) == 10:
        forms.update({
            nat, f"1{nat}", f"+1{nat}", f"+{nat}",
            f"({nat[0:3]}) {nat[3:6]}-{nat[6:10]}",
            f"{nat[0:3]}-{nat[3:6]}-{nat[6:10]}",
            f"{nat[0:3]}.{nat[3:6]}.{nat[6:10]}",
        })
    elif digits:
        forms.update({digits, f"+{digits}"})
    return [f for f in forms if f]


def validate_email(email: str) -> bool:
    """Validate email format."""
    if not email:
        return False
    return bool(EMAIL_REGEX.match(email.strip().lower()))


def normalize_email(email: str) -> str:
    """Normalize email to lowercase."""
    return email.strip().lower()


def validate_lead_row(row: dict, row_number: int = 0) -> ValidationResult:
    """
    Validate a single lead row.

    Required fields: first_name, last_name, phone
    Optional fields: email, source, state, city, zip_code, tags

    Returns ValidationResult with errors and warnings.
    """
    result = ValidationResult()

    # Required fields
    if not row.get("first_name", "").strip():
        result.add_error("first_name", "First name is required")

    if not row.get("last_name", "").strip():
        result.add_error("last_name", "Last name is required")

    if not row.get("phone", "").strip():
        result.add_error("phone", "Phone number is required")
    elif not validate_phone(row["phone"]):
        result.add_error("phone", f"Invalid phone format: {row['phone']}")

    # Optional field validation
    if row.get("email") and not validate_email(row["email"]):
        result.add_error("email", f"Invalid email format: {row['email']}")

    if row.get("zip_code") and not re.match(r"^\d{5}(-\d{4})?$", row["zip_code"].strip()):
        result.add_warning("zip_code", f"Non-standard zip code: {row['zip_code']}")

    if row.get("lead_score") is not None:
        try:
            score = int(row["lead_score"])
            if score < 0 or score > 100:
                result.add_warning("lead_score", f"Score {score} outside 0-100 range")
        except (ValueError, TypeError):
            result.add_error("lead_score", f"Invalid score: {row['lead_score']}")

    return result


# Hardcoded CSV header aliases (NO AI / NO ML — a plain lookup table) so an upload
# isn't locked to the exact "first_name,last_name,state,phone" spelling. Keys are the
# header AFTER _header_token() (lowercased, every run of non-alphanumerics collapsed to
# a single "_"), so "First Name" / "first-name" -> "first_name" and "FirstName" /
# "Firstname" -> "firstname" both resolve here. Unknown headers pass through unchanged,
# so optional/extra columns (source, tags, lead_score, campaign_id, …) are preserved.
_HEADER_ALIASES = {
    # -> first_name
    "first_name": "first_name", "firstname": "first_name", "first": "first_name",
    "fname": "first_name", "f_name": "first_name", "given_name": "first_name",
    "givenname": "first_name", "first_nm": "first_name",
    # -> last_name
    "last_name": "last_name", "lastname": "last_name", "last": "last_name",
    "lname": "last_name", "l_name": "last_name", "surname": "last_name",
    "family_name": "last_name", "familyname": "last_name", "last_nm": "last_name",
    # -> phone
    "phone": "phone", "phone_number": "phone", "phonenumber": "phone",
    "phone_no": "phone", "phoneno": "phone", "phone_num": "phone", "phone1": "phone",
    "mobile": "phone", "mobile_number": "phone", "mobilenumber": "phone",
    "mobile_phone": "phone", "cell": "phone", "cell_phone": "phone", "cellphone": "phone",
    "telephone": "phone", "tel": "phone", "primary_phone": "phone",
    "contact_number": "phone", "contact_no": "phone", "contactnumber": "phone",
    # -> state
    "state": "state", "st": "state", "state_code": "state", "statecode": "state",
    "state_name": "state", "statename": "state", "province": "state", "region": "state",
    # -> email
    "email": "email", "email_address": "email", "emailaddress": "email",
    "e_mail": "email", "mail": "email",
    # -> city
    "city": "city", "town": "city",
    # -> zip_code
    "zip_code": "zip_code", "zip": "zip_code", "zipcode": "zip_code",
    "postal_code": "zip_code", "postalcode": "zip_code", "postal": "zip_code",
    "postcode": "zip_code", "zip5": "zip_code",
}


def _header_token(h: str) -> str:
    """Lowercase a header and collapse every run of non-alphanumerics to a single '_'
    (and drop a leading BOM). 'First Name'/'first-name' -> 'first_name'; 'FirstName' ->
    'firstname'. This is the key used to look up _HEADER_ALIASES."""
    h = str(h or "").lstrip("﻿").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def canonical_header(h: str) -> str:
    """Map a CSV header to the importer's canonical column name via the hardcoded alias
    table. Unknown headers pass through as their normalized token (so extra columns are
    kept). Strictly more permissive than the old lower/space normalization."""
    tok = _header_token(h)
    return _HEADER_ALIASES.get(tok, tok)


def validate_csv_headers(headers: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate that CSV has required headers.

    Required: first_name, last_name, phone
    Optional: email, source, state, city, zip_code, tags, lead_score, campaign_id

    Header spelling is flexible via canonical_header() — e.g. firstname / FirstName /
    fname all satisfy first_name; mobile / cell / phone_number all satisfy phone.

    Returns (is_valid, missing_headers).
    """
    required = {"first_name", "last_name", "phone"}
    normalized = {canonical_header(h) for h in headers}
    missing = required - normalized

    return len(missing) == 0, list(missing)


def normalize_row(row: dict) -> dict:
    """Normalize a lead row: canonicalize header keys (alias-aware), trim strings,
    normalize phone/email."""
    normalized = {}
    for key, value in row.items():
        key = canonical_header(key)
        if isinstance(value, str):
            value = value.strip()
        normalized[key] = value

    if "phone" in normalized:
        normalized["phone"] = normalize_phone(normalized["phone"])

    if "email" in normalized and normalized["email"]:
        normalized["email"] = normalize_email(normalized["email"])

    return normalized
