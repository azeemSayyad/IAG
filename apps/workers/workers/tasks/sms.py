"""
SMS Worker Tasks

Processes outbound SMS queue.
"""

import logging
import json
from datetime import datetime, timezone
from typing import Dict

from workers.celery_app import celery_app
from app.core.database import get_db
from app.core.audit import log_ai_action

logger = logging.getLogger(__name__)

# Sync Socket.IO emitter (write-only) for the worker process. Publishes to the
# same Redis channel the async web-server Socket.IO manager reads, so events
# emitted here (e.g. outbound SMS) reach connected browsers.
_rt_emitter = None
def _emit_realtime(tenant_id: str, event: str, data: dict):
    global _rt_emitter
    try:
        if _rt_emitter is None:
            import socketio
            from app.core.config import settings as _s
            _rt_emitter = socketio.Server(client_manager=socketio.RedisManager(_s.REDIS_URL, write_only=True))
        _rt_emitter.emit(event, data, room=f"tenant:{tenant_id}")
    except Exception as e:  # never let realtime break the send path
        logger.warning(f"realtime emit failed: {e}")


def _send_sms_impl(lead_id: str, message: str, tenant_id: str, campaign_id: str = None, kind: str = "other") -> Dict:
    from app.ai.services.communication_provider import communication_service
    from app.ai.services.communication_provider import send_sms_to_lead
    from app.models.conversation import Conversation
    from app.models.lead import Lead
    from app.models.message import Message

    logger.info(f"Sending SMS to lead {lead_id}")
    db = next(get_db())
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return {"success": False, "error": "Lead not found"}

        # Guard: never re-send cold outreach to a lead that has already moved
        # past the outreach stage. A stale queued job must not clobber a booked
        # lead's status or spawn a duplicate conversation.
        if (lead.status or "").lower() in ("booked", "stopped", "unqualified"):
            logger.info(f"Skipping outreach for lead {lead_id}: already {lead.status}")
            return {"success": False, "skipped": True, "reason": f"lead already {lead.status}"}

        # Resolve which lead-SMS provider THIS campaign sends through (default
        # "sinch"). Looked up per-send from the campaign so old queued jobs (no
        # provider field) still route correctly; unknown/blank => Sinch.
        provider = "sinch"
        if campaign_id:
            try:
                from app.models.campaign import Campaign
                camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
                if camp and getattr(camp, "provider", None):
                    provider = camp.provider
            except Exception:
                provider = "sinch"

        result = send_sms_to_lead(
            phone=lead.phone,
            lead_id=lead_id,
            message=message,
            tenant_id=tenant_id,
            kind=kind,
            campaign_id=campaign_id,
            provider=provider,
        )

        if result.get("success"):
            lead.status = "contacted"
            lead.lifecycle_stage = "contacted"
            lead.last_contacted_at = datetime.now(timezone.utc)

            conversation = (
                db.query(Conversation)
                .filter(Conversation.lead_id == lead.id, Conversation.status.in_(["initiated", "active", "booking"]))
                .first()
            )
            if not conversation:
                conversation = Conversation(
                    tenant_id=lead.tenant_id,
                    lead_id=lead.id,
                    status="active",
                )
                db.add(conversation)
                db.flush()

            outbound = Message(
                conversation_id=conversation.id,
                tenant_id=lead.tenant_id,
                sender="ai",
                content=message,
                message_type="sms",
                provider=result.get("provider") or communication_service.provider_name,
                provider_message_sid=result.get("message_sid"),
                delivery_status=result.get("status"),
                msg_metadata={
                    (result.get("provider") or communication_service.provider_name): {
                        "message_sid": result.get("message_sid"),
                        "status": result.get("status"),
                        "campaign_id": campaign_id,
                    }
                },
            )
            db.add(outbound)
            conversation.message_count += 1
            conversation.last_message_at = datetime.now(timezone.utc)
            conversation.last_message_from = "ai"
            db.commit()

            # Realtime: push outbound to the inbox/dashboard live (cross-process
            # via the Socket.IO Redis manager).
            _emit_realtime(str(lead.tenant_id), "conversation_message_created", {
                "conversation_id": str(conversation.id),
                "lead_id": str(lead.id),
                "sender": "ai",
                "content": message,
                "message_type": "sms",
                "last_message_from": "ai",
            })
            _emit_realtime(str(lead.tenant_id), "lead_updated", {"lead_id": str(lead.id), "status": lead.status})

        log_ai_action(
            tenant_id=tenant_id,
            action="sms_sent",
            resource_type="lead",
            resource_id=lead_id,
            details={
                "campaign_id": campaign_id,
                "success": result.get("success"),
                "provider": result.get("provider"),
                "message_sid": result.get("message_sid"),
            },
        )

        return result
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="workers.tasks.sms.send_sms",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_sms(self, lead_id: str, message: str, tenant_id: str, campaign_id: str = None) -> Dict:
    """
    Send SMS to a lead.

    Retries up to 3 times with exponential backoff.
    """
    try:
        return _send_sms_impl(lead_id, message, tenant_id, campaign_id)
    except Exception as exc:
        logger.error(f"SMS send failed: {exc}")
        # Retry with exponential backoff
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks.sms.process_sms_queue",
    bind=True,
)
def process_sms_queue(self) -> Dict:
    """
    Process outbound SMS queue.

    Dequeues and sends SMS messages.
    """
    from app.core.redis import redis_service
    from app.ai.services.prompts import resolve_first_message

    processed = 0
    failed = 0

    for _ in range(50):  # Process up to 50 messages per run
        raw_job = redis_service.client.lpop("queue:outbound_sms")
        if not raw_job:
            break
        if isinstance(raw_job, bytes):
            raw_job = raw_job.decode("utf-8")

        try:
            job = json.loads(raw_job)
            message = resolve_first_message(job)
            result = _send_sms_impl(
                lead_id=job["lead_id"],
                message=message,
                tenant_id=job["tenant_id"],
                campaign_id=job.get("campaign_id"),
                kind=job.get("kind", "other"),
            )

            if result.get("success"):
                processed += 1
            else:
                job["last_error"] = result.get("error", "Unknown error")
                job["attempts"] = int(job.get("attempts") or 0) + 1
                redis_service.client.rpush("queue:retries", json.dumps(job))
                failed += 1

        except Exception as exc:
            try:
                job = json.loads(raw_job)
                job["last_error"] = str(exc)
                job["attempts"] = int(job.get("attempts") or 0) + 1
                redis_service.client.rpush("queue:retries", json.dumps(job))
            except Exception:
                redis_service.client.rpush("queue:dead_letter", raw_job)
            failed += 1

    return {"processed": processed, "failed": failed}


