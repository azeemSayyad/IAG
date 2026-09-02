"""Measured conversion-funnel rates with safe defaults.

reply_rate = P(reply | contacted), book_rate = P(book | reply),
show_rate  = P(show | booked). Measured from real events over a rolling window;
when a stage has too few samples we fall back to the configured default so the
controller always has usable numbers (cold-start safe).
"""

from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.pacing import events

# Below this many observations for a stage, trust the configured default instead
# of a noisy measured ratio.
_MIN_SAMPLE = 20


def _ratio(num: int, den: int, default: float) -> float:
    if den < _MIN_SAMPLE:
        return default
    if den <= 0:
        return default
    return max(0.01, min(1.0, num / den))


def rates(db: Session, tenant_id: str, state: Optional[str] = None) -> Dict[str, float]:
    """Return {reply, book, show} for a state (or tenant-wide if state is None)."""
    window = int(getattr(settings, "PACING_FUNNEL_WINDOW_DAYS", 21) or 21)
    c = events.funnel_counts(db, tenant_id, state=state, days=window)

    reply = _ratio(c["replied"], c["contacted"], settings.PACING_DEFAULT_REPLY_RATE)
    book = _ratio(c["booked"], c["replied"], settings.PACING_DEFAULT_BOOK_RATE)
    show = _ratio(c["shown"], c["booked"], settings.PACING_DEFAULT_SHOW_RATE)

    return {
        "reply": round(reply, 4),
        "book": round(book, 4),
        "show": round(show, 4),
        "effective_conv": round(max(0.0001, reply * book), 5),
        "samples": c,
    }
