"""The authorization texts (owner's Developer Spec, 2026-11-17)."""
import pytest

from app.hs_auth import auth_texts as a


def test_lengths_match_the_spec():
    assert len(a.AUTH_MAIN_TEXT.replace("{agent_name}", "")) == 234
    assert len(a.AUTH_REMINDER_TEXT.replace("{agent_name}", "")) == 148
    assert "STOP" in a.AUTH_MAIN_TEXT and "STOP" not in a.AUTH_REMINDER_TEXT
    assert "\n\n1. Open that text\n2. Tap the link inside it\n3. Follow the steps to say YES\n\n" in a.AUTH_MAIN_TEXT


@pytest.mark.parametrize("full,sms", [
    ("Alandinio Norame", "Alandinio Norame"),
    ("Maximiliano Fernandez-Rodriguez", "Maximiliano..."),
    ("Ana Lopez", "Ana Lopez"),
    ("Christopherjonathan Smith", "Christopherjonath..."),
    ("José Muñoz", "Jose Munoz"),
    ("  Stanley   Loseille ", "Stanley Loseille"),
])
def test_agent_name_rule(full, sms):
    assert a.agent_name_for_sms(full) == sms
    assert len(sms) <= 20 and "…" not in sms


def test_texts_are_plain_gsm_and_fit_two_segments():
    for t in (a.AUTH_MAIN_TEXT, a.AUTH_REMINDER_TEXT):
        out = a.build(t, "Maximiliano Fernandez-Rodriguez")
        assert a.is_gsm(out) and len(out) <= 306


def test_non_gsm_name_falls_back_to_no_name():
    out = a.build(a.AUTH_MAIN_TEXT, "Stan 😀 Lo")
    assert '"Your agent... with Insurance Alliance Group..."' in out and "😀" not in out
    assert a.build(a.AUTH_REMINDER_TEXT, "") .count('"Your agent... with Insurance Alliance Group..."') == 1
