"""No-webhook inbound polling for the dedicated hiree (applicant) account.

The hiree Sinch account has its OWN credentials and is NOT covered by the lead reply
poller. This polls the hiree account's /replies with its api key and files each reply
into the applicant inbox (never the lead pipeline), then confirms it so it isn't
re-delivered. Runs alongside the lead poller; the lead path is untouched.
"""
import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.applicant_inbox.inbound import route_inbound_if_applicant
from app.applicant_inbox.provider import applicant_provider

logger = logging.getLogger(__name__)


def poll_applicant_replies_once(db: Session) -> Dict[str, Any]:
    """Fetch + file the hiree account's replies once. Safe no-op when the hiree
    provider isn't configured (dev). Never raises."""
    fetched = applicant_provider.fetch_replies()
    if not fetched.get("success"):
        return {"processed": 0, "confirmed": 0, "failed": 0, "skipped": 0,
                "available": 0, "error": fetched.get("error")}

    from app.ai.services.communication_provider import communication_service
    from app.core.redis import redis_service

    processed = skipped = failed = 0
    confirm_ids = []

    for reply in fetched.get("replies", []):
        reply_id = reply.get("reply_id")
        from_number = reply.get("from")
        body = reply.get("body")
        if not reply_id or not from_number or not body:
            skipped += 1
            continue

        # Short lock so overlapping polls don't grab the same reply at once.
        try:
            first = redis_service.client.set(f"applicant:reply:{reply_id}", "1", nx=True, ex=300)
        except Exception:
            first = True
        if not first:
            skipped += 1
            continue

        # Shared content dedup with the webhook path (same (phone, body) key).
        if not communication_service.mark_inbound_seen(from_number, body):
            skipped += 1
            confirm_ids.append(reply_id)
            continue

        try:
            # This IS the hiree channel, so file every reply (skip the to-number check).
            route_inbound_if_applicant(db, reply.get("to"), from_number, body,
                                       require_applicant_number=False)
            confirm_ids.append(reply_id)
            processed += 1
        except Exception as exc:  # pragma: no cover - defensive
            failed += 1
            try:
                redis_service.client.delete(f"applicant:reply:{reply_id}")
            except Exception:
                pass
            logger.error("[applicant-inbox] failed to file reply %s: %s", reply_id, exc)

    confirmed = applicant_provider.confirm_replies(confirm_ids)
    return {"processed": processed, "confirmed": confirmed.get("confirmed", 0),
            "failed": failed, "skipped": skipped,
            "available": len(fetched.get("replies", []))}
