"""Hardcoded CSV header aliases — an upload isn't locked to the exact
'first_name,last_name,state,phone' spelling. Plain lookup table, no AI/ML.

Guards the campaign + bulk import paths, which both flow through validate_csv_headers /
normalize_row in app.ingestion.services.validation.
"""
import pytest

from app.ingestion.services.validation import (
    canonical_header,
    normalize_row,
    validate_csv_headers,
)


@pytest.mark.parametrize("variant", [
    "first_name", "First Name", "firstname", "Firstname", "FirstName",
    "first-name", "FIRST_NAME", "fname", "First", "given_name",
])
def test_first_name_variants_canonicalize(variant):
    assert canonical_header(variant) == "first_name"


@pytest.mark.parametrize("variant", [
    "last_name", "Last Name", "lastname", "Lastname", "LastName",
    "last-name", "lname", "surname", "family_name",
])
def test_last_name_variants_canonicalize(variant):
    assert canonical_header(variant) == "last_name"


@pytest.mark.parametrize("variant", [
    "phone", "Phone", "phone_number", "PhoneNumber", "phone number",
    "mobile", "Mobile", "cell", "cell_phone", "telephone", "contact_number",
])
def test_phone_variants_canonicalize(variant):
    assert canonical_header(variant) == "phone"


@pytest.mark.parametrize("variant", ["state", "State", "ST", "state_code", "province"])
def test_state_variants_canonicalize(variant):
    assert canonical_header(variant) == "state"


def test_unknown_header_passes_through_normalized():
    # Extra/optional columns are preserved (lowercased + underscored), not dropped.
    assert canonical_header("Lead Score") == "lead_score"
    assert canonical_header("source") == "source"
    assert canonical_header("campaign_id") == "campaign_id"


def test_headers_valid_with_aliases():
    ok, missing = validate_csv_headers(["FirstName", "LastName", "Mobile", "State"])
    assert ok is True and missing == []


def test_headers_valid_with_strict_original_spelling():
    ok, missing = validate_csv_headers(["first_name", "last_name", "state", "phone"])
    assert ok is True and missing == []


def test_headers_still_reject_genuinely_missing_required():
    ok, missing = validate_csv_headers(["FirstName", "State"])  # no last name / phone
    assert ok is False
    assert set(missing) == {"last_name", "phone"}


def test_bom_on_first_header_is_tolerated():
    ok, _ = validate_csv_headers(["﻿firstname", "lastname", "phone"])
    assert ok is True


def test_normalize_row_canonicalizes_keys():
    row = normalize_row({"FirstName": " Maria ", "Last Name": "Rodriguez",
                         "Mobile": "(305) 555-1234", "State": "FL", "Lead Score": "9"})
    assert row["first_name"] == "Maria"
    assert row["last_name"] == "Rodriguez"
    assert "phone" in row and row["phone"]          # phone present + normalized
    assert row["state"] == "FL"
    assert row["lead_score"] == "9"                 # unknown column preserved
