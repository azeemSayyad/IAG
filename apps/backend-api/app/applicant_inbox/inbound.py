"""Route inbound SMS for the dedicated hiree number into the applicant inbox.

A reply that arrives AT the reserved applicant number (APPLICANT_SMS_FROM_NUMBERS) is,
by definition, a hiree answering the recruiting channel — it must NOT enter the lead
pipeline (no lead created, no AI orchestration). Both inbound paths (the live Engage
Cloud webhook and the no-webhook reply poller) call ``route_inbound_if_applicant``
first; when it returns True the caller stops and never touches the lead flow.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.applicant_numbers import _digits, is_applicant_number
from app.models.applicant_message import ApplicantMessage
from app.models.hiree import HireeOnboarding

logger = logging.getLogger(__name__)


def _match_hiree(db: Session, from_number: str) -> Optional[HireeOnboarding]:
    """The hiree whose phone matches the inbound sender, format-agnostically. Hiree
    phones are free-form, so compare canonical digits. Recruiting volume is small."""
    target = _digits(from_number)
    if not target:
        return None
    for h in db.query(HireeOnboarding).filter(HireeOnboarding.phone.isnot(None)).all():
        if _digits(h.phone) == target:
            return h
    return None


def route_inbound_if_applicant(
    db: Session, to_number, from_number, body, *, require_applicant_number: bool = True
) -> bool:
    """If ``to_number`` is the dedicated hiree number, record the reply as an INBOUND
    ApplicantMessage on the matching hiree thread and return True (handled). Returns
    False only when the message is NOT addressed to the applicant number, so the caller
    handles it as a normal lead reply.

    ``require_applicant_number=False`` skips the destination check — used by the
    DEDICATED applicant webhook, which is already the hiree-only channel, so every
    inbound it receives is filed here regardless of how the 'to' number is formatted.

    Always returns True once routed to the applicant inbox — even when no hiree matches
    the sender — so such a message can never leak into the lead pipeline.
    """
    if require_applicant_number and not is_applicant_number(to_number):
        return False

    text = (body or "").strip()
    if not text:
        return True  # addressed to the hiree number but empty — swallow

    hiree = _match_hiree(db, from_number)
    if not hiree:
        logger.warning(
            "[applicant-inbox] inbound to dedicated number from unknown sender %s — "
            "kept out of the lead pipeline (no matching hiree)", from_number,
        )
        return True

    try:
        msg = ApplicantMessage(
            tenant_id=hiree.tenant_id,
            contact_type="hiree",
            hiree_id=hiree.id,
            phone_number=(from_number or hiree.phone or "").strip(),
            from_number=(from_number or "").strip(),
            direction="INBOUND",
            body=text,
            sender_type="APPLICANT",
            status="RECEIVED",
        )
        db.add(msg)
        db.commit()
        logger.info("[applicant-inbox] inbound reply recorded for hiree %s", hiree.id)
    except Exception as exc:  # never let inbox capture raise into the webhook
        db.rollback()
        logger.error("[applicant-inbox] failed to record inbound reply: %s", exc)
    return True
