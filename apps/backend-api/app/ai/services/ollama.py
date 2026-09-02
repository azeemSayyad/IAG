"""
Ollama Integration (Step 4.2)

Provides LLM capabilities via Ollama for:
- Intent detection
- Response generation
- Objection handling
- Sentiment analysis

Supports multiple models with fallback:
- Llama 3 (primary)
- Mistral (fallback)
- DeepSeek (fallback)
"""

import httpx
import json
from typing import List, Dict, Optional

from app.core.config import settings


# Model priority order
MODELS = [
    settings.OLLAMA_MODEL,  # Primary (llama3)
    "mistral",              # Fallback 1
    "deepseek-llm",         # Fallback 2
]


class OllamaClient:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or settings.OLLAMA_BASE_URL
        # CPU inference (Railway has no GPU) is slow; give a 3B-class model room
        # to finish. Tunable via OLLAMA_TIMEOUT_SECONDS.
        self.timeout = float(getattr(settings, "OLLAMA_TIMEOUT_SECONDS", 0) or 90.0)

    async def generate(
        self,
        prompt: str,
        model: str = None,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: The user prompt
            model: Model to use (defaults to settings.OLLAMA_MODEL)
            system: System prompt
            temperature: Response creativity (0-1)
            max_tokens: Maximum response length

        Returns:
            Generated text response
        """
        model = model or settings.OLLAMA_MODEL
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
                result = response.json()
                return result.get("response", "").strip()
        except httpx.TimeoutException:
            # Try fallback models
            return await self._try_fallbacks(prompt, system, temperature, max_tokens)
        except Exception as e:
            raise RuntimeError(f"Ollama error: {str(e)}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
    ) -> str:
        """
        Chat completion with the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            temperature: Response creativity
            max_tokens: Maximum response length

        Returns:
            Assistant's response text
        """
        model = model or settings.OLLAMA_MODEL
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                result = response.json()
                message = result.get("message", {})
                return message.get("content", "").strip()
        except httpx.TimeoutException:
            return await self._try_fallbacks_chat(messages, temperature, max_tokens)
        except Exception as e:
            raise RuntimeError(f"Ollama chat error: {str(e)}")

    async def _try_fallbacks(
        self, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> str:
        """Try fallback models if primary fails."""
        for fallback_model in MODELS[1:]:
            try:
                return await self.generate(
                    prompt=prompt,
                    model=fallback_model,
                    system=system,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception:
                continue
        return "I'm having trouble processing that right now. Let me connect you with an agent."

    async def _try_fallbacks_chat(
        self, messages: List[Dict], temperature: float, max_tokens: int
    ) -> str:
        """Try fallback models for chat if primary fails."""
        for fallback_model in MODELS[1:]:
            try:
                return await self.chat(
                    messages=messages,
                    model=fallback_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception:
                continue
        return "I'm having trouble processing that right now. Let me connect you with an agent."

    async def is_available(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """List available models."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                result = response.json()
                return [m["name"] for m in result.get("models", [])]
        except Exception:
            return []


# Singleton instance
ollama_client = OllamaClient()
