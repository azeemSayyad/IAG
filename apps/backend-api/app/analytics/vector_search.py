"""
Vector Search Service — Qdrant (Phase 37.1)

Production-grade vector database integration:
- Collection management with proper schemas
- CRUD operations with error handling
- Batch operations for bulk embedding
- Health checks and connection management
- Tenant-scoped search with filters
- Collection initialization on startup

Collections:
- conversation_embeddings — Past conversation vectors
- objection_embeddings — Objection handling examples
- sales_script_embeddings — Sales script vectors
- knowledge_base — Insurance knowledge vectors
"""

import logging
from typing import Dict, List, Optional, Any
from uuid import UUID

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# Collection definitions with schemas
COLLECTION_DEFINITIONS = {
    "conversation_embeddings": {
        "vector_size": 384,
        "distance": "Cosine",
        "description": "Past conversation vectors for similarity search",
        "payload_schema": {
            "tenant_id": "keyword",
            "lead_id": "keyword",
            "conversation_id": "keyword",
            "status": "keyword",
            "sentiment": "keyword",
            "message_count": "integer",
            "objection_types": "keyword",
            "created_at": "keyword",
        },
    },
    "objection_embeddings": {
        "vector_size": 384,
        "distance": "Cosine",
        "description": "Objection handling examples",
        "payload_schema": {
            "tenant_id": "keyword",
            "objection_type": "keyword",
            "was_successful": "boolean",
            "created_at": "keyword",
        },
    },
    "sales_script_embeddings": {
        "vector_size": 384,
        "distance": "Cosine",
        "description": "Sales script vectors",
        "payload_schema": {
            "tenant_id": "keyword",
            "script_type": "keyword",
            "tone": "keyword",
            "campaign_id": "keyword",
            "created_at": "keyword",
        },
    },
    "knowledge_base": {
        "vector_size": 384,
        "distance": "Cosine",
        "description": "Insurance knowledge base vectors",
        "payload_schema": {
            "tenant_id": "keyword",
            "category": "keyword",
            "topic": "keyword",
            "source": "keyword",
            "created_at": "keyword",
        },
    },
}


