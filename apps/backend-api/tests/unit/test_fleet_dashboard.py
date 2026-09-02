"""Unit tests for the DID-provisioning forecast (pure, no Redis)."""
import pytest

from app.ai.services import fleet_dashboard as fd


@pytest.mark.unit
def test_flat_demand_gives_no_forecast():
    out = fd.forecast(240, [100, 100, 100], 100, 0.8, 2000, 1)
    assert out["growth_per_day"] == 0.0
    assert out["days_until_new_did"] is None       # not growing -> not needed
    assert out["recommend_provision"] is False
    assert out["history_days_used"] == 3


@pytest.mark.unit
def test_growing_demand_projects_days():
    # cap 240 -> provision at 192; today 150 growing +10/day -> ceil((192-150)/10)=5
    out = fd.forecast(240, [120, 130, 140, 150], 150, 0.8, 2000, 1)
    assert out["growth_per_day"] == 10.0
    assert out["days_until_new_did"] == 5
    assert out["recommend_provision"] is True      # <= 7 days


@pytest.mark.unit
def test_over_threshold_provision_now():
    out = fd.forecast(240, [180, 200], 200, 0.8, 2000, 1)   # 200 >= 0.8*240
    assert out["days_until_new_did"] == 0 and out["recommend_provision"] is True


@pytest.mark.unit
def test_over_capacity_provision_now():
    out = fd.forecast(240, [240], 250, 0.8, 2000, 1)
    assert out["days_until_new_did"] == 0


@pytest.mark.unit
def test_suggested_new_dids():
    # today 5000, per-number cap 2000, threshold 0.8 -> need ceil(5000/1600)=4; have 1 -> 3
    out = fd.forecast(8000, [5000], 5000, 0.8, 2000, 1)
    assert out["suggested_new_dids"] == 3


@pytest.mark.unit
def test_zero_capacity_provision_now():
    out = fd.forecast(0, [10], 10, 0.8, 2000, 0)
    assert out["days_until_new_did"] == 0