@celery_app.task(
    name="workers.tasks.sms.poll_provider_replies",
    bind=True,
)
def poll_provider_replies(self) -> Dict:
    """
    Poll Sinch Engage replies and process customer responses.

    This is the no-webhook fallback path. Replies are confirmed with the
    provider only after they have been persisted and processed successfully.
    """
    from app.ai.services.reply_polling import poll_provider_replies_once

    db = next(get_db())
    try:
        return poll_provider_replies_once(db)
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.sms.poll_engage2_replies",
    bind=True,
)
def poll_engage2_replies(self) -> Dict:
    """Poll the SECOND lead-SMS provider (ENGAGE2) for replies and route them into the
    SAME lead pipeline as Sinch. No-op until ENGAGE2_* is configured; independent of
    Sinch (its own account + provider-namespaced reply locks)."""
    from app.ai.services.sms_providers import is_configured
    if not is_configured("engage2"):
        return {"processed": 0, "skipped": "engage2_not_configured"}
    from app.ai.services.reply_polling import poll_provider_replies_once
    from app.ai.services.communication_provider import engage2_service

    db = next(get_db())
    try:
        return poll_provider_replies_once(db, service=engage2_service)
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.sms.poll_applicant_replies",
    bind=True,
)
def poll_applicant_replies(self) -> Dict:
    """Poll the DEDICATED hiree (applicant) account's replies and file them into the
    applicant inbox. Separate account/credentials from leads; no-op when the hiree
    provider isn't configured. The lead first-template lockdown is never involved.
    """
    from app.applicant_inbox.reply_polling import poll_applicant_replies_once

    db = next(get_db())
    try:
        return poll_applicant_replies_once(db)
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.sms.ingest_positive_leads",
    bind=True,
)
def ingest_positive_leads(self) -> Dict:
    """Auto-sync: pull positive-intent inbound leads into the SMS human queue.

    Runs per active tenant. The AI keeps handling every lead independently;
    this only mirrors the positive ones into sms_leads for human agents.
    """
    from datetime import timedelta

    from sqlalchemy import distinct

    from app.core.config import settings
    from app.models.message import Message
    from app.sms_queue.services.lead_ingest import ingest_inbound_leads

    if not getattr(settings, "SMS_QUEUE_AUTOSYNC_ENABLED", True):
        return {"skipped": True}

    db = next(get_db())
    try:
        # Discover tenants with inbound over a WIDE window (24h, not 1h) so a
        # tenant that got a reply spike but has since gone quiet is still picked
        # up and its un-pooled backlog gets drained.
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        tenant_ids = [
            str(row[0])
            for row in db.query(distinct(Message.tenant_id))
            .filter(Message.sender == "customer", Message.created_at >= since)
            .all()
        ]
        total = 0
        for tid in tenant_ids:
            # since_hours=72 + a high cap so any un-pooled reply within ~3 days is
            # recovered; ingest_inbound_leads now pages through the backlog.
            res = ingest_inbound_leads(db, tid, since_hours=72, limit=1000)
            ingested = res.get("ingested", 0)
            total += ingested
            if ingested:
                _emit_realtime(tid, "sms:queue_updated", {"reason": "autosync"})

        # Heartbeat: log this tick as a poll so SMS Monitoring shows a regular
        # spike (~every 60s) and real polling-success stats, like Gamified.
        try:
            from app.models.sms import SmsPollLog

            db.add(SmsPollLog(succeeded=True, messages_pulled=total))
            db.commit()
            for tid in tenant_ids:
                _emit_realtime(tid, "sms:queue_updated", {"reason": "autosync_tick"})
        except Exception as exc:  # never let the heartbeat break ingest
            logger.warning(f"autosync heartbeat poll-log failed: {exc}")

        return {"tenants": len(tenant_ids), "ingested": total}
    finally:
        db.close()


