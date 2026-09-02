"""Unit tests for the carrier registry (pure parsing/ordering, no settings/DB)."""
import json

import pytest

from app.ai.services import carrier_registry as cr


@pytest.mark.unit
def test_empty_json_defaults_to_single_sinch_carrier():
    # mixed input: already-prefixed and bare-with-country-code -> both normalized to E.164
    carriers = cr.parse_carrier_pools("", "+13051112222, 13052223333")
    assert len(carriers) == 1
    c = carriers[0]
    assert c["name"] == "sinch" and c["role"] == "primary"
    assert c["numbers"] == ["+13051112222", "+13052223333"]   # '+' prepended, digits kept as-is


@pytest.mark.unit
def test_invalid_json_falls_back_to_default():
    carriers = cr.parse_carrier_pools("{not json", "+13051112222")
    assert len(carriers) == 1 and carriers[0]["name"] == "sinch"


@pytest.mark.unit
def test_parses_multiple_carriers_with_roles_and_limits():
    cfg = json.dumps([
        {"name": "sinch", "priority": 1, "role": "primary", "numbers": ["+1305111"]},
        {"name": "carrierB", "priority": 2, "role": "primary", "daily_cap": 500, "mps": 2, "numbers": ["+1305222"]},
        {"name": "safety", "priority": 9, "role": "reserve", "numbers": ["+1305999"]},
    ])
    carriers = cr.parse_carrier_pools(cfg, "ignored")
    names = [c["name"] for c in carriers]
    assert names == ["sinch", "carrierB", "safety"]
    assert carriers[1]["daily_cap"] == 500 and carriers[1]["mps"] == 2


@pytest.mark.unit
def test_carrier_of_map():
    carriers = cr.parse_carrier_pools(json.dumps([
        {"name": "sinch", "numbers": ["+1305111"]},
        {"name": "carrierB", "numbers": ["+1305222"]},
    ]), "")
    m = cr.carrier_of_map(carriers)
    assert m["+1305111"] == "sinch" and m["+1305222"] == "carrierB"


@pytest.mark.unit
def test_ordered_numbers_primary_first_reserve_last():
    carriers = cr.parse_carrier_pools(json.dumps([
        {"name": "safety", "priority": 9, "role": "reserve", "numbers": ["+1999"]},
        {"name": "carrierB", "priority": 2, "role": "primary", "numbers": ["+1222"]},
        {"name": "sinch", "priority": 1, "role": "primary", "numbers": ["+1111"]},
    ]), "")
    # primaries by priority (sinch=1, carrierB=2), reserve last
    assert cr.ordered_numbers(carriers, include_reserve=True) == ["+1111", "+1222", "+1999"]
    assert cr.ordered_numbers(carriers, include_reserve=False) == ["+1111", "+1222"]


@pytest.mark.unit
def test_dedupe_number_across_carriers_first_wins():
    carriers = cr.parse_carrier_pools(json.dumps([
        {"name": "sinch", "priority": 1, "numbers": ["+1111", "+1222"]},
        {"name": "carrierB", "priority": 2, "numbers": ["+1222"]},
    ]), "")
    assert cr.ordered_numbers(carriers) == ["+1111", "+1222"]
    assert cr.carrier_of_map(carriers)["+1222"] == "sinch"   # first listed wins
