"""
AI Worker Tasks

Processes AI generation queue.
"""

import asyncio
import logging
from typing import Dict

from workers.celery_app import celery_app
from app.core.database import get_db
from app.core.audit import log_ai_action

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.tasks.ai.generate_response",
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=60,
)
def generate_response(
    self,
    conversation_id: str,
    message: str,
    tenant_id: str,
    tone: str = "friendly",
) -> Dict:
    """
    Generate AI response for a conversation.

    Uses Ollama with fallback models.
    """
    try:
        logger.info(f"Generating response for conversation {conversation_id}")

        from app.ai.services.ollama import OllamaClient
        from app.ai.services.prompts import build_llm_prompt
        from app.ai.services.hallucination_guard import validate_response
        from app.ai.services.state_machine import transition_by_intent
        from app.models.conversation import Conversation

        db = next(get_db())
        try:
            conversation = db.query(Conversation).filter(
                Conversation.id == conversation_id
            ).first()

            if not conversation:
                return {"success": False, "error": "Conversation not found"}

            # Build prompt with context
            context = conversation.ai_context or {}
            prompt = build_llm_prompt(
                message=message,
                conversation_history=context.get("message_history", []),
                tone=tone,
                context=context,
            )

            # Generate response (async Ollama client in sync Celery task)
            ollama = OllamaClient()
            response = asyncio.run(ollama.generate(
                prompt=prompt,
                system=f"You are a {tone} insurance agent.",
                temperature=0.7,
                max_tokens=500,
            ))

            # Validate response
            validation = validate_response(
                response=response,
                allowed_pricing=context.get("pricing"),
                expected_tone=tone,
            )

            safe_response = validation["safe_response"]

            # Log action
            log_ai_action(
                tenant_id=tenant_id,
                action="ai_response_generated",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "original_length": len(response),
                    "safe_length": len(safe_response),
                    "was_replaced": validation["replaced"],
                    "violations": validation["violations"],
                },
            )

            return {
                "success": True,
                "response": safe_response,
                "validation": validation,
            }

        finally:
            db.close()

    except Exception as exc:
        logger.error(f"AI generation failed: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks.ai.process_ai_queue",
    bind=True,
)
def process_ai_queue(self) -> Dict:
    """
    Process AI generation queue.
    """
    from app.core.queues import queue_manager, QueueType

    processed = 0
    failed = 0

    for _ in range(20):  # Process up to 20 per run
        job = queue_manager.dequeue(QueueType.AI_GENERATION, timeout=1)
        if not job:
            break

        try:
            result = generate_response(
                conversation_id=job.payload["conversation_id"],
                message=job.payload["message"],
                tenant_id=job.payload["tenant_id"],
                tone=job.payload.get("tone", "friendly"),
            )

            if result.get("success"):
                queue_manager.complete_job(job)
                processed += 1
            else:
                queue_manager.fail_job(job, result.get("error", "Unknown error"))
                failed += 1

        except Exception as exc:
            queue_manager.fail_job(job, str(exc))
            failed += 1

    return {"processed": processed, "failed": failed}
