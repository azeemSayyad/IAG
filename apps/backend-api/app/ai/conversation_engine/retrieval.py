"""
RAG Pipeline (Step 36.4)

Retrieval-Augmented Generation pipeline:
1. Embedding generation for documents and conversations
2. Vector storage via Qdrant
3. Semantic retrieval for context injection

Components:
- EmbeddingGenerator — generates embeddings from text
- RetrievalService — searches vector store for relevant context
- RAGPipeline — orchestrates embedding + retrieval + context injection
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from app.analytics.vector_search import QdrantClient
from app.core.config import settings

logger = logging.getLogger(__name__)

# Embedding dimensions for different models
EMBEDDING_DIMENSIONS = {
    "all-MiniLM-L6-v2": 384,
    "nomic-embed-text": 768,
    "text-embedding-ada-002": 1536,
    "default": 384,
}

# Default collection names
COLLECTIONS = {
    "conversations": "conversation_embeddings",
    "objections": "objection_embeddings",
    "scripts": "sales_script_embeddings",
    "knowledge": "knowledge_base",
}


class EmbeddingGenerator:
    """
    Generates embeddings from text.

    Uses Ollama's embedding endpoint or falls back to
    simple TF-IDF-like embeddings for local development.
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        self.model = model
        self.dimension = EMBEDDING_DIMENSIONS.get(model, 384)

    async def generate(self, text: str) -> List[float]:
        """
        Generate embedding vector for a text string.

        Tries Ollama embedding endpoint first.
        Falls back to simple hash-based embedding for development.
        """
        try:
            return await self._generate_ollama(text)
        except Exception as e:
            logger.warning(f"Ollama embedding failed, using fallback: {e}")
            return self._generate_fallback(text)

    async def generate_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        embeddings = []
        for text in texts:
            emb = await self.generate(text)
            embeddings.append(emb)
        return embeddings

    async def _generate_ollama(self, text: str) -> List[float]:
        """Generate embedding using Ollama's embedding endpoint."""
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                json={
                    "model": self.model,
                    "prompt": text,
                },
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("embedding", [])

        raise Exception(f"Ollama embedding failed: {response.status_code}")

    def _generate_fallback(self, text: str) -> List[float]:
        """
        Generate a simple hash-based embedding for development.

        NOT suitable for production semantic search.
        Provides basic deduplication and rough similarity.
        """
        import math

        # Create multiple hash-based features
        words = text.lower().split()
        features = [0.0] * self.dimension

        # Word hash features
        for i, word in enumerate(words[:self.dimension]):
            idx = hash(word) % self.dimension
            features[idx] += 1.0

        # Character n-gram features
        for i in range(len(text) - 2):
            ngram = text[i:i+3].lower()
            idx = hash(ngram) % self.dimension
            features[idx] += 0.5

        # Normalize to unit vector
        magnitude = math.sqrt(sum(f * f for f in features))
        if magnitude > 0:
            features = [f / magnitude for f in features]

        return features


