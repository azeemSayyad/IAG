"""Versioned model artifact store (JSON files on disk)."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
_CACHE: dict = {}


def _path(name: str) -> str:
    return os.path.join(ARTIFACTS_DIR, f"{name}.json")


def save_model(name: str, model_dict: dict) -> str:
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    p = _path(name)
    with open(p, "w") as f:
        json.dump(model_dict, f)
    _CACHE[name] = model_dict
    return p


def load_model(name: str) -> Optional[dict]:
    """Return the model dict, or None if no trained artifact exists."""
    if name in _CACHE:
        return _CACHE[name]
    p = _path(name)
    if not os.path.exists(p):
        _CACHE[name] = None
        return None
    try:
        with open(p) as f:
            d = json.load(f)
        _CACHE[name] = d
        return d
    except Exception as exc:  # pragma: no cover
        logger.warning("load_model %s failed: %s", name, exc)
        _CACHE[name] = None
        return None


def clear_cache() -> None:
    _CACHE.clear()
