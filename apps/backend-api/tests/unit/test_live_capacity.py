"""Unit tests for the live-capacity demand math (pure helpers, no DB)."""
from types import SimpleNamespace

import pytest

from app.pacing import live_capacity as lc


def A(uid):
    return SimpleNamespace(user_id=uid)


@pytest.mark.unit
def test_count_free_licensed_basic():
    free = {"u1", "u2"}
    licensed = {"FL": [A("u1"), A("u3")], "TX": [A("u2")], "GA": [A("u4")]}
    assert lc.count_free_licensed(free, licensed) == {"FL": 1, "TX": 1, "GA": 0}


@pytest.mark.unit
def test_count_free_licensed_multi_state_agent_counts_in_each():
    free = {"u1"}
    licensed = {"FL": [A("u1")], "TX": [A("u1")], "GA": []}
    assert lc.count_free_licensed(free, licensed) == {"FL": 1, "TX": 1, "GA": 0}


@pytest.mark.unit
def test_count_free_licensed_busy_agent_not_counted():
    # u1 is licensed but NOT in the free set (busy / ON_CALL) -> 0
    assert lc.count_free_licensed(set(), {"FL": [A("u1")]}) == {"FL": 0}


@pytest.mark.unit
def test_total_demand_applies_buffer():
    assert lc.total_demand({"FL": 2, "TX": 2}, 1.5) == 6      # 4 * 1.5
    assert lc.total_demand({"FL": 4}, 1.0) == 4
    assert lc.total_demand({"FL": 0, "TX": 0}, 1.5) == 0      # no free agents -> pause
    assert lc.total_demand({}, 1.5) == 0


@pytest.mark.unit
def test_gate_states_drops_zero_agent_states():
    licensed = {"FL": [A("u1")], "TX": [], "GA": [A("u2"), A("u3")]}
    assert lc.gate_states(licensed) == {"FL", "GA"}


@pytest.mark.unit
def test_gate_states_empty():
    assert lc.gate_states({"FL": [], "TX": []}) == set()
