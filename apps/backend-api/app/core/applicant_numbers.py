"""Dedicated hiree (applicant) sender pool.

A small Sinch sub-account number — `ACAHelplineChannel_PLM_0002` / +1 772 315 0752 —
is reserved EXCLUSIVELY for texting hirees (job applicants) from the Inbox. It must
NEVER be used for lead outreach, so:

  * the lead sender pool (`sender_pool` / `communication_provider._sender`) filters
    every number in this pool OUT of rotation — even if one is left in
    `ENGAGECLOUD_FROM_NUMBERS` by mistake, a lead blast can never pick it; and
  * inbound replies that arrive AT one of these numbers are routed to the applicant
    inbox, never the lead pipeline.

Config (env-overridable, comma-separated):
  APPLICANT_SMS_FROM_NUMBERS  the dedicated pool. Falls back to the single
                              APPLICANT_SMS_FROM_NUMBER when blank.

This module only parses config — no DB, no provider — so it is safe to import from
both the lead send path and the inbound webhook with no circular imports.
"""
from __future__ import annotations

from typing import List, Optional, Set

from app.core.config import settings


def _digits(num) -> str:
    """Canonical US key: digits only, leading '1' country code dropped, so
    '+1 (772) 315-0752' and '7723150752' match the same pool entry."""
    d = "".join(c for c in str(num or "") if c.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def _e164(num) -> str:
    """'+<digits>' with the country code kept (the form the provider wants as a
    source_number). '17723150752' -> '+17723150752'."""
    d = "".join(c for c in str(num or "") if c.isdigit())
    return f"+{d}" if d else ""


def applicant_pool() -> List[str]:
    """The reserved hiree numbers in E.164 ('+1…'), in config order. Reads
    APPLICANT_SMS_FROM_NUMBERS, falling back to the single APPLICANT_SMS_FROM_NUMBER."""
    raw = (getattr(settings, "APPLICANT_SMS_FROM_NUMBERS", "") or "").replace(";", ",")
    nums = [n.strip() for n in raw.split(",") if n.strip()]
    if not nums:
        single = (getattr(settings, "APPLICANT_SMS_FROM_NUMBER", "") or "").strip()
        if single:
            nums = [single]
    out: List[str] = []
    for n in nums:
        e = _e164(n)
        if e and e not in out:
            out.append(e)
    return out


def applicant_pool_digits() -> Set[str]:
    """Canonical (leading-1-stripped) digit strings of the reserved pool, for matching
    an arbitrary number against it."""
    return {_digits(n) for n in applicant_pool() if _digits(n)}


def is_applicant_number(num) -> bool:
    """True if `num` is one of the reserved hiree numbers (format-agnostic)."""
    d = _digits(num)
    return bool(d) and d in applicant_pool_digits()


def pick_sender(seed: Optional[str] = None) -> str:
    """Choose a hiree sender number (E.164). One number today; for a multi-number pool
    this round-robins via a best-effort Redis cursor, falling back to the first."""
    pool = applicant_pool()
    if not pool:
        return ""
    if len(pool) == 1:
        return pool[0]
    try:
        from app.core.redis import redis_service
        idx = int(redis_service.client.incr("applicant:sender:cursor"))
    except Exception:
        idx = 0
    return pool[idx % len(pool)]