@celery_app.task(
    name="workers.tasks.sms.auto_assign_queue",
    bind=True,
)
def auto_assign_queue(self) -> Dict:
    """Continuously hand QUEUED leads to AVAILABLE idle agents.

    Assignment otherwise only fires on event triggers (agent join / break-end /
    disposition, or the realtime inbound mirror). A lead that enters the queue
    via the 60s batch ingest — or arrives while every agent is momentarily busy —
    has no trigger and sits QUEUED indefinitely even when agents are free. This
    sweep guarantees every queued lead reaches an available agent within ~10s and
    pushes the assignment popup to that agent.
    """
    from sqlalchemy import distinct

    from app.models.sms import SmsLead
    from app.sms_queue.services import queue_service
    from app.sms_queue.services.inbound_sync import _flush_events

    # An offer the agent never accepts (closed tab / missed popup / walked away)
    # is reclaimed after this many seconds and re-offered to someone else, so a
    # lead never sits stuck on a "Waiting to accept" agent. Gives a real agent
    # ample time to click Accept first.
    STALE_OFFER_SECONDS = 90

    db = next(get_db())
    try:
        from app.models.sms import SmsQueueAgent

        # Tenants needing attention: those with queued/assigned leads OR any agent
        # whose presence may have gone stale (closed laptop) and must be reaped.
        tenant_ids = set(
            str(row[0])
            for row in db.query(distinct(SmsLead.tenant_id))
            .filter(SmsLead.status.in_(("QUEUED", "ASSIGNED")))
            .all()
        )
        tenant_ids |= set(
            str(row[0])
            for row in db.query(distinct(SmsQueueAgent.tenant_id))
            .filter(SmsQueueAgent.status.in_(("AVAILABLE", "ON_CALL", "AWAY")))
            .all()
        )
        total = 0
        for tid in tenant_ids:
            events = []
            try:
                # Reap agents whose UI stopped heartbeating (closed laptop/tab):
                # take them OFFLINE and release whatever lead they were holding.
                _d, ev = queue_service.reap_stale_agents(db, tid)
                events += ev
                # Reclaim stale offers (also re-distributes everything queued).
                _d, ev = queue_service.rebroadcast(db, tid, stale_seconds=STALE_OFFER_SECONDS)
                events += ev
                # Belt-and-suspenders: assign any remaining queued leads.
                _d, ev = queue_service.distribute_all(db, tid)
                events += ev
            except Exception as exc:
                db.rollback()
                logger.warning(f"auto_assign_queue failed for tenant {tid}: {exc}")
                continue
            if events:
                _flush_events(events)  # push sms:lead_assigned to each agent room
                total += sum(1 for e in events if e.get("event") == "sms:lead_assigned")
        return {"tenants": len(tenant_ids), "assigned": total}
    finally:
        db.close()
