"""
Response Generator (Step 36.1)

Handles LLM response generation with:
- Multi-model fallback chain
- Token optimization
- Response validation
- Streaming support (future)
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from app.ai.services.ollama import OllamaClient, MODELS
from app.ai.services.hallucination_guard import validate_response
from app.ai.services.token_optimizer import (
    estimate_tokens,
    summarize_conversation,
    compress_messages,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class GenerationResult:
    """Result of LLM generation."""

    def __init__(
        self,
        response: str,
        model_used: str,
        tokens_used: int = 0,
        was_validated: bool = False,
        was_replaced: bool = False,
        violations: List[str] = None,
        fallback_used: bool = False,
        generation_time_ms: float = 0,
    ):
        self.response = response
        self.model_used = model_used
        self.tokens_used = tokens_used
        self.was_validated = was_validated
        self.was_replaced = was_replaced
        self.violations = violations or []
        self.fallback_used = fallback_used
        self.generation_time_ms = generation_time_ms

    def to_dict(self) -> Dict:
        return {
            "response": self.response,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "was_validated": self.was_validated,
            "was_replaced": self.was_replaced,
            "violations": self.violations,
            "fallback_used": self.fallback_used,
            "generation_time_ms": round(self.generation_time_ms, 1),
        }


class ResponseGenerator:
    """Generates AI responses using LLM with fallback chain."""

    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ollama = ollama_client or OllamaClient()
        self.max_tokens = 500
        self.temperature = 0.7
        self.max_context_tokens = 3000

    async def generate(
        self,
        messages: List[Dict[str, str]],
        allowed_pricing: Optional[Dict] = None,
        expected_tone: str = "friendly",
        model: Optional[str] = None,
    ) -> GenerationResult:
        """
        Generate a response using LLM with fallback chain.

        Args:
            messages: List of message dicts with 'role' and 'content'
            allowed_pricing: Allowed pricing values for validation
            expected_tone: Expected tone for validation
            model: Specific model to use (overrides default)

        Returns:
            GenerationResult with response and metadata
        """
        start_time = datetime.now(timezone.utc)
        fallback_used = False

        # Optimize tokens if context is too large
        optimized_messages = self._optimize_context(messages)

        # Try primary model
        try:
            response = await self.ollama.chat(
                messages=optimized_messages,
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if response:
                # Validate response
                validation = validate_response(
                    response=response,
                    allowed_pricing=allowed_pricing,
                    expected_tone=expected_tone,
                )

                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

                return GenerationResult(
                    response=validation["safe_response"],
                    model_used=model or settings.OLLAMA_MODEL,
                    tokens_used=estimate_tokens(response),
                    was_validated=True,
                    was_replaced=validation["replaced"],
                    violations=validation["violations"],
                    fallback_used=False,
                    generation_time_ms=elapsed,
                )

        except Exception as e:
            logger.warning(f"Primary model failed: {e}")
            fallback_used = True

        # Fallback chain
        for fallback_model in MODELS[1:]:
            try:
                logger.info(f"Trying fallback model: {fallback_model}")
                response = await self.ollama.chat(
                    messages=optimized_messages,
                    model=fallback_model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )

                if response:
                    validation = validate_response(
                        response=response,
                        allowed_pricing=allowed_pricing,
                        expected_tone=expected_tone,
                    )

                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

                    return GenerationResult(
                        response=validation["safe_response"],
                        model_used=fallback_model,
                        tokens_used=estimate_tokens(response),
                        was_validated=True,
                        was_replaced=validation["replaced"],
                        violations=validation["violations"],
                        fallback_used=True,
                        generation_time_ms=elapsed,
                    )

            except Exception as e:
                logger.warning(f"Fallback model {fallback_model} failed: {e}")
                continue

        # All models failed — return safe template
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        return GenerationResult(
            response=self._get_safe_fallback(expected_tone),
            model_used="template_fallback",
            tokens_used=0,
            was_validated=False,
            was_replaced=True,
            violations=["all_models_failed"],
            fallback_used=True,
            generation_time_ms=elapsed,
        )

    async def generate_with_prompt(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: List[Dict] = None,
        allowed_pricing: Optional[Dict] = None,
        expected_tone: str = "friendly",
    ) -> GenerationResult:
        """
        Generate response with explicit system prompt and user message.

        Convenience method for simpler use cases.
        """
        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history[-10:]:
                role = "assistant" if msg.get("sender") == "ai" else "user"
                messages.append({"role": role, "content": msg.get("content", "")})

        messages.append({"role": "user", "content": user_message})

        return await self.generate(
            messages=messages,
            allowed_pricing=allowed_pricing,
            expected_tone=expected_tone,
        )

    def _optimize_context(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Optimize context to fit within token limits."""
        total_tokens = estimate_tokens(
            " ".join(m.get("content", "") for m in messages)
        )

        if total_tokens <= self.max_context_tokens:
            return messages

        # Keep system message, compress conversation
        system_messages = [m for m in messages if m.get("role") == "system"]
        conversation_messages = [m for m in messages if m.get("role") != "system"]

        # Compress conversation
        compressed = compress_messages(conversation_messages)

        # Summarize if still too large
        system_content = system_messages[0]["content"] if system_messages else ""
        summarized = summarize_conversation(
            [{"role": "system", "content": system_content}] + compressed,
            max_tokens=self.max_context_tokens,
        )

        return summarized

    def _get_safe_fallback(self, tone: str = "friendly") -> str:
        """Get a safe fallback response when all LLM attempts fail."""
        fallbacks = {
            "friendly": [
                "Thanks for reaching out! I'd love to help you explore our insurance options. When would be a good time for a quick call?",
                "Great question! Let me connect you with one of our specialists who can provide accurate information. What works best for you?",
            ],
            "professional": [
                "Thank you for your inquiry. I'd be happy to arrange a consultation with one of our insurance specialists. Please let me know your availability.",
                "I understand your interest. Our team can provide detailed information about our coverage options. When would be convenient for a call?",
            ],
            "casual": [
                "Hey! Thanks for reaching out. Want to hop on a quick call to chat about your insurance options? Let me know when works!",
                "Thanks for getting in touch! Our team is great at finding the right coverage. When's a good time to talk?",
            ],
            "urgent": [
                "Thank you for your interest. I'd like to connect you with a specialist right away. When can we schedule a call?",
                "I understand you're interested. Our team is available to help immediately. What's the best time to reach you?",
            ],
        }

        import random
        tone_fallbacks = fallbacks.get(tone, fallbacks["friendly"])
        return random.choice(tone_fallbacks)

    async def check_health(self) -> Dict:
        """Check if LLM service is available."""
        try:
            is_available = await self.ollama.is_available()
            models = await self.ollama.list_models() if is_available else []
            return {
                "available": is_available,
                "models": models,
                "base_url": self.ollama.base_url,
            }
        except Exception as e:
            return {
                "available": False,
                "error": str(e),
                "base_url": self.ollama.base_url,
            }
