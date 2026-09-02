"""
AI Inference Optimization (Step 26.2)

Optimizes AI inference for performance and cost.

Features:
- Request batching
- Response caching
- Prompt compression
- Model selection
- GPU utilization tracking
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from app.core.redis import RedisService
from app.core.config import settings


class InferenceOptimizer:
    """Optimizes AI inference performance."""

    def __init__(self):
        self.redis = RedisService()
        self._request_queue = []
        self._batch_size = 10
        self._batch_timeout = 0.1  # 100ms

    def get_cached_response(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """
        Get cached AI response.

        Returns cached response if available and not expired.
        """
        cache_key = self._generate_cache_key(prompt, model, temperature)
        return self.redis.get_cache(cache_key)

    def cache_response(
        self,
        prompt: str,
        model: str,
        response: str,
        temperature: float = 0.7,
        ttl: int = 3600,
    ) -> None:
        """
        Cache AI response.
        """
        cache_key = self._generate_cache_key(prompt, model, temperature)
        self.redis.set_cache(cache_key, response, ttl=ttl)

    def _generate_cache_key(
        self,
        prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        """Generate cache key for prompt."""
        content = f"{model}:{temperature}:{prompt}"
        return f"ai_cache:{hashlib.md5(content.encode()).hexdigest()}"

    def select_optimal_model(
        self,
        task_type: str,
        complexity: str = "medium",
    ) -> str:
        """
        Select optimal model for task.

        Args:
            task_type: Type of task (intent, response, summary, objection)
            complexity: Task complexity (low, medium, high)

        Returns:
            Model name
        """
        # Model selection strategy
        model_map = {
            "intent": {
                "low": "mistral",  # Fast for simple intent
                "medium": "mistral",
                "high": "llama3",
            },
            "response": {
                "low": "mistral",
                "medium": "llama3",
                "high": "llama3",
            },
            "summary": {
                "low": "mistral",
                "medium": "mistral",
                "high": "llama3",
            },
            "objection": {
                "low": "mistral",
                "medium": "llama3",
                "high": "llama3",
            },
        }

        return model_map.get(task_type, {}).get(complexity, settings.OLLAMA_MODEL)

    def compress_prompt(
        self,
        system_prompt: str,
        messages: List[Dict],
        max_tokens: int = 2000,
    ) -> Tuple[str, List[Dict]]:
        """
        Compress prompt to reduce token usage.

        Args:
            system_prompt: System prompt
            messages: Conversation messages
            max_tokens: Maximum tokens

        Returns:
            Tuple of (compressed_system, compressed_messages)
        """
        # Estimate current tokens
        current_tokens = self._estimate_tokens(system_prompt, messages)

        if current_tokens <= max_tokens:
            return system_prompt, messages

        # Strategy: Keep system prompt, truncate older messages
        system_tokens = len(system_prompt) // 4
        available_tokens = max_tokens - system_tokens

        # Keep most recent messages
        compressed = []
        used_tokens = 0

        for msg in reversed(messages):
            msg_tokens = len(msg.get("content", "")) // 4
            if used_tokens + msg_tokens > available_tokens:
                break
            compressed.insert(0, msg)
            used_tokens += msg_tokens

        return system_prompt, compressed

    def _estimate_tokens(self, system: str, messages: List[Dict]) -> int:
        """Estimate total token count."""
        total = len(system) // 4
        for msg in messages:
            total += len(msg.get("content", "")) // 4
            total += 4  # Message overhead
        return total

    def batch_requests(
        self,
        requests: List[Dict],
        batch_size: int = 10,
    ) -> List[List[Dict]]:
        """
        Batch requests for processing.

        Args:
            requests: List of requests
            batch_size: Size of each batch

        Returns:
            List of batches
        """
        batches = []
        for i in range(0, len(requests), batch_size):
            batches.append(requests[i:i + batch_size])
        return batches

    def track_inference(
        self,
        model: str,
        task_type: str,
        duration: float,
        tokens: int,
        cached: bool = False,
    ) -> None:
        """
        Track inference metrics.
        """
        # Increment counters
        self.redis.client.incr(f"ai:inference:{model}:total")
        self.redis.client.incr(f"ai:inference:{model}:{task_type}")

        if cached:
            self.redis.client.incr(f"ai:inference:{model}:cached")

        # Track duration
        self.redis.client.lpush(
            f"ai:inference:{model}:durations",
            duration
        )
        self.redis.client.ltrim(f"ai:inference:{model}:durations", 0, 999)

        # Track tokens
        self.redis.client.incrby(f"ai:inference:{model}:tokens", tokens)

    def get_inference_stats(self, model: str) -> Dict:
        """
        Get inference statistics for a model.
        """
        total = int(self.redis.client.get(f"ai:inference:{model}:total") or 0)
        cached = int(self.redis.client.get(f"ai:inference:{model}:cached") or 0)
        tokens = int(self.redis.client.get(f"ai:inference:{model}:tokens") or 0)

        durations = self.redis.client.lrange(f"ai:inference:{model}:durations", 0, -1)
        durations = [float(d) for d in durations] if durations else []

        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "model": model,
            "total_requests": total,
            "cached_requests": cached,
            "cache_hit_rate": cached / total if total > 0 else 0,
            "total_tokens": tokens,
            "avg_duration_ms": avg_duration * 1000,
            "p95_duration_ms": self._percentile(durations, 0.95) * 1000 if durations else 0,
        }

    def _percentile(self, data: List[float], percentile: float) -> float:
        """Calculate percentile."""
        if not data:
            return 0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile)
        return sorted_data[min(index, len(sorted_data) - 1)]


class BatchProcessor:
    """Processes AI requests in batches."""

    def __init__(self, optimizer: InferenceOptimizer):
        self.optimizer = optimizer
        self._pending_requests = []
        self._results = {}

    async def add_request(
        self,
        request_id: str,
        prompt: str,
        model: str,
        **kwargs,
    ) -> str:
        """
        Add request to batch queue.

        Returns request_id for tracking.
        """
        self._pending_requests.append({
            "id": request_id,
            "prompt": prompt,
            "model": model,
            "kwargs": kwargs,
        })

        return request_id

    async def process_batch(self) -> Dict[str, str]:
        """
        Process all pending requests in batch.

        Returns dict of request_id -> response.
        """
        if not self._pending_requests:
            return {}

        # Group by model
        by_model = defaultdict(list)
        for req in self._pending_requests:
            by_model[req["model"]].append(req)

        results = {}

        # Process each model's batch
        for model, requests in by_model.items():
            # Check cache first
            uncached = []
            for req in requests:
                cached = self.optimizer.get_cached_response(
                    req["prompt"],
                    model,
                    req["kwargs"].get("temperature", 0.7),
                )
                if cached:
                    results[req["id"]] = cached
                else:
                    uncached.append(req)

            # Process uncached requests
            if uncached:
                # TODO: Implement actual batch inference
                # For now, process individually
                for req in uncached:
                    # This would call the actual AI model
                    results[req["id"]] = None

        self._pending_requests = []
        return results

    def get_result(self, request_id: str) -> Optional[str]:
        """Get result for a request."""
        return self._results.get(request_id)


class ModelSelector:
    """Selects optimal model based on task and performance."""

    def __init__(self):
        self.redis = RedisService()

    def select_model(
        self,
        task_type: str,
        max_latency_ms: float = 5000,
        min_accuracy: float = 0.8,
    ) -> str:
        """
        Select model based on requirements.

        Args:
            task_type: Type of task
            max_latency_ms: Maximum acceptable latency
            min_accuracy: Minimum required accuracy

        Returns:
            Model name
        """
        # Get model stats
        models = ["llama3", "mistral", "deepseek-llm"]
        best_model = settings.OLLAMA_MODEL
        best_score = 0

        for model in models:
            stats = self._get_model_stats(model)

            # Calculate score based on latency and accuracy
            latency_score = 1.0 - min(stats["avg_latency_ms"] / max_latency_ms, 1.0)
            accuracy_score = stats.get("accuracy", 0.8)

            # Weighted score
            score = (latency_score * 0.4) + (accuracy_score * 0.6)

            if score > best_score and stats["avg_latency_ms"] <= max_latency_ms:
                best_score = score
                best_model = model

        return best_model

    def _get_model_stats(self, model: str) -> Dict:
        """Get model performance stats."""
        cached = self.redis.get_cache(f"model_stats:{model}")
        if cached:
            return cached

        # Default stats
        return {
            "avg_latency_ms": 2000,
            "accuracy": 0.85,
            "total_requests": 0,
        }


# Global instances
inference_optimizer = InferenceOptimizer()
batch_processor = BatchProcessor(inference_optimizer)
model_selector = ModelSelector()
