"""
Enhanced Retrieval Service (Phase 37.3)

Provides semantic search across all vector collections:
- search_similar_conversations — Find similar past conversations
- search_objections — Find successful objection handling examples
- search_sales_scripts — Find relevant sales scripts
- search_knowledge_base — Search insurance knowledge
- search_lead_history — Find similar lead patterns

Features:
- Tenant-scoped search
- Result ranking and filtering
- Context formatting for LLM injection
- Caching for frequent queries
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.analytics.vector_search import (
    qdrant_client,
    build_tenant_filter,
    build_tenant_type_filter,
)
from app.ai.conversation_engine.retrieval import EmbeddingGenerator

logger = logging.getLogger(__name__)


class SearchResult:
    """A single search result with score and metadata."""

    def __init__(
        self,
        id: str,
        score: float,
        text: str,
        metadata: Dict[str, Any],
    ):
        self.id = id
        self.score = score
        self.text = text
        self.metadata = metadata

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "score": round(self.score, 4),
            "text": self.text[:500],
            "metadata": self.metadata,
        }


class RetrievalService:
    """
    Production retrieval service with semantic search.

    Searches across all vector collections with proper
    tenant scoping, result ranking, and context formatting.
    """

    def __init__(self):
        self.embedding_gen = EmbeddingGenerator()

    # --- Conversation Search ---

    async def search_similar_conversations(
        self,
        query: str,
        tenant_id: str,
        limit: int = 5,
        score_threshold: float = 0.6,
        status_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search for similar past conversations.

        Args:
            query: Search query (customer message or topic)
            tenant_id: Tenant ID for scoping
            limit: Maximum results
            score_threshold: Minimum similarity score
            status_filter: Optional status filter (booked, won, etc.)

        Returns:
            List of SearchResult objects
        """
        embedding = await self.embedding_gen.generate(query)

        filter_conditions = build_tenant_filter(tenant_id)
        if status_filter:
            filter_conditions["must"].append(
                {"key": "status", "match": {"value": status_filter}}
            )

        results = await qdrant_client.search(
            collection_name="conversation_embeddings",
            query_vector=embedding,
            limit=limit,
            score_threshold=score_threshold,
            filter_conditions=filter_conditions,
        )

        return self._parse_results(results)

    async def search_successful_conversations(
        self,
        query: str,
        tenant_id: str,
        limit: int = 3,
    ) -> List[SearchResult]:
        """Search for conversations that led to bookings."""
        return await self.search_similar_conversations(
            query=query,
            tenant_id=tenant_id,
            limit=limit,
            score_threshold=0.5,
            status_filter="booked",
        )

    # --- Objection Search ---

    async def search_objections(
        self,
        objection_type: str,
        query: str,
        tenant_id: str,
        limit: int = 3,
        successful_only: bool = True,
    ) -> List[SearchResult]:
        """
        Search for objection handling examples.

        Args:
            objection_type: Type of objection (pricing, trust, etc.)
            query: Customer's objection text
            tenant_id: Tenant ID for scoping
            limit: Maximum results
            successful_only: Only return successful objection handling

        Returns:
            List of SearchResult objects
        """
        search_text = f"{objection_type}: {query}"
        embedding = await self.embedding_gen.generate(search_text)

        filter_conditions = build_tenant_type_filter(
            tenant_id, objection_type, "objection_type"
        )

        if successful_only:
            filter_conditions["must"].append(
                {"key": "was_successful", "match": {"value": True}}
            )

        results = await qdrant_client.search(
            collection_name="objection_embeddings",
            query_vector=embedding,
            limit=limit,
            score_threshold=0.5,
            filter_conditions=filter_conditions,
        )

        return self._parse_results(results)

    # --- Sales Script Search ---

    async def search_sales_scripts(
        self,
        query: str,
        tenant_id: str,
        script_type: Optional[str] = None,
        tone: Optional[str] = None,
        limit: int = 3,
    ) -> List[SearchResult]:
        """
        Search for relevant sales scripts.

        Args:
            query: Search query
            tenant_id: Tenant ID for scoping
            script_type: Optional type filter (outreach, objection, followup, closing)
            tone: Optional tone filter (friendly, professional, etc.)
            limit: Maximum results

        Returns:
            List of SearchResult objects
        """
        embedding = await self.embedding_gen.generate(query)

        filter_conditions = build_tenant_filter(tenant_id)

        if script_type:
            filter_conditions["must"].append(
                {"key": "script_type", "match": {"value": script_type}}
            )

        if tone:
            filter_conditions["must"].append(
                {"key": "tone", "match": {"value": tone}}
            )

        results = await qdrant_client.search(
            collection_name="sales_script_embeddings",
            query_vector=embedding,
            limit=limit,
            score_threshold=0.5,
            filter_conditions=filter_conditions,
        )

        return self._parse_results(results)

    # --- Knowledge Base Search ---

    async def search_knowledge_base(
        self,
        query: str,
        tenant_id: str,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        """
        Search the insurance knowledge base.

        Args:
            query: Search query
            tenant_id: Tenant ID for scoping
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of SearchResult objects
        """
        embedding = await self.embedding_gen.generate(query)

        filter_conditions = build_tenant_filter(tenant_id)

        if category:
            filter_conditions["must"].append(
                {"key": "category", "match": {"value": category}}
            )

        results = await qdrant_client.search(
            collection_name="knowledge_base",
            query_vector=embedding,
            limit=limit,
            score_threshold=0.4,
            filter_conditions=filter_conditions,
        )

        return self._parse_results(results)

    # --- Combined Search ---

    async def search_all(
        self,
        query: str,
        tenant_id: str,
        include_conversations: bool = True,
        include_objections: bool = True,
        include_scripts: bool = True,
        include_knowledge: bool = True,
        limit_per_collection: int = 3,
    ) -> Dict[str, List[SearchResult]]:
        """
        Search across all collections.

        Returns:
            Dict with results per collection
        """
        results = {}

        if include_conversations:
            results["conversations"] = await self.search_similar_conversations(
                query=query,
                tenant_id=tenant_id,
                limit=limit_per_collection,
            )

        if include_objections:
            results["objections"] = await self.search_objections(
                objection_type="general",
                query=query,
                tenant_id=tenant_id,
                limit=limit_per_collection,
                successful_only=False,
            )

        if include_scripts:
            results["scripts"] = await self.search_sales_scripts(
                query=query,
                tenant_id=tenant_id,
                limit=limit_per_collection,
            )

        if include_knowledge:
            results["knowledge"] = await self.search_knowledge_base(
                query=query,
                tenant_id=tenant_id,
                limit=limit_per_collection,
            )

        return results

    # --- Context Formatting ---

    def format_for_prompt(
        self,
        results: Dict[str, List[SearchResult]],
        max_per_type: int = 2,
    ) -> str:
        """
        Format search results for LLM prompt injection.

        Args:
            results: Dict of results per collection
            max_per_type: Maximum results per type to include

        Returns:
            Formatted string for prompt injection
        """
        parts = []

        # Conversations
        convs = results.get("conversations", [])
        if convs:
            parts.append("SIMILAR PAST CONVERSATIONS:")
            for r in convs[:max_per_type]:
                parts.append(f"- {r.text[:200]}")

        # Objections
        objs = results.get("objections", [])
        if objs:
            parts.append("\nOBJECTION HANDLING EXAMPLES:")
            for r in objs[:max_per_type]:
                parts.append(f"- {r.text[:200]}")

        # Scripts
        scripts = results.get("scripts", [])
        if scripts:
            parts.append("\nRELEVANT SALES SCRIPTS:")
            for r in scripts[:max_per_type]:
                parts.append(f"- {r.text[:200]}")

        # Knowledge
        kb = results.get("knowledge", [])
        if kb:
            parts.append("\nRELEVANT KNOWLEDGE:")
            for r in kb[:max_per_type]:
                parts.append(f"- {r.text[:200]}")

        return "\n".join(parts) if parts else ""

    # --- Helpers ---

    def _parse_results(self, raw_results: List[Dict]) -> List[SearchResult]:
        """Parse raw Qdrant results into SearchResult objects."""
        results = []

        for r in raw_results:
            payload = r.get("payload", {})
            text = payload.get("text", "")

            results.append(SearchResult(
                id=r.get("id", ""),
                score=r.get("score", 0.0),
                text=text,
                metadata=payload,
            ))

        return results


# Singleton
retrieval_service = RetrievalService()
