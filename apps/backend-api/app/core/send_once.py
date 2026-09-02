"""First-template send-once guard, scoped PER CAMPAIGN.

A number gets the FIRST template AT MOST ONCE per campaign — even when the same
CSV is uploaded into a campaign more than once, or a number appears in it twice.
A NEW campaign starts fresh, so re-uploading as a new campaign re-texts everyone
(the intended behaviour). Sends with no campaign share one 'none' bucket, so
non-campaign duplicates collapse to one too.

We claim (campaign, phone) atomically BEFORE the send (Redis SET NX, 90-day
window) so two concurrent drip waves can't double-send, and we release the claim
if the send genuinely fails so a retry can still go out. This is additive to the
first-template-only lockdown — it never relaxes it, only makes the first template
send at most once per number per campaign.
"""
from __future__ import annotations

_SENT_KEY = "sms:ft_sent:{tid}:{camp}:{phone}"
_TTL_SECONDS = 90 * 24 * 3600   # one 90-day outreach window


def _digits(phone) -> str:
    """Digits-only phone, so '+1 (850) 503-1888' and '+18505031888' match."""
    return "".join(c for c in str(phone or "") if c.isdigit())


def _camp(campaign_id) -> str:
    """Campaign bucket; non-campaign sends share the single 'none' bucket."""
    c = str(campaign_id or "").strip()
    return c or "none"


def claim_first_template_send(tenant_id, campaign_id, phone) -> bool:
    """Atomically claim this campaign's single first-template send for this phone.

    Returns True if the caller SHOULD send (first claim in this campaign), False
    if it was already sent/claimed for this (campaign, phone) — caller skips.
    Fails OPEN (True) if Redis is unreachable; the send pipeline already needs
    Redis, so this only ever degrades back to today's behaviour."""
    digits = _digits(phone)
    if not tenant_id or not digits:
        return True
    try:
        from app.core.redis import redis_service
        key = _SENT_KEY.format(tid=tenant_id, camp=_camp(campaign_id), phone=digits)
        return bool(redis_service.client.set(key, "1", nx=True, ex=_TTL_SECONDS))
    except Exception:
        return True


def release_first_template_claim(tenant_id, campaign_id, phone) -> None:
    """Undo a claim when the send failed, so a legitimate retry can re-send."""
    digits = _digits(phone)
    if not tenant_id or not digits:
        return
    try:
        from app.core.redis import redis_service
        redis_service.client.delete(_SENT_KEY.format(tid=tenant_id, camp=_camp(campaign_id), phone=digits))
    except Exception:
        pass


def record_duplicate_suppressed() -> None:
    """Proof-counter for the duplicates we stop:
    send:suppressed_duplicate:first_template grows each time one is suppressed."""
    try:
        from app.core.redis import redis_service
        redis_service.client.incr("send:suppressed_duplicate:first_template")
    except Exception:
        pass
