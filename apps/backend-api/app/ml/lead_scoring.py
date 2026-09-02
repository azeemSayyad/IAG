"""ML conversion-propensity scoring (graceful fallback to None)."""

from __future__ import annotations

from typing import Optional

from app.ml import registry, features
from app.ml.logreg import LogReg

MODEL_NAME = "conversion"


def predict_conversion(lead) -> Optional[float]:
    """P(conversion) in [0,1] from the trained model, or None if unavailable."""
    d = registry.load_model(MODEL_NAME)
    if not d:
        return None
    try:
        return LogReg.from_dict(d).predict_proba(features.lead_features(lead))
    except Exception:
        return None


def available() -> bool:
    return registry.load_model(MODEL_NAME) is not None
