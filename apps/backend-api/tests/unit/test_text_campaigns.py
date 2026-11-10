"""Text campaigns (Auto Dialer page) — the parts with no database."""
from types import SimpleNamespace

import pytest

from app.voice_dialer import campaigns, telnyx


def test_name_is_filled_and_stop_line_added():
    assert campaigns.render("Hi {name}, reply YES for a call.", "maria") == \
        "Hi Maria, reply YES for a call. Reply STOP to opt out."
    assert campaigns.render("Hi {first_name}!", "JOHN smith") == "Hi John! Reply STOP to opt out."
    assert campaigns.render("Hi {name}. Reply STOP to end", None) == "Hi there. Reply STOP to end"


def test_template_limits():
    with pytest.raises(campaigns.CampaignError):
        campaigns.check_template("   ")
    with pytest.raises(campaigns.CampaignError):
        campaigns.check_template("x" * (campaigns.MAX_TEMPLATE + 1))


def test_csv_columns_found_by_header_and_rows_cleaned():
    raw = (
        "First Name,Last Name,Mobile Phone,State,Policy Number\n"
        "Ana,Diaz,(305) 555-0101,Florida,P-1\n"
        "Bo,Li,305.555.0101,TX,P-2\n"        # duplicate number
        "Cy,Ng,12345,GA,P-3\n"               # invalid
        "Di,Oz,+1 404 555 0199,ga,P-4\n"
    ).encode()
    people, stats = campaigns.parse_csv(raw)
    assert [p["phone"] for p in people] == ["+13055550101", "+14045550199"]
    assert people[0]["first_name"] == "Ana" and people[0]["state"] == "FL" and people[1]["state"] == "GA"
    assert ["Policy Number", "P-1"] in people[0]["fields"]
    assert stats == {"rows": 4, "invalid": 1, "duplicate": 1}


def test_csv_without_header_finds_the_phone_column():
    people, stats = campaigns.parse_csv(b"Ana Diaz,3055550101\nBo Li,4045550199\n")
    assert [p["phone"] for p in people] == ["+13055550101", "+14045550199"] and stats["rows"] == 2


def test_csv_full_name_column_gives_first_name():
    people, _ = campaigns.parse_csv(b"Name,Phone\nMaria Lopez,3055550101\n")
    assert people[0]["first_name"] == "Maria"


def test_csv_with_no_phones_is_refused():
    with pytest.raises(campaigns.CampaignError):
        campaigns.parse_csv(b"Name,City\nAna,Miami\n")


def test_a_client_always_gets_the_same_number():
    nums = ["+15610000001", "+15610000002", "+15610000003"]
    a = campaigns.sender_for("+13055550101", nums)
    assert all(campaigns.sender_for("+13055550101", nums) == a for _ in range(5))
    # The number they already know wins.
    assert campaigns.sender_for("+13055550101", nums, known="+15610000003") == "+15610000003"
    picks = {campaigns.sender_for(f"+1305555{i:04d}", nums) for i in range(60)}
    assert picks == set(nums)  # spread across every number


def test_delivery_report_parsing():
    def rep(event, status, errors=None):
        return {"data": {"event_type": event, "payload": {"id": "m1", "to": [{"status": status}], "errors": errors or []}}}

    assert telnyx.message_status(rep("message.finalized", "delivered"))["status"] == "delivered"
    failed = telnyx.message_status(rep("message.finalized", "delivery_failed", [{"code": "40002", "title": "Blocked as spam"}]))
    assert failed["status"] == "failed" and failed["error_code"] == "40002"
    assert telnyx.message_status(rep("message.sent", "queued")) == {}
    assert telnyx.message_status({"data": {"event_type": "message.received", "payload": {}}}) == {}


def _db_with(campaign, history=()):
    class Q:
        def __init__(self, rows): self.rows = rows
        def filter(self, *a, **k): return self
        def order_by(self, *a): return self
        def limit(self, n): return Q(self.rows[:n])
        def first(self): return campaign
        def all(self): return [(s,) for s in self.rows]

    return SimpleNamespace(query=lambda *a: Q(list(history)))


def _recipient():
    return SimpleNamespace(status="SENDING", error=None, campaign_id="c1", tenant_id="t1",
                           phone_number="+13055550101", from_number="+15610000001")


def test_spam_block_pauses_the_campaign():
    c = SimpleNamespace(id="c1", status="RUNNING", pause_reason=None)
    r = _recipient()
    campaigns._failed(_db_with(c), r, "40002", "Blocked as spam")
    assert r.status == "FAILED" and c.status == "PAUSED" and "spam" in c.pause_reason


def test_unregistered_number_pauses_the_campaign():
    c = SimpleNamespace(id="c1", status="RUNNING", pause_reason=None)
    campaigns._failed(_db_with(c), _recipient(), "40010", "Not 10DLC registered")
    assert c.status == "PAUSED" and "10DLC" in c.pause_reason


def test_a_run_of_failures_pauses_but_one_does_not():
    c = SimpleNamespace(id="c1", status="RUNNING", pause_reason=None)
    campaigns._failed(_db_with(c, ["FAILED"] * 3 + ["DELIVERED"] * 7), _recipient(), "40008", "Undeliverable")
    assert c.status == "RUNNING"
    campaigns._failed(_db_with(c, ["FAILED"] * campaigns.FAIL_STREAK), _recipient(), "40008", "Undeliverable")
    assert c.status == "PAUSED"


def test_campaign_texts_are_paced_safely():
    # Five numbers at one text per SEND_GAP_SECONDS each stays far under AT&T's
    # 240/min for the campaign, and the day stops at T-Mobile's 2,000.
    assert 5 * 60 / campaigns.SEND_GAP_SECONDS <= 240
    from app.core.config import settings

    assert settings.TEXT_DAILY_CAP <= 2000