class QdrantClient:
    """
    Production-grade Qdrant vector database client.

    Features:
    - Connection pooling via httpx.AsyncClient
    - Proper error handling and logging
    - Batch operations
    - Health checks
    - Collection management
    """

    def __init__(self, base_url: str = None):
        self.base_url = base_url or getattr(settings, "QDRANT_URL", "http://qdrant:6333")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        return self._client

    async def close(self):
        """Close the httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # --- Health ---

    async def health_check(self) -> Dict:
        """Check Qdrant health and connectivity."""
        try:
            client = await self._get_client()
            response = await client.get("/")

            if response.status_code == 200:
                # Get collections info
                collections = await self.list_collections()
                return {
                    "status": "healthy",
                    "url": self.base_url,
                    "collections": len(collections),
                    "version": response.json().get("version", "unknown"),
                }
            else:
                return {
                    "status": "unhealthy",
                    "url": self.base_url,
                    "error": f"HTTP {response.status_code}",
                }

        except Exception as e:
            return {
                "status": "unreachable",
                "url": self.base_url,
                "error": str(e),
            }

    # --- Collection Management ---

    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 384,
        distance: str = "Cosine",
    ) -> bool:
        """Create a collection with the given schema."""
        try:
            client = await self._get_client()
            response = await client.put(
                f"/collections/{collection_name}",
                json={
                    "vectors": {
                        "size": vector_size,
                        "distance": distance,
                    },
                },
            )

            if response.status_code in (200, 201):
                logger.info(f"Created collection: {collection_name}")
                return True
            elif response.status_code == 409:
                logger.debug(f"Collection already exists: {collection_name}")
                return True
            else:
                logger.error(f"Failed to create collection {collection_name}: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Failed to create collection {collection_name}: {e}")
            return False

    async def list_collections(self) -> List[str]:
        """List all collections."""
        try:
            client = await self._get_client()
            response = await client.get("/collections")

            if response.status_code == 200:
                data = response.json()
                collections = data.get("result", {}).get("collections", [])
                return [c["name"] for c in collections]
            return []

        except Exception as e:
            logger.error(f"Failed to list collections: {e}")
            return []

    async def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection."""
        try:
            client = await self._get_client()
            response = await client.delete(f"/collections/{collection_name}")

            if response.status_code == 200:
                logger.info(f"Deleted collection: {collection_name}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to delete collection {collection_name}: {e}")
            return False

    async def get_collection_info(self, collection_name: str) -> Dict:
        """Get collection information including vector count."""
        try:
            client = await self._get_client()
            response = await client.get(f"/collections/{collection_name}")

            if response.status_code == 200:
                data = response.json()
                result = data.get("result", {})
                return {
                    "name": collection_name,
                    "vectors_count": result.get("vectors_count", 0),
                    "indexed_vectors_count": result.get("indexed_vectors_count", 0),
                    "points_count": result.get("points_count", 0),
                    "status": result.get("status", "unknown"),
                }
            return {}

        except Exception as e:
            logger.error(f"Failed to get collection info for {collection_name}: {e}")
            return {}

    # --- Vector Operations ---

    async def upsert_vector(
        self,
        collection_name: str,
        vector_id: str,
        vector: List[float],
        payload: Dict = None,
    ) -> bool:
        """Insert or update a single vector."""
        try:
            client = await self._get_client()
            response = await client.put(
                f"/collections/{collection_name}/points",
                json={
                    "points": [
                        {
                            "id": vector_id,
                            "vector": vector,
                            "payload": payload or {},
                        }
                    ]
                },
            )

            if response.status_code == 200:
                return True
            else:
                logger.error(f"Upsert failed for {collection_name}/{vector_id}: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Upsert failed for {collection_name}/{vector_id}: {e}")
            return False

    async def upsert_batch(
        self,
        collection_name: str,
        points: List[Dict[str, Any]],
        batch_size: int = 100,
    ) -> int:
        """
        Batch insert/update vectors.

        Args:
            collection_name: Collection name
            points: List of {"id": str, "vector": List[float], "payload": Dict}
            batch_size: Number of points per batch

        Returns:
            Number of successfully inserted points
        """
        total_inserted = 0

        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]

            try:
                client = await self._get_client()
                response = await client.put(
                    f"/collections/{collection_name}/points",
                    json={"points": batch},
                )

                if response.status_code == 200:
                    total_inserted += len(batch)
                else:
                    logger.error(
                        f"Batch upsert failed for {collection_name} "
                        f"(batch {i // batch_size}): {response.status_code}"
                    )

            except Exception as e:
                logger.error(f"Batch upsert failed for {collection_name}: {e}")

        return total_inserted

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 10,
        score_threshold: float = 0.7,
        filter_conditions: Dict = None,
    ) -> List[Dict]:
        """
        Search for similar vectors.

        Args:
            collection_name: Collection to search
            query_vector: Query embedding vector
            limit: Maximum results
            score_threshold: Minimum similarity score
            filter_conditions: Qdrant filter dict

        Returns:
            List of matching results with scores and payloads
        """
        try:
            search_body = {
                "vector": query_vector,
                "limit": limit,
                "score_threshold": score_threshold,
                "with_payload": True,
            }

            if filter_conditions:
                search_body["filter"] = filter_conditions

            client = await self._get_client()
            response = await client.post(
                f"/collections/{collection_name}/points/search",
                json=search_body,
            )

            if response.status_code != 200:
                logger.error(f"Search failed for {collection_name}: {response.status_code}")
                return []

            results = response.json()
            return results.get("result", [])

        except Exception as e:
            logger.error(f"Search failed for {collection_name}: {e}")
            return []

    async def delete_vector(self, collection_name: str, vector_id: str) -> bool:
        """Delete a single vector."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/collections/{collection_name}/points/delete",
                json={"points": [vector_id]},
            )
            return response.status_code == 200

        except Exception as e:
            logger.error(f"Delete failed for {collection_name}/{vector_id}: {e}")
            return False

    async def delete_by_filter(
        self,
        collection_name: str,
        filter_conditions: Dict,
    ) -> bool:
        """Delete vectors matching filter conditions."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/collections/{collection_name}/points/delete",
                json={"filter": filter_conditions},
            )
            return response.status_code == 200

        except Exception as e:
            logger.error(f"Delete by filter failed for {collection_name}: {e}")
            return False

    async def count(
        self,
        collection_name: str,
        filter_conditions: Dict = None,
    ) -> int:
        """Count vectors in a collection, optionally with filter."""
        try:
            client = await self._get_client()

            if filter_conditions:
                response = await client.post(
                    f"/collections/{collection_name}/points/count",
                    json={"filter": filter_conditions},
                )
            else:
                response = await client.post(
                    f"/collections/{collection_name}/points/count",
                    json={},
                )

            if response.status_code == 200:
                return response.json().get("result", {}).get("count", 0)
            return 0

        except Exception as e:
            logger.error(f"Count failed for {collection_name}: {e}")
            return 0


# Singleton instance
qdrant_client = QdrantClient()


# --- Collection Initialization ---

async def init_all_collections() -> Dict[str, bool]:
    """
    Initialize all required collections.

    Called on application startup.
    """
    results = {}

    for name, definition in COLLECTION_DEFINITIONS.items():
        success = await qdrant_client.create_collection(
            collection_name=name,
            vector_size=definition["vector_size"],
            distance=definition["distance"],
        )
        results[name] = success

    return results


async def get_all_collection_info() -> Dict[str, Dict]:
    """Get info for all collections."""
    info = {}

    for name in COLLECTION_DEFINITIONS:
        info[name] = await qdrant_client.get_collection_info(name)

    return info


# --- Helper Functions ---

def build_tenant_filter(tenant_id: str) -> Dict:
    """Build a Qdrant filter for tenant scoping."""
    return {
        "must": [
            {"key": "tenant_id", "match": {"value": tenant_id}}
        ]
    }


def build_tenant_type_filter(tenant_id: str, type_value: str, type_key: str = "type") -> Dict:
    """Build a Qdrant filter for tenant + type scoping."""
    return {
        "must": [
            {"key": "tenant_id", "match": {"value": tenant_id}},
            {"key": type_key, "match": {"value": type_value}},
        ]
    }
