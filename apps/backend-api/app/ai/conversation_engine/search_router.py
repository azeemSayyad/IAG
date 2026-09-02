"""
Semantic Search API Router (Phase 37.4)

Endpoints:
- POST /search/conversations — Search similar conversations
- POST /search/objections — Search objection handling examples
- POST /search/scripts — Search sales scripts
- POST /search/knowledge — Search knowledge base
- POST /search/all — Search across all collections
- POST /search/index/conversation — Index a conversation
- POST /search/index/objection — Index an objection example
- POST /search/index/script — Index a sales script
- POST /search/index/knowledge — Index knowledge base entry
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user
from app.models.user import User
from app.ai.conversation_engine.retrieval_service import retrieval_service
from app.ai.conversation_engine.embedding_pipeline import EmbeddingPipeline

router = APIRouter(prefix="/search", tags=["semantic-search"])


# --- Request Models ---

class ConversationSearchRequest(BaseModel):
    query: str
    limit: int = 5
    score_threshold: float = 0.6
    status_filter: Optional[str] = None


class ObjectionSearchRequest(BaseModel):
    objection_type: str
    query: str
    limit: int = 3
    successful_only: bool = True


class ScriptSearchRequest(BaseModel):
    query: str
    script_type: Optional[str] = None
    tone: Optional[str] = None
    limit: int = 3


class KnowledgeSearchRequest(BaseModel):
    query: str
    category: Optional[str] = None
    limit: int = 5


class AllSearchRequest(BaseModel):
    query: str
    include_conversations: bool = True
    include_objections: bool = True
    include_scripts: bool = True
    include_knowledge: bool = True
    limit_per_collection: int = 3


class IndexConversationRequest(BaseModel):
    conversation_id: UUID


class IndexObjectionRequest(BaseModel):
    objection_type: str
    objection_text: str
    response_text: str
    was_successful: bool = True


class IndexScriptRequest(BaseModel):
    script_id: str
    script_text: str
    script_type: str = "general"
    tone: str = "friendly"
    campaign_id: Optional[str] = None


class IndexKnowledgeRequest(BaseModel):
    text: str
    category: str
    topic: str
    source: str = "manual"


# --- Search Endpoints ---

@router.post("/conversations")
async def search_conversations(
    request: ConversationSearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search for similar past conversations.

    Uses semantic similarity to find conversations that match
    the query text. Useful for finding similar customer scenarios.
    """
    results = await retrieval_service.search_similar_conversations(
        query=request.query,
        tenant_id=tenant_id,
        limit=request.limit,
        score_threshold=request.score_threshold,
        status_filter=request.status_filter,
    )

    return {
        "query": request.query,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.post("/objections")
async def search_objections(
    request: ObjectionSearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search for objection handling examples.

    Finds similar objections and how they were successfully handled.
    Useful for AI to learn from past successful interactions.
    """
    results = await retrieval_service.search_objections(
        objection_type=request.objection_type,
        query=request.query,
        tenant_id=tenant_id,
        limit=request.limit,
        successful_only=request.successful_only,
    )

    return {
        "objection_type": request.objection_type,
        "query": request.query,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.post("/scripts")
async def search_scripts(
    request: ScriptSearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search for relevant sales scripts.

    Finds scripts that match the query, optionally filtered by
    type and tone.
    """
    results = await retrieval_service.search_sales_scripts(
        query=request.query,
        tenant_id=tenant_id,
        script_type=request.script_type,
        tone=request.tone,
        limit=request.limit,
    )

    return {
        "query": request.query,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.post("/knowledge")
async def search_knowledge(
    request: KnowledgeSearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search the insurance knowledge base.

    Finds relevant knowledge entries for the query.
    """
    results = await retrieval_service.search_knowledge_base(
        query=request.query,
        tenant_id=tenant_id,
        category=request.category,
        limit=request.limit,
    )

    return {
        "query": request.query,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.post("/all")
async def search_all(
    request: AllSearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Search across all collections.

    Returns results from conversations, objections, scripts,
    and knowledge base in a single call.
    """
    results = await retrieval_service.search_all(
        query=request.query,
        tenant_id=tenant_id,
        include_conversations=request.include_conversations,
        include_objections=request.include_objections,
        include_scripts=request.include_scripts,
        include_knowledge=request.include_knowledge,
        limit_per_collection=request.limit_per_collection,
    )

    return {
        "query": request.query,
        "results": {
            k: [r.to_dict() for r in v]
            for k, v in results.items()
        },
        "total": sum(len(v) for v in results.values()),
        "formatted": retrieval_service.format_for_prompt(results),
    }


# --- Index Endpoints ---

@router.post("/index/conversation")
async def index_conversation(
    request: IndexConversationRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Index a conversation for semantic search.

    Embeds the conversation's message history and stores it
    in the vector database.
    """
    pipeline = EmbeddingPipeline(db, tenant_id)
    success = await pipeline.embed_conversation(request.conversation_id)

    return {
        "success": success,
        "conversation_id": str(request.conversation_id),
    }


@router.post("/index/objection")
async def index_objection(
    request: IndexObjectionRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Index an objection handling example.

    Stores the objection and response for future retrieval.
    """
    pipeline = EmbeddingPipeline(db, tenant_id)
    success = await pipeline.embed_objection(
        objection_type=request.objection_type,
        objection_text=request.objection_text,
        response_text=request.response_text,
        was_successful=request.was_successful,
    )

    return {"success": success}


@router.post("/index/script")
async def index_script(
    request: IndexScriptRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Index a sales script.

    Stores the script for semantic retrieval.
    """
    pipeline = EmbeddingPipeline(db, tenant_id)
    success = await pipeline.embed_sales_script(
        script_id=request.script_id,
        script_text=request.script_text,
        script_type=request.script_type,
        tone=request.tone,
        campaign_id=request.campaign_id,
    )

    return {"success": success}


@router.post("/index/knowledge")
async def index_knowledge(
    request: IndexKnowledgeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Index a knowledge base entry.

    Stores the knowledge for semantic retrieval.
    """
    from app.analytics.vector_search import qdrant_client
    from app.ai.conversation_engine.retrieval import EmbeddingGenerator
    from datetime import datetime, timezone
    import hashlib

    gen = EmbeddingGenerator()
    embedding = await gen.generate(request.text)

    content_hash = hashlib.md5(request.text.encode()).hexdigest()[:12]
    doc_id = f"kb_{request.category}_{content_hash}"

    success = await qdrant_client.upsert_vector(
        collection_name="knowledge_base",
        vector_id=doc_id,
        vector=embedding,
        payload={
            "tenant_id": tenant_id,
            "category": request.category,
            "topic": request.topic,
            "source": request.source,
            "text": request.text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {"success": success, "doc_id": doc_id}
