"""
Embedding Pipeline (Phase 37.2)

Generates and stores embeddings for:
1. Conversations — Past conversation summaries
2. Objections — Objection handling examples
3. Sales Scripts — Sales script templates
4. Lead Notes — Lead interaction notes
5. Successful Conversions — Winning conversation patterns

Pipeline Flow:
Source Data → Text Preparation → Embedding Generation → Vector Storage
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.appointment import Appointment
from app.models.campaign import Campaign
from app.analytics.vector_search import (
    qdrant_client,
    build_tenant_filter,
    build_tenant_type_filter,
)
from app.ai.conversation_engine.retrieval import EmbeddingGenerator

logger = logging.getLogger(__name__)


class EmbeddingPipeline:
    """
    Generates and stores embeddings for various content types.

    Usage:
        pipeline = EmbeddingPipeline(db, tenant_id)
        await pipeline.embed_conversation(conversation_id)
        await pipeline.embed_objection("pricing", "too expensive", "Our plans are flexible...")
        await pipeline.embed_lead_notes(lead_id)
    """

    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.embedding_gen = EmbeddingGenerator()

    # --- Conversation Embedding ---

    async def embed_conversation(
        self,
        conversation_id: UUID,
        force: bool = False,
    ) -> bool:
        """
        Embed a conversation's message history.

        Args:
            conversation_id: Conversation UUID
            force: Re-embed even if already exists

        Returns:
            True if embedding was stored
        """
        conversation = self.db.query(Conversation).filter(
            Conversation.id == conversation_id,
        ).first()

        if not conversation:
            logger.warning(f"Conversation {conversation_id} not found")
            return False

        # Check if already embedded
        doc_id = f"conv_{conversation_id}"
        if not force:
            existing = await qdrant_client.search(
                collection_name="conversation_embeddings",
                query_vector=[0.0] * 384,  # Dummy vector
                limit=1,
                filter_conditions={
                    "must": [
                        {"key": "conversation_id", "match": {"value": str(conversation_id)}},
                    ]
                },
            )
            if existing:
                logger.debug(f"Conversation {conversation_id} already embedded")
                return True

        # Get messages
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(50)
            .all()
        )

        if not messages:
            return False

        # Prepare text
        text = self._prepare_conversation_text(messages, conversation)

        # Generate embedding
        embedding = await self.embedding_gen.generate(text)

        # Prepare metadata
        lead = self.db.query(Lead).filter(Lead.id == conversation.lead_id).first()
        payload = {
            "tenant_id": self.tenant_id,
            "lead_id": str(conversation.lead_id),
            "conversation_id": str(conversation_id),
            "status": conversation.status,
            "message_count": len(messages),
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        }

        if lead:
            payload["lead_source"] = lead.source
            payload["lead_state"] = lead.state

        # Store
        success = await qdrant_client.upsert_vector(
            collection_name="conversation_embeddings",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.info(f"Embedded conversation {conversation_id}")

        return success

    # --- Objection Embedding ---

    async def embed_objection(
        self,
        objection_type: str,
        objection_text: str,
        response_text: str,
        was_successful: bool = True,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """
        Embed an objection handling example.

        Args:
            objection_type: Type of objection (pricing, trust, etc.)
            objection_text: Customer's objection
            response_text: AI/agent response
            was_successful: Whether the objection was overcome
            metadata: Additional metadata

        Returns:
            True if embedding was stored
        """
        # Prepare text
        text = (
            f"Objection Type: {objection_type}\n"
            f"Customer: {objection_text}\n"
            f"Response: {response_text}\n"
            f"Outcome: {'Overcome' if was_successful else 'Not overcome'}"
        )

        # Generate embedding
        embedding = await self.embedding_gen.generate(text)

        # Create unique ID
        content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        doc_id = f"obj_{objection_type}_{content_hash}"

        # Store
        payload = {
            "tenant_id": self.tenant_id,
            "objection_type": objection_type,
            "was_successful": was_successful,
            "text": text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            payload.update(metadata)

        success = await qdrant_client.upsert_vector(
            collection_name="objection_embeddings",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.info(f"Embedded objection: {objection_type}")

        return success

    # --- Sales Script Embedding ---

    async def embed_sales_script(
        self,
        script_id: str,
        script_text: str,
        script_type: str = "general",
        tone: str = "friendly",
        campaign_id: Optional[str] = None,
    ) -> bool:
        """
        Embed a sales script.

        Args:
            script_id: Unique script identifier
            script_text: Script content
            script_type: Type (outreach, objection, followup, closing)
            tone: Script tone
            campaign_id: Associated campaign

        Returns:
            True if embedding was stored
        """
        # Generate embedding
        embedding = await self.embedding_gen.generate(script_text)

        doc_id = f"script_{script_id}"

        payload = {
            "tenant_id": self.tenant_id,
            "script_type": script_type,
            "tone": tone,
            "text": script_text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if campaign_id:
            payload["campaign_id"] = campaign_id

        success = await qdrant_client.upsert_vector(
            collection_name="sales_script_embeddings",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.info(f"Embedded sales script: {script_id}")

        return success

    # --- Lead Notes Embedding ---

    async def embed_lead_notes(self, lead_id: UUID) -> bool:
        """
        Embed a lead's notes and interaction history.

        Args:
            lead_id: Lead UUID

        Returns:
            True if embedding was stored
        """
        lead = self.db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return False

        # Prepare text from lead data
        parts = [
            f"Lead: {lead.first_name} {lead.last_name}",
            f"Source: {lead.source}",
            f"State: {lead.state}",
            f"Status: {lead.status}",
            f"Score: {lead.lead_score}",
        ]

        if lead.notes:
            parts.append(f"Notes: {lead.notes}")

        if lead.tags:
            parts.append(f"Tags: {', '.join(lead.tags)}")

        # Get conversation summaries
        conversations = (
            self.db.query(Conversation)
            .filter(Conversation.lead_id == lead_id)
            .order_by(Conversation.created_at.desc())
            .limit(5)
            .all()
        )

        for conv in conversations:
            messages = (
                self.db.query(Message)
                .filter(Message.conversation_id == conv.id, Message.sender == "customer")
                .order_by(Message.created_at.desc())
                .limit(3)
                .all()
            )
            for msg in messages:
                parts.append(f"Customer said: {msg.content[:200]}")

        text = "\n".join(parts)

        # Generate embedding
        embedding = await self.embedding_gen.generate(text)

        doc_id = f"lead_{lead_id}"

        payload = {
            "tenant_id": self.tenant_id,
            "lead_id": str(lead_id),
            "lead_source": lead.source,
            "lead_state": lead.state,
            "lead_score": lead.lead_score or 0,
            "text": text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        success = await qdrant_client.upsert_vector(
            collection_name="conversation_embeddings",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.info(f"Embedded lead notes: {lead_id}")

        return success

    # --- Successful Conversion Embedding ---

    async def embed_successful_conversion(
        self,
        appointment_id: UUID,
    ) -> bool:
        """
        Embed a successful conversion (won appointment) for future reference.

        Args:
            appointment_id: Appointment UUID

        Returns:
            True if embedding was stored
        """
        appointment = self.db.query(Appointment).filter(
            Appointment.id == appointment_id,
        ).first()

        if not appointment:
            return False

        # Get conversation messages
        messages = []
        if appointment.conversation_id:
            messages = (
                self.db.query(Message)
                .filter(Message.conversation_id == appointment.conversation_id)
                .order_by(Message.created_at)
                .limit(30)
                .all()
            )

        # Prepare text
        parts = [
            f"Successful conversion",
            f"Disposition: {appointment.disposition}",
            f"Duration: {appointment.call_duration_seconds} seconds",
        ]

        for msg in messages:
            prefix = "Customer" if msg.sender == "customer" else "Agent"
            parts.append(f"{prefix}: {msg.content[:200]}")

        text = "\n".join(parts)

        # Generate embedding
        embedding = await self.embedding_gen.generate(text)

        doc_id = f"conversion_{appointment_id}"

        payload = {
            "tenant_id": self.tenant_id,
            "appointment_id": str(appointment_id),
            "lead_id": str(appointment.lead_id),
            "agent_id": str(appointment.agent_id),
            "disposition": appointment.disposition,
            "call_duration": appointment.call_duration_seconds,
            "text": text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        success = await qdrant_client.upsert_vector(
            collection_name="conversation_embeddings",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.info(f"Embedded successful conversion: {appointment_id}")

        return success

    # --- Batch Operations ---

    async def embed_all_objections_from_campaigns(self) -> Dict[str, int]:
        """
        Embed all objection prompts from campaigns.

        Returns:
            Dict with counts per campaign
        """
        campaigns = self.db.query(Campaign).filter(
            Campaign.tenant_id == self.tenant_id,
            Campaign.deleted_at.is_(None),
        ).all()

        results = {}

        for campaign in campaigns:
            count = 0
            if campaign.objection_prompts:
                for obj_type, response in campaign.objection_prompts.items():
                    success = await self.embed_objection(
                        objection_type=obj_type,
                        objection_text=f"Common {obj_type} objection",
                        response_text=response,
                        was_successful=True,
                        metadata={"campaign_id": str(campaign.id)},
                    )
                    if success:
                        count += 1

            results[str(campaign.id)] = count

        return results

    async def embed_campaign_scripts(self) -> Dict[str, int]:
        """
        Embed all campaign prompt templates as sales scripts.

        Returns:
            Dict with counts per campaign
        """
        campaigns = self.db.query(Campaign).filter(
            Campaign.tenant_id == self.tenant_id,
            Campaign.deleted_at.is_(None),
        ).all()

        results = {}

        for campaign in campaigns:
            count = 0

            if campaign.prompt_template:
                success = await self.embed_sales_script(
                    script_id=f"campaign_{campaign.id}_main",
                    script_text=campaign.prompt_template,
                    script_type="outreach",
                    tone=campaign.tone,
                    campaign_id=str(campaign.id),
                )
                if success:
                    count += 1

            results[str(campaign.id)] = count

        return results

    # --- Helpers ---

    def _prepare_conversation_text(
        self,
        messages: List[Message],
        conversation: Conversation,
    ) -> str:
        """Prepare conversation text for embedding."""
        parts = [
            f"Conversation status: {conversation.status}",
            f"Messages: {len(messages)}",
        ]

        for msg in messages:
            prefix = "Customer" if msg.sender == "customer" else "Agent"
            parts.append(f"{prefix}: {msg.content[:200]}")

        return "\n".join(parts)