class RetrievalService:
    """
    Searches vector store for relevant context.

    Retrieves:
    - Similar past conversations
    - Relevant objection handling examples
    - Matching sales scripts
    - Knowledge base entries
    """

    def __init__(self, qdrant_client: Optional[QdrantClient] = None):
        self.qdrant = qdrant_client or QdrantClient()
        self.embedding_gen = EmbeddingGenerator()

    async def search_conversations(
        self,
        query: str,
        tenant_id: str,
        limit: int = 5,
        score_threshold: float = 0.7,
    ) -> List[Dict]:
        """Search for similar past conversations."""
        return await self._search(
            collection=COLLECTIONS["conversations"],
            query=query,
            tenant_id=tenant_id,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def search_objections(
        self,
        objection_type: str,
        query: str,
        tenant_id: str,
        limit: int = 3,
    ) -> List[Dict]:
        """Search for successful objection handling examples."""
        return await self._search(
            collection=COLLECTIONS["objections"],
            query=f"{objection_type}: {query}",
            tenant_id=tenant_id,
            limit=limit,
            score_threshold=0.6,
        )

    async def search_scripts(
        self,
        query: str,
        tenant_id: str,
        limit: int = 3,
    ) -> List[Dict]:
        """Search for relevant sales scripts."""
        return await self._search(
            collection=COLLECTIONS["scripts"],
            query=query,
            tenant_id=tenant_id,
            limit=limit,
            score_threshold=0.6,
        )

    async def search_knowledge(
        self,
        query: str,
        tenant_id: str,
        limit: int = 5,
    ) -> List[Dict]:
        """Search the knowledge base."""
        return await self._search(
            collection=COLLECTIONS["knowledge"],
            query=query,
            tenant_id=tenant_id,
            limit=limit,
            score_threshold=0.5,
        )

    async def store_conversation(
        self,
        conversation_id: str,
        text: str,
        metadata: Dict,
        tenant_id: str,
    ) -> bool:
        """Store a conversation embedding."""
        return await self._store(
            collection=COLLECTIONS["conversations"],
            doc_id=f"conv_{conversation_id}",
            text=text,
            metadata={**metadata, "tenant_id": tenant_id},
        )

    async def store_objection(
        self,
        objection_type: str,
        text: str,
        response: str,
        was_successful: bool,
        tenant_id: str,
    ) -> bool:
        """Store an objection handling example."""
        doc_text = f"Objection ({objection_type}): {text}\nResponse: {response}"
        doc_id = f"obj_{hashlib.md5(doc_text.encode()).hexdigest()[:12]}"

        return await self._store(
            collection=COLLECTIONS["objections"],
            doc_id=doc_id,
            text=doc_text,
            metadata={
                "objection_type": objection_type,
                "was_successful": was_successful,
                "tenant_id": tenant_id,
            },
        )

    async def store_script(
        self,
        script_id: str,
        text: str,
        metadata: Dict,
        tenant_id: str,
    ) -> bool:
        """Store a sales script."""
        return await self._store(
            collection=COLLECTIONS["scripts"],
            doc_id=f"script_{script_id}",
            text=text,
            metadata={**metadata, "tenant_id": tenant_id},
        )

    async def _search(
        self,
        collection: str,
        query: str,
        tenant_id: str,
        limit: int = 5,
        score_threshold: float = 0.7,
    ) -> List[Dict]:
        """Search a collection for similar documents."""
        try:
            query_embedding = await self.embedding_gen.generate(query)

            results = await self.qdrant.search(
                collection_name=collection,
                query_vector=query_embedding,
                limit=limit,
                score_threshold=score_threshold,
                filter_conditions={"tenant_id": tenant_id},
            )

            return results or []

        except Exception as e:
            logger.warning(f"Vector search failed for {collection}: {e}")
            return []

    async def _store(
        self,
        collection: str,
        doc_id: str,
        text: str,
        metadata: Dict,
    ) -> bool:
        """Store a document embedding."""
        try:
            embedding = await self.embedding_gen.generate(text)

            return await self.qdrant.upsert_vector(
                collection_name=collection,
                vector_id=doc_id,
                vector=embedding,
                payload={**metadata, "text": text[:1000]},
            )

        except Exception as e:
            logger.warning(f"Vector store failed for {collection}: {e}")
            return False


class RAGPipeline:
    """
    Orchestrates RAG: retrieval + context injection.

    Used by the conversation engine to enhance LLM prompts
    with relevant retrieved context.
    """

    def __init__(self):
        self.retrieval = RetrievalService()

    async def enhance_context(
        self,
        query: str,
        tenant_id: str,
        objection_type: Optional[str] = None,
    ) -> str:
        """
        Retrieve relevant context and format for prompt injection.

        Args:
            query: The current message or topic
            tenant_id: Tenant ID for scoping
            objection_type: Type of objection (if any)

        Returns:
            Formatted context string for LLM prompt
        """
        context_parts = []

        # Search for relevant conversations
        conv_results = await self.retrieval.search_conversations(
            query=query, tenant_id=tenant_id, limit=3
        )
        if conv_results:
            context_parts.append("RELEVANT PAST CONVERSATIONS:")
            for r in conv_results[:2]:
                text = r.get("text", "")[:200]
                context_parts.append(f"- {text}")

        # Search for objection handling if applicable
        if objection_type:
            obj_results = await self.retrieval.search_objections(
                objection_type=objection_type,
                query=query,
                tenant_id=tenant_id,
                limit=2,
            )
            if obj_results:
                context_parts.append(f"\nSUCCESSFUL {objection_type.upper()} OBJECTION HANDLING:")
                for r in obj_results[:2]:
                    text = r.get("text", "")[:200]
                    context_parts.append(f"- {text}")

        # Search knowledge base
        kb_results = await self.retrieval.search_knowledge(
            query=query, tenant_id=tenant_id, limit=2
        )
        if kb_results:
            context_parts.append("\nRELEVANT KNOWLEDGE:")
            for r in kb_results[:2]:
                text = r.get("text", "")[:200]
                context_parts.append(f"- {text}")

        return "\n".join(context_parts) if context_parts else ""

    async def store_interaction(
        self,
        conversation_id: str,
        messages: List[Dict],
        tenant_id: str,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Store a conversation interaction for future retrieval."""
        # Create summary text
        summary_parts = []
        for msg in messages[-10:]:
            prefix = "Customer" if msg.get("sender") == "customer" else "Agent"
            summary_parts.append(f"{prefix}: {msg.get('content', '')[:100]}")

        text = "\n".join(summary_parts)

        return await self.retrieval.store_conversation(
            conversation_id=conversation_id,
            text=text,
            metadata=metadata or {},
            tenant_id=tenant_id,
        )
