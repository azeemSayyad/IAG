"""SMS queue distribution fixes — leads must not strand in the pool:
  A) a PASSED lead is skipped for that agent only during a cooldown, then re-offered
  B) a deliberate PASS does not count as a "miss" that auto-parks a working agent

These guard the exact failure the manager saw: QUEUED leads piling up ("Rejected
2+ passes") while agents sit available but get no offer.
"""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.sms_queue.services import queue_service as q


def test_passed_lead_reoffered_after_cooldown():
    """A lead passed within the cooldown stays skipped for that agent; one passed
    longer ago re-enters their rotation (so aged leads never strand forever)."""
    now = datetime.now(timezone.utc)
    recent = ("lead-recent", now - timedelta(minutes=5))              # inside cooldown
    aged = ("lead-aged", now - timedelta(minutes=q.PASS_COOLDOWN_MINUTES + 30))  # past it
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [recent, aged]

    excluded = q._passed_lead_ids(db, "t1", "u1")
    assert "lead-recent" in excluded        # just passed → not bounced back yet
    assert "lead-aged" not in excluded       # aged past cooldown → re-offerable (fix A)


def test_pass_null_timestamp_is_excluded_safely():
    """A PASS row with no timestamp is treated as current (kept excluded), never crashes."""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [("lead-x", None)]
    assert q._passed_lead_ids(db, "t1", "u1") == {"lead-x"}


def test_deliberate_pass_does_not_auto_park_agent(monkeypatch):
    """Passing a lead resets the miss counter instead of incrementing it, so a rep
    actively working the queue is never auto-parked AWAY (fix B)."""
    agent = SimpleNamespace(consecutive_misses=1, current_lead_id="L1",
                            status="AVAILABLE", user_id="u1")
    lead = SimpleNamespace(id="L1", status="ASSIGNED", assigned_agent_id="u1", pass_count=1)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lead
    monkeypatch.setattr(q, "_get_or_create_agent", lambda *a, **k: agent)
    monkeypatch.setattr(q, "_try_assign", lambda *a, **k: [])

    data, _events = q.pass_lead(db, "t1", "u1", "L1")
    assert data["ok"] is True
    assert agent.consecutive_misses == 0            # reset, NOT incremented (fix B)
    assert lead.status == "QUEUED" and lead.pass_count == 2   # released back to pool
    assert agent.current_lead_id is None
