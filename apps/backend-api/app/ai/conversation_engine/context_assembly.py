"""
Context Assembly Engine (Phase 37.5)

Combines all context sources for LLM prompt injection:

1. Semantic Results — Vector search results from RAG
2. Recent Messages — Last N conversation messages
3. Lead Profile — Lead data, score, tier, preferences
4. Campaign Strategy — Campaign tone, scripts, targeting

Assembly Flow:
1. Fetch lead profile
2. Load conversation history
3. Run semantic search (RAG)
4. Load campaign strategy
5. Merge and prioritize context
6. Format for LLM injection
7. Respect token limits
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.campaign import Campaign
from app.ai.conversation_engine.context_builder import ConversationContext
from app.ai.conversation_engine.retrieval_service import retrieval_service, SearchResult
from app.ai.conversation_engine.reasoning import MultiTurnReasoner
from app.ai.conversation_engine.personalization import PersonalizationEngine

logger = logging.getLogger(__name__)


class AssembledContext:
    """Complete assembled context for LLM prompt injection."""

    def __init__(self):
        # Core context
        self.lead_profile: str = ""
        self.conversation_history: str = ""
        self.campaign_strategy: str = ""

        # RAG results
        self.semantic_conversations: List[SearchResult] = []
        self.semantic_objections: List[SearchResult] = []
        self.semantic_scripts: List[SearchResult] = []
        self.semantic_knowledge: List[SearchResult] = []

        # Analysis
        self.reasoning_analysis: Dict = {}
        self.personalization: Dict = {}

        # Metadata
        self.total_tokens_estimate: int = 0
        self.sources_used: List[str] = []

    def to_prompt_context(self) -> str:
        """Convert assembled context to prompt-ready string."""
        parts = []

        # Lead profile
        if self.lead_profile:
            parts.append(f"LEAD PROFILE:\n{self.lead_profile}")

        # Campaign strategy
        if self.campaign_strategy:
            parts.append(f"CAMPAIGN STRATEGY:\n{self.campaign_strategy}")

        # Conversation history
        if self.conversation_history:
            parts.append(f"CONVERSATION HISTORY:\n{self.conversation_history}")

        # Semantic results
        rag_parts = []
        if self.semantic_conversations:
            rag_parts.append("SIMILAR PAST CONVERSATIONS:")
            for r in self.semantic_conversations[:2]:
                rag_parts.append(f"- {r.text[:200]}")

        if self.semantic_objections:
            rag_parts.append("\nOBJECTION HANDLING EXAMPLES:")
            for r in self.semantic_objections[:2]:
                rag_parts.append(f"- {r.text[:200]}")

        if self.semantic_scripts:
            rag_parts.append("\nRELEVANT SCRIPTS:")
            for r in self.semantic_scripts[:2]:
                rag_parts.append(f"- {r.text[:200]}")

        if self.semantic_knowledge:
            rag_parts.append("\nRELEVANT KNOWLEDGE:")
            for r in self.semantic_knowledge[:2]:
                rag_parts.append(f"- {r.text[:200]}")

        if rag_parts:
            parts.append("RETRIEVED CONTEXT:\n" + "\n".join(rag_parts))

        # Reasoning guidance
        if self.reasoning_analysis:
            guidance = self.reasoning_analysis.get("prompt_addition", "")
            if guidance:
                parts.append(f"REASONING GUIDANCE:\n{guidance}")

        # Personalization
        if self.personalization:
            p_prompt = self.personalization.get("prompt_addition", "")
            if p_prompt:
                parts.append(p_prompt)

        return "\n\n".join(parts)

    def to_dict(self) -> Dict:
        """Serialize for debugging/logging."""
        return {
            "lead_profile_length": len(self.lead_profile),
            "conversation_history_length": len(self.conversation_history),
            "campaign_strategy_length": len(self.campaign_strategy),
            "semantic_conversations": len(self.semantic_conversations),
            "semantic_objections": len(self.semantic_objections),
            "semantic_scripts": len(self.semantic_scripts),
            "semantic_knowledge": len(self.semantic_knowledge),
            "reasoning_health": self.reasoning_analysis.get("health"),
            "persuasion_level": self.reasoning_analysis.get("persuasion_level"),
            "total_tokens_estimate": self.total_tokens_estimate,
            "sources_used": self.sources_used,
        }


class ContextAssemblyEngine:
    """
    Assembles complete context from all sources for LLM prompts.

    Orchestrates:
    - Lead profile loading
    - Conversation history
    - RAG retrieval
    - Campaign strategy
    - Reasoning analysis
    - Personalization
    """

    def __init__(self, db: Session):
        self.db = db
        self.reasoner = MultiTurnReasoner()
        self.personalizer = PersonalizationEngine()

    async def assemble(
        self,
        ctx: ConversationContext,
        query: str,
        tenant_id: str,
        include_rag: bool = True,
        max_tokens: int = 3000,
    ) -> AssembledContext:
        """
        Assemble complete context for LLM prompt injection.

        Args:
            ctx: ConversationContext from context builder
            query: Current customer message
            tenant_id: Tenant ID for RAG scoping
            include_rag: Whether to include RAG results
            max_tokens: Maximum token budget

        Returns:
            AssembledContext with all sources combined
        """
        assembled = AssembledContext()

        # 1. Lead profile
        assembled.lead_profile = self._build_lead_profile(ctx)
        assembled.sources_used.append("lead_profile")

        # 2. Campaign strategy
        assembled.campaign_strategy = self._build_campaign_strategy(ctx)
        assembled.sources_used.append("campaign_strategy")

        # 3. Conversation history
        assembled.conversation_history = self._build_conversation_history(ctx)
        assembled.sources_used.append("conversation_history")

        # 4. RAG retrieval
        if include_rag:
            rag_results = await self._retrieve_rag(query, tenant_id, ctx)
            assembled.semantic_conversations = rag_results.get("conversations", [])
            assembled.semantic_objections = rag_results.get("objections", [])
            assembled.semantic_scripts = rag_results.get("scripts", [])
            assembled.semantic_knowledge = rag_results.get("knowledge", [])
            assembled.sources_used.append("rag")

        # 5. Reasoning analysis
        assembled.reasoning_analysis = self.reasoner.analyze_conversation(ctx)
        assembled.reasoning_analysis["prompt_addition"] = self.reasoner.get_reasoning_prompt_addition(ctx)
        assembled.sources_used.append("reasoning")

        # 6. Personalization
        assembled.personalization = self.personalizer.personalize(ctx)
        assembled.sources_used.append("personalization")

        # 7. Estimate tokens
        assembled.total_tokens_estimate = self._estimate_tokens(assembled)

        return assembled

    def _build_lead_profile(self, ctx: ConversationContext) -> str:
        """Build lead profile string."""
        if not ctx.lead:
            return ""

        parts = [
            f"Name: {ctx.lead.first_name} {ctx.lead.last_name}",
            f"Source: {ctx.lead.source}",
            f"Score: {ctx.lead.lead_score or 0}/100 (tier: {ctx.lead_tier})",
            f"Status: {ctx.lead.status}",
        ]

        if ctx.lead.state:
            parts.append(f"State: {ctx.lead.state}")
        if ctx.lead.phone:
            parts.append(f"Phone: {ctx.lead.phone}")
        if ctx.lead.email:
            parts.append(f"Email: {ctx.lead.email}")

        return " | ".join(parts)

    def _build_campaign_strategy(self, ctx: ConversationContext) -> str:
        """Build campaign strategy string."""
        if not ctx.campaign:
            return ""

        parts = [
            f"Campaign: {ctx.campaign.name}",
            f"Tone: {ctx.campaign.tone}",
        ]

        if ctx.campaign.prompt_template:
            parts.append(f"Script: {ctx.campaign.prompt_template[:200]}")

        return " | ".join(parts)

    def _build_conversation_history(self, ctx: ConversationContext) -> str:
        """Build conversation history string."""
        if not ctx.messages:
            return ""

        parts = []
        for msg in ctx.messages[-10:]:
            prefix = "Customer" if msg.get("sender") == "customer" else "AI"
            content = msg.get("content", "")[:150]
            parts.append(f"{prefix}: {content}")

        return "\n".join(parts)

    async def _retrieve_rag(
        self,
        query: str,
        tenant_id: str,
        ctx: ConversationContext,
    ) -> Dict[str, List[SearchResult]]:
        """Retrieve RAG results from vector store."""
        results = {}

        # Search conversations
        try:
            results["conversations"] = await retrieval_service.search_similar_conversations(
                query=query,
                tenant_id=tenant_id,
                limit=3,
                score_threshold=0.6,
            )
        except Exception as e:
            logger.debug(f"Conversation search failed: {e}")
            results["conversations"] = []

        # Search objections if we have one
        if ctx.objections:
            last_obj = ctx.objections[-1]
            try:
                results["objections"] = await retrieval_service.search_objections(
                    objection_type=last_obj.get("type", "general"),
                    query=query,
                    tenant_id=tenant_id,
                    limit=2,
                )
            except Exception as e:
                logger.debug(f"Objection search failed: {e}")
                results["objections"] = []

        # Search scripts
        try:
            tone = ctx.campaign.tone if ctx.campaign else "friendly"
            results["scripts"] = await retrieval_service.search_sales_scripts(
                query=query,
                tenant_id=tenant_id,
                tone=tone,
                limit=2,
            )
        except Exception as e:
            logger.debug(f"Script search failed: {e}")
            results["scripts"] = []

        # Search knowledge base
        try:
            results["knowledge"] = await retrieval_service.search_knowledge_base(
                query=query,
                tenant_id=tenant_id,
                limit=2,
            )
        except Exception as e:
            logger.debug(f"Knowledge search failed: {e}")
            results["knowledge"] = []

        return results

    def _estimate_tokens(self, assembled: AssembledContext) -> int:
        """Estimate total tokens in assembled context."""
        total_chars = (
            len(assembled.lead_profile)
            + len(assembled.conversation_history)
            + len(assembled.campaign_strategy)
            + sum(len(r.text) for r in assembled.semantic_conversations)
            + sum(len(r.text) for r in assembled.semantic_objections)
            + sum(len(r.text) for r in assembled.semantic_scripts)
            + sum(len(r.text) for r in assembled.semantic_knowledge)
        )

        # Rough estimate: ~4 chars per token
        return total_chars // 4


# Singleton factory
def get_context_assembly_engine(db: Session) -> ContextAssemblyEngine:
    """Get a context assembly engine instance."""
    return ContextAssemblyEngine(db)
