"""ML layer for the capacity engine (dependency-free, pluggable).

Trained models upgrade the rule-based scoring/funnel without changing their
interfaces. Everything degrades gracefully: if no trained artifact exists (or a
prediction fails), callers fall back to the deterministic logic in
app/pacing/scoring.py and app/pacing/funnel.py.

Models are pure-Python logistic regression (app/ml/logreg.py) trained from real
funnel events (scripts/train_pacing_models.py) — no numpy/sklearn required, so
the engine works out of the box and improves once trained.
"""
