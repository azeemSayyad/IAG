"""Shared lead feature extraction for the ML models (fixed order)."""

from __future__ import annotations

from typing import List

# Stable feature order — training and serving MUST agree.
FEATURE_NAMES = [
    "lead_score",            # 0..1
    "has_email",
    "has_zip",
    "has_state",
    "conversion_prob",       # 0..1
    "booking_prob",          # 0..1
    "completeness",          # 0..1
]

_COMPLETENESS_KEYS = ("age", "dob", "income", "plan", "household", "address")


def _pct01(v) -> float:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:
        v /= 100.0
    return max(0.0, min(1.0, v))


def lead_features(lead) -> List[float]:
    cf = getattr(lead, "custom_fields", None) or {}
    completeness = 0.0
    if isinstance(cf, dict) and cf:
        completeness = min(1.0, sum(1 for k in _COMPLETENESS_KEYS if cf.get(k)) / len(_COMPLETENESS_KEYS))
    return [
        max(0.0, min(1.0, float(getattr(lead, "lead_score", 0) or 0) / 100.0)),
        1.0 if getattr(lead, "email", None) else 0.0,
        1.0 if getattr(lead, "zip_code", None) else 0.0,
        1.0 if getattr(lead, "state", None) else 0.0,
        _pct01(getattr(lead, "conversion_probability", 0)),
        _pct01(getattr(lead, "booking_probability", 0)),
        completeness,
    ]
