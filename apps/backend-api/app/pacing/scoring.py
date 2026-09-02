"""Lead prioritization & bucketing for capacity-aware release.

The best leads should consume the scarce same-day appointment inventory. This is
the rule-based v1 (deterministic, no model needed); app/ml upgrades it to trained
propensity later without changing this interface.

  priority_score : blend of lead_score + intent + profile completeness (0..~125)
  bucket         : Hot (>=70) / Warm (40..69) / Cold (<40)
  aging_bonus    : older held leads get a gradual boost so the backlog never rots
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.lead import Lead

logger = logging.getLogger(__name__)

_COMPLETENESS_KEYS = ("age", "dob", "income", "plan", "household", "address")
HOT, WARM = 70.0, 40.0


def _pct(v) -> float:
    """Normalize an intent probability to a 0..100 scale (handles 0-1 or 0-100)."""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


def compute_priority(lead: Lead) -> float:
    """Static base priority (no aging) in roughly 0..125.

    lead_score (0-100) is the backbone so buckets line up with it; intent
    (conversion/booking propensity) and profile completeness are additive
    bonuses that break ties toward the most convertible leads.
    """
    base = float(lead.lead_score or 0)
    # Conversion signal: prefer the trained ML model when an artifact exists;
    # otherwise fall back to the stored conversion_probability (rule-based).
    conv_pct = None
    try:
        from app.ml.lead_scoring import predict_conversion
        ml = predict_conversion(lead)
        if ml is not None:
            conv_pct = ml * 100.0
    except Exception:
        conv_pct = None
    if conv_pct is None:
        conv_pct = _pct(getattr(lead, "conversion_probability", 0))
    intent = min(15.0, 0.15 * conv_pct + 0.10 * _pct(getattr(lead, "booking_probability", 0)))
    cf = lead.custom_fields or {}
    completeness = 0.0
    if isinstance(cf, dict):
        completeness = min(10.0, sum(2.0 for k in _COMPLETENESS_KEYS if cf.get(k)))
    return round(base + intent + completeness, 2)


def aging_bonus(lead: Lead, now: Optional[datetime] = None) -> float:
    """+1.5 per day a lead has sat held, capped at +15 — applied at rank time."""
    created = getattr(lead, "created_at", None)
    if not created:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        days = (now - created).days
    except Exception:
        return 0.0
    return min(15.0, max(0, days) * 1.5)


def bucket(score: float) -> str:
    if score >= HOT:
        return "hot"
    if score >= WARM:
        return "warm"
    return "cold"


def score_leads(db: Session, lead_ids: List) -> int:
    """(Re)compute and persist priority_score for the given leads. Returns count."""
    if not lead_ids:
        return 0
    leads = db.query(Lead).filter(Lead.id.in_(lead_ids)).all()
    for lead in leads:
        lead.priority_score = compute_priority(lead)
    db.commit()
    return len(leads)


def ranked_held(
    db: Session,
    tenant_id: str,
    state: Optional[str],
    limit: int,
) -> List[Lead]:
    """Top ``limit`` held leads for a state, best-first (priority + aging).

    Ordered Hot -> Warm -> Cold via the composite score. A generous SQL prefetch
    is re-ranked in Python so the aging bonus (time-dependent) is applied without
    a computed SQL column.
    """
    if limit <= 0:
        return []
    q = db.query(Lead).filter(
        Lead.tenant_id == tenant_id,
        Lead.deleted_at.is_(None),
        Lead.pacing_status == "held",
        Lead.campaign_id.is_(None),   # campaign leads are released ONLY by their own campaign's drip
    )
    if state:
        q = q.filter(Lead.state == state)
    # Prefetch a multiple of the limit by static priority, then re-rank with aging.
    candidates = q.order_by(Lead.priority_score.desc().nullslast()).limit(max(limit * 4, limit + 50)).all()
    now = datetime.now(timezone.utc)
    candidates.sort(key=lambda l: (float(l.priority_score or 0) + aging_bonus(l, now)), reverse=True)
    return candidates[:limit]
