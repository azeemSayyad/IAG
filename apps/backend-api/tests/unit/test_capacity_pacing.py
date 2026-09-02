"""Unit tests for capacity-sized pacing + compliance gate in drip (_apply_capacity).

Covers P2 (per-tick cap by free-agent demand) and P3 (drop leads in states with no
licensed agent). live_capacity's DB calls are stubbed; the engine flags are stubbed
via the resolver (engine_flags.engine_enabled).
"""
from types import SimpleNamespace

import pytest

from app.pacing import release
from app.pacing import live_capacity as lc


def L(state):
    return SimpleNamespace(state=state)


def _flags(**on):
    """Stub release.engine_flags.engine_enabled: True only for the named flags."""
    return lambda name: on.get(name, False)


@pytest.mark.unit
def test_off_is_noop(monkeypatch):
    monkeypatch.setattr(release.engine_flags, "engine_enabled", _flags())
    leads = [L("FL"), L("GA")]
    out, info = release._apply_capacity(None, "t", leads)
    assert out is leads
    assert info == {"capacity": "off"}


@pytest.mark.unit
def test_on_gates_unlicensed_states_and_caps(monkeypatch):
    monkeypatch.setattr(release.engine_flags, "engine_enabled", _flags(CAPACITY_PACING_ENABLED=True))
    monkeypatch.setattr(lc, "default_states", lambda: ["FL", "TX"])
    monkeypatch.setattr(lc, "states_with_capacity", lambda db, t, s: {"FL"})  # TX has no agents
    monkeypatch.setattr(lc, "release_ceiling", lambda db, t, s: 1)            # only 1 free slot
    leads = [L("TX"), L("FL"), L("FL")]
    out, info = release._apply_capacity(None, "t", leads)
    assert [x.state for x in out] == ["FL"]          # TX gated out, FL capped to 1
    assert info["after_gate"] == 2 and info["ceiling"] == 1 and info["released"] == 1
    assert info["allowed_states"] == ["FL"]


@pytest.mark.unit
def test_on_zero_capacity_holds_everything(monkeypatch):
    monkeypatch.setattr(release.engine_flags, "engine_enabled", _flags(CAPACITY_PACING_ENABLED=True))
    monkeypatch.setattr(lc, "default_states", lambda: ["FL"])
    monkeypatch.setattr(lc, "states_with_capacity", lambda db, t, s: {"FL"})
    monkeypatch.setattr(lc, "release_ceiling", lambda db, t, s: 0)            # all agents busy
    out, info = release._apply_capacity(None, "t", [L("FL"), L("FL")])
    assert out == []                                  # nothing released -> caller holds
    assert info["released"] == 0


@pytest.mark.unit
def test_on_stateless_lead_passes_gate(monkeypatch):
    monkeypatch.setattr(release.engine_flags, "engine_enabled", _flags(CAPACITY_PACING_ENABLED=True))
    monkeypatch.setattr(lc, "default_states", lambda: ["FL"])
    monkeypatch.setattr(lc, "states_with_capacity", lambda db, t, s: set())   # no licensed states
    monkeypatch.setattr(lc, "release_ceiling", lambda db, t, s: 5)
    out, info = release._apply_capacity(None, "t", [L(None), L("FL")])
    assert [x.state for x in out] == [None]           # state-less passes, FL (unlicensed) dropped


@pytest.mark.unit
def test_engine_status_endpoint_assembles_defensively():
    from unittest.mock import MagicMock
    from app.sms_queue.services import manager_service
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    st = manager_service.get_engine_status(db, "tenant-1")
    assert set(st.keys()) == {"flags", "capacity", "fleet"}
    assert "capacity_pacing_enabled" in st["flags"] and "capacity_pacing_source" in st["flags"]
    assert "carriers" in st["fleet"] and "free_agents_by_state" in st["capacity"]


@pytest.mark.unit
def test_fatigue_at_pull_drops_fatigued_leads(monkeypatch):
    import app.core.fatigue as fat
    # fatigue ON, capacity OFF -> fatigue still filters the pull, independently
    monkeypatch.setattr(release.engine_flags, "engine_enabled", _flags(FATIGUE_ENABLED=True))
    monkeypatch.setattr(fat, "fatigue_ok", lambda phone: not str(phone).endswith("1111"))
    leads = [SimpleNamespace(state="FL", phone="+13051111"),   # fatigued -> dropped
             SimpleNamespace(state="FL", phone="+13052222")]
    out, info = release._apply_capacity(None, "t", leads)
    assert [x.phone for x in out] == ["+13052222"]
    assert info["fatigue_dropped"] == 1 and info["capacity"] == "off"
