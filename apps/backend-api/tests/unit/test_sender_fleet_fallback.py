"""Sender-number selection — a lead whose state has NO dedicated numbers must
round-robin across the WHOLE fleet (all state numbers + global pool), not collapse
onto the single global ENGAGECLOUD_FROM_NUMBERS. Mapped states (FL/TX) are unchanged.
"""
from app.core.state_sender_numbers import all_sender_numbers, numbers_for_state


def test_all_sender_numbers_is_deduped_union():
    fleet = all_sender_numbers()
    assert len(fleet) == len(set(fleet))                 # deduped
    assert set(numbers_for_state("FL")) <= set(fleet)    # includes FL
    assert set(numbers_for_state("TX")) <= set(fleet)    # includes TX
    assert len(fleet) >= len(numbers_for_state("FL")) + 1


def test_matched_state_still_uses_its_own_pool(monkeypatch):
    from app.ai.services import communication_provider as cp
    from app.ai.services import sender_pool
    svc = cp.communication_service
    assert svc._provider == "sinch"
    monkeypatch.setattr(svc, "_state_pool_for_lead", lambda lead_id=None: ["+15610000001", "+15610000002"])
    captured = {}
    monkeypatch.setattr(sender_pool, "select_sender",
                        lambda **kw: (captured.update(kw), "+15610000001")[1])
    svc._sender(tenant_id="t1", lead_id="L1")
    assert captured["pool"] == ["+15610000001", "+15610000002"]   # FL/TX pool unchanged


def test_unmatched_state_uses_full_fleet(monkeypatch):
    from app.ai.services import communication_provider as cp
    from app.ai.services import sender_pool
    svc = cp.communication_service
    monkeypatch.setattr(svc, "_state_pool_for_lead", lambda lead_id=None: [])   # e.g. Georgia
    captured = {}
    monkeypatch.setattr(sender_pool, "select_sender",
                        lambda **kw: (captured.update(kw), "+15610000001")[1])
    svc._sender(tenant_id="t1", lead_id="L1")
    pool = captured["pool"]
    assert pool is not None and len(pool) > 5                       # NOT a single number
    assert set(numbers_for_state("FL")) & set(pool)                 # spreads over FL numbers
    assert set(numbers_for_state("TX")) & set(pool)                 # and TX numbers


def test_full_fleet_excludes_reserved_hiree_number(monkeypatch):
    """The reserved hiree DID must never enter the lead fleet, even though it's listed
    among the FL numbers."""
    from app.ai.services import communication_provider as cp
    import app.core.applicant_numbers as an
    svc = cp.communication_service
    monkeypatch.setattr(an, "is_applicant_number", lambda num: num == "+17723150752")
    fleet = svc._full_fleet_pool()
    assert "+17723150752" not in fleet
    assert len(fleet) == len(set(fleet))                            # still deduped
