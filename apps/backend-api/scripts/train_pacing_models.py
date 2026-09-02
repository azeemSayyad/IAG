"""Train the capacity-engine propensity models from real funnel events.

Builds labeled examples from the leads/appointments tables and fits the
dependency-free logistic-regression models (conversion / reply / book), saving
them to app/ml/artifacts/. Safe to run repeatedly (nightly). If there is too
little data a model is skipped and the engine keeps using the rule-based path.

Usage:
    PYTHONPATH=apps/backend-api python scripts/train_pacing_models.py
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("train_pacing")

MIN_EXAMPLES = 30
_REPLIED = ("replied", "qualified", "booked", "completed", "won")
_BOOKED = ("booked", "completed", "won")


def main() -> int:
    from app.core.database import SessionLocal
    from app.models.lead import Lead
    from app.models.appointment import Appointment
    from app.ml import features, registry
    from app.ml.logreg import LogReg

    db = SessionLocal()
    try:
        leads = db.query(Lead).filter(Lead.deleted_at.is_(None)).limit(50000).all()
        booked_ids = {row[0] for row in db.query(Appointment.lead_id).all()}
        log.info("loaded %s leads, %s booked lead ids", len(leads), len(booked_ids))

        conv_X, conv_y = [], []
        reply_X, reply_y = [], []
        book_X, book_y = [], []

        for l in leads:
            f = features.lead_features(l)
            replied = bool(getattr(l, "last_replied_at", None)) or (l.status in _REPLIED)
            booked = (l.id in booked_ids) or (l.status in _BOOKED)
            conv_X.append(f); conv_y.append(1.0 if booked else 0.0)
            reply_X.append(f); reply_y.append(1.0 if replied else 0.0)
            if replied:
                book_X.append(f); book_y.append(1.0 if booked else 0.0)

        def _train(name, X, y):
            pos = sum(y)
            if len(X) < MIN_EXAMPLES or pos == 0 or pos == len(y):
                log.info("skip %s: insufficient/degenerate data (n=%s pos=%s)", name, len(X), int(pos))
                return False
            m = LogReg().fit(X, y, epochs=500, lr=0.3, l2=1e-3)
            registry.save_model(name, m.to_dict())
            log.info("trained %s: n=%s pos=%s -> %s", name, len(X), int(pos), registry._path(name))
            return True

        trained = sum([
            _train("conversion", conv_X, conv_y),
            _train("reply", reply_X, reply_y),
            _train("book", book_X, book_y),
        ])
        registry.clear_cache()
        log.info("done: %s model(s) trained", trained)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
