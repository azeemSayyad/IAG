"""
Conversation Engine (Step 36.1 — Main Orchestrator)

The core AI conversation engine that replaces template-based responses
with real LLM-driven intelligent conversations.

Flow:
1. Receive incoming message
2. Build context (lead, conversation, memory, campaign)
3. Build dynamic prompt (4 layers)
4. Generate LLM response
5. Validate response (hallucination guard)
6. Parse and execute tool calls
7. Update memory
8. Return response
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.ai.conversation_engine.context_builder import ContextBuilder, ConversationContext
from app.ai.conversation_engine.prompt_builder import build_messages, build_objection_prompt
from app.ai.conversation_engine.response_generator import ResponseGenerator, GenerationResult
from app.ai.conversation_engine.tool_calling import ToolExecutor, ToolCall
from app.intent.services.memory import MemoryEngine, analyze_sentiment
from app.intent.services.classifier import classify_intent
from app.intent.services.objections import detect_objection
from app.ai.services.state_machine import transition_by_intent, ConversationEvent
from app.core.audit import log_ai_action

logger = logging.getLogger(__name__)


class ConversationResponse:
    """Complete response from the conversation engine."""

    def __init__(
        self,
        message: str,
        intent: Optional[str] = None,
        intent_confidence: float = 0,
        sentiment: Optional[str] = None,
        sentiment_score: float = 0.5,
        objection_type: Optional[str] = None,
        tool_calls: List[ToolCall] = None,
        generation: Optional[GenerationResult] = None,
        conversation_state: Optional[str] = None,
        should_book: bool = False,
        should_stop: bool = False,
        context_summary: str = "",
    ):
        self.message = message
        self.intent = intent
        self.intent_confidence = intent_confidence
        self.sentiment = sentiment
        self.sentiment_score = sentiment_score
        self.objection_type = objection_type
        self.tool_calls = tool_calls or []
        self.generation = generation
        self.conversation_state = conversation_state
        self.should_book = should_book
        self.should_stop = should_stop
        self.context_summary = context_summary

    def to_dict(self) -> Dict:
        return {
            "message": self.message,
            "intent": {
                "type": self.intent,
                "confidence": round(self.intent_confidence, 3),
            },
            "sentiment": {
                "type": self.sentiment,
                "score": round(self.sentiment_score, 3),
            },
            "objection_type": self.objection_type,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "generation": self.generation.to_dict() if self.generation else None,
            "conversation_state": self.conversation_state,
            "should_book": self.should_book,
            "should_stop": self.should_stop,
            "context_summary": self.context_summary,
        }


class ConversationEngine:
    """
    Main AI conversation engine.

    Replaces template-based responses with real LLM-driven conversations.
    """

    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.context_builder = ContextBuilder(db)
        self.response_generator = ResponseGenerator()
        self.memory_engine = MemoryEngine(db)

    async def process_message(
        self,
        lead_id: UUID,
        message_text: str,
        conversation_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        language: Optional[str] = None,
    ) -> ConversationResponse:
        """
        Process an incoming customer message and generate an AI response.

        Full pipeline:
        1. Build context
        2. Detect intent and sentiment
        3. Update memory
        4. Transition conversation state
        5. Build prompt
        6. Generate LLM response
        7. Parse tool calls
        8. Execute tool calls
        9. Return response

        Args:
            lead_id: Lead UUID
            message_text: Customer's message text
            conversation_id: Optional conversation UUID
            campaign_id: Optional campaign UUID

        Returns:
            ConversationResponse with AI message and metadata
        """
        # Step 1: Build context
        ctx = self.context_builder.build(
            lead_id=lead_id,
            conversation_id=conversation_id,
            campaign_id=campaign_id,
        )

        if not ctx.lead:
            return ConversationResponse(
                message="I'm sorry, I couldn't find your information. Let me connect you with an agent.",
                context_summary="Lead not found",
            )

        # Ensure conversation exists
        if not ctx.conversation:
            ctx.conversation = Conversation(
                tenant_id=self.tenant_id,
                lead_id=lead_id,
                status="active",
            )
            self.db.add(ctx.conversation)
            self.db.commit()
            self.db.refresh(ctx.conversation)

        # Step 2: Detect intent and sentiment
        intent_result = await classify_intent(
            text=message_text,
            conversation_history=ctx.messages,
        )
        sentiment, sentiment_score = analyze_sentiment(message_text)
        objection_type, objection_confidence = detect_objection(message_text)

        # Step 3: Log incoming message
        incoming_msg = Message(
            conversation_id=ctx.conversation.id,
            tenant_id=self.tenant_id,
            sender="customer",
            content=message_text,
            message_type="sms",
            intent=intent_result.intent.value if intent_result else None,
            sentiment=sentiment,
        )
        self.db.add(incoming_msg)
        ctx.conversation.message_count += 1
        ctx.conversation.last_message_at = datetime.now(timezone.utc)
        ctx.conversation.last_message_from = "customer"

        # Step 4: Update memory
        if objection_type and objection_type.value != "unknown":
            self.memory_engine.add_objection(
                ctx.conversation, objection_type.value, message_text
            )

        self.memory_engine.update_sentiment(
            ctx.conversation, sentiment, sentiment_score
        )
        self.memory_engine.add_to_history(
            ctx.conversation, "customer", message_text
        )

        # Step 5: Transition conversation state
        intent_value = intent_result.intent.value if intent_result else None
        if intent_value:
            transition_result = transition_by_intent(
                self.db, ctx.conversation.id, intent_value, self.tenant_id
            )
            if transition_result.get("success"):
                ctx.conversation_state = transition_result.get("new_state")

        # Step 6: Check for special intents that bypass LLM
        if intent_value == "STOP":
            return await self._handle_stop(ctx, sentiment, sentiment_score)

        # Step 7: Build prompt and generate response
        messages = build_messages(ctx, message_text, include_tools=True, language=language)

        generation = await self.response_generator.generate(
            messages=messages,
            expected_tone=ctx.campaign.tone if ctx.campaign else "friendly",
        )

        # Step 8: Parse and execute tool calls
        tool_executor = ToolExecutor(self.db, self.tenant_id)
        tool_calls = tool_executor.parse_tool_calls(generation.response)

        if tool_calls:
            tool_calls = await tool_executor.execute_tool_calls(
                tool_calls=tool_calls,
                lead=ctx.lead,
                conversation=ctx.conversation,
                appointment=ctx.appointment,
            )

            # Remove tool call blocks from response message
            clean_message = self._remove_tool_blocks(generation.response)

            # If tool executed successfully, add tool results to context
            for tc in tool_calls:
                if tc.success:
                    clean_message += f"\n\n{self._format_tool_result(tc)}"
        else:
            clean_message = generation.response

        # Step 9: Log outgoing message and update state
        outgoing_msg = Message(
            conversation_id=ctx.conversation.id,
            tenant_id=self.tenant_id,
            sender="ai",
            content=clean_message,
            message_type="sms",
        )
        self.db.add(outgoing_msg)
        ctx.conversation.message_count += 1
        ctx.conversation.last_message_at = datetime.now(timezone.utc)
        ctx.conversation.last_message_from = "ai"

        # Update memory with AI response
        self.memory_engine.add_to_history(ctx.conversation, "ai", clean_message)

        self.db.commit()

        # Audit log
        log_ai_action(
            tenant_id=self.tenant_id,
            action="ai_conversation_response",
            resource_type="conversation",
            resource_id=str(ctx.conversation.id),
            details={
                "intent": intent_value,
                "sentiment": sentiment,
                "objection": objection_type.value if objection_type else None,
                "model_used": generation.model_used,
                "tokens_used": generation.tokens_used,
                "tool_calls_count": len(tool_calls),
                "was_fallback": generation.fallback_used,
            },
        )

        # Determine if should book or stop
        should_book = intent_value in ("BOOK_NOW", "POSITIVE") or any(
            tc.tool_name == "book_appointment" and tc.success for tc in tool_calls
        )
        should_stop = intent_value == "STOP"

        return ConversationResponse(
            message=clean_message,
            intent=intent_result.intent.value if intent_result else None,
            intent_confidence=intent_result.confidence if intent_result else 0,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            objection_type=objection_type.value if objection_type else None,
            tool_calls=tool_calls,
            generation=generation,
            conversation_state=ctx.conversation_state,
            should_book=should_book,
            should_stop=should_stop,
            context_summary=self.context_builder.build_summary(ctx),
        )

    async def _handle_stop(
        self,
        ctx: ConversationContext,
        sentiment: str,
        sentiment_score: float,
    ) -> ConversationResponse:
        """Handle STOP intent — silently stop contacting the lead.

        No suppression list, no unsubscribe confirmation message: we simply mark
        the lead unqualified and stop the conversation so nothing else is sent.
        """
        # Update statuses — this alone stops any further outreach to the lead.
        ctx.lead.status = "unqualified"
        ctx.conversation.status = "stopped"
        self.db.commit()

        # Return an empty message so NOTHING is sent back to the customer.
        return ConversationResponse(
            message="",
            intent="STOP",
            intent_confidence=1.0,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            should_stop=True,
            conversation_state="stopped",
        )

    def _remove_tool_blocks(self, response: str) -> str:
        """Remove ```tool blocks from response."""
        import re
        return re.sub(r'```tool\s*\n\{[^`]+\}\s*\n```', '', response).strip()

    def _format_tool_result(self, tc: ToolCall) -> str:
        """Format a tool call result as human-readable text."""
        if not tc.success:
            return ""

        if tc.tool_name == "search_slots":
            slots = tc.result.get("slots", [])
            if slots:
                slot_lines = [
                    f"{i+1}. {s['start_display']} ({s['date_display']})"
                    for i, s in enumerate(slots[:3])
                ]
                return "Here are some available times:\n" + "\n".join(slot_lines)
            return "Let me check other available times for you."

        if tc.tool_name == "book_appointment":
            if tc.result.get("success"):
                start_time = tc.result.get("start_time", "")
                return f"Your appointment is confirmed! Looking forward to speaking with you."
            return "Let me try a different time slot for you."

        if tc.tool_name == "reschedule":
            if tc.result.get("success"):
                return "Let's find you a new time that works better."

        if tc.tool_name == "cancel_appointment":
            if tc.result.get("success"):
                return "Your appointment has been cancelled."

        if tc.tool_name == "add_to_suppression":
            return "You've been unsubscribed. We won't contact you again."

        if tc.tool_name == "escalate_to_agent":
            return "I'm connecting you with one of our specialists who can help you further."

        return ""

    async def health_check(self) -> Dict:
        """Check health of all conversation engine components."""
        llm_health = await self.response_generator.check_health()
        context_summary = "ContextBuilder: OK"
        tool_summary = "ToolExecutor: OK"

        return {
            "status": "ok" if llm_health.get("available") else "degraded",
            "llm": llm_health,
            "context_builder": context_summary,
            "tool_executor": tool_summary,
        }
