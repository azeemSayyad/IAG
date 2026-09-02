"""Unit tests for per-campaign first-message resolution (resolve_first_message)."""
import pytest

from app.ai.services import prompts


@pytest.mark.unit
def test_explicit_job_message_wins():
    assert prompts.resolve_first_message({"message": "explicit body"}) == "explicit body"


@pytest.mark.unit
def test_campaign_template_rendered_with_first_name_only(monkeypatch):
    monkeypatch.setattr(prompts, "_campaign_first_template", lambda cid: "Hi {first_name}, it's us!")
    msg = prompts.resolve_first_message({"campaign_id": "c1", "lead_name": "John Doe"})
    assert msg == "Hi John, it's us!"          # full name -> first token only, then rendered


@pytest.mark.unit
def test_falls_back_to_global_when_campaign_has_no_template(monkeypatch):
    monkeypatch.setattr(prompts, "_campaign_first_template", lambda cid: None)
    monkeypatch.setattr(prompts, "get_outreach_message", lambda *a, **k: "GLOBAL DEFAULT")
    assert prompts.resolve_first_message({"campaign_id": "c1", "lead_name": "John Doe"}) == "GLOBAL DEFAULT"


@pytest.mark.unit
def test_no_campaign_id_uses_global(monkeypatch):
    monkeypatch.setattr(prompts, "get_outreach_message", lambda *a, **k: "GLOBAL")
    assert prompts.resolve_first_message({"lead_name": "Jane"}) == "GLOBAL"
