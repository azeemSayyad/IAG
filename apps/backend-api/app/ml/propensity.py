"""ML reply/book/show propensity (graceful fallback to None)."""

from __future__ import annotations

from typing import Optional

from app.ml import registry, features
from app.ml.logreg import LogReg

_NAMES = {"reply": "reply", "book": "book", "show": "show"}


def predict(kind: str, lead) -> Optional[float]:
    name = _NAMES.get(kind)
    if not name:
        return None
    d = registry.load_model(name)
    if not d:
        return None
    try:
        return LogReg.from_dict(d).predict_proba(features.lead_features(lead))
    except Exception:
        return None
