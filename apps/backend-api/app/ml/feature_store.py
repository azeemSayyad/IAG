"""
Feature Store (Phase 38.2)

Stores and retrieves computed features for ML inference:

Feature Tables:
- lead_features — Per-lead feature vectors
- agent_features — Per-agent feature vectors
- campaign_features — Per-campaign feature vectors

Storage: Redis (for fast inference) + PostgreSQL (for persistence)

Usage:
    store = FeatureStore(db)
    store.save_lead_features(lead_id, features)
    features = store.get_lead_features(lead_id)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.database import Base
from app.core.redis import redis_service

logger = logging.getLogger(__name__)


# --- Feature Store Models ---

class LeadFeatureSnapshot(Base):
    """Persistent storage for lead features."""
    __tablename__ = "lead_feature_snapshots"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: __import__('uuid').uuid4())
    lead_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    features = Column(JSON, nullable=False)
    feature_version = Column(String, default="v1")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentFeatureSnapshot(Base):
    """Persistent storage for agent features."""
    __tablename__ = "agent_feature_snapshots"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: __import__('uuid').uuid4())
    agent_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    features = Column(JSON, nullable=False)
    feature_version = Column(String, default="v1")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CampaignFeatureSnapshot(Base):
    """Persistent storage for campaign features."""
    __tablename__ = "campaign_feature_snapshots"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: __import__('uuid').uuid4())
    campaign_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    features = Column(JSON, nullable=False)
    feature_version = Column(String, default="v1")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# --- Feature Store Class ---

class FeatureStore:
    """
    Manages feature storage and retrieval.

    Two-tier storage:
    1. Redis — Fast inference (TTL: 1 hour)
    2. PostgreSQL — Persistent storage

    Usage:
        store = FeatureStore(db)
        store.save_lead_features(lead_id, features, tenant_id)
        features = store.get_lead_features(lead_id)
    """

    # Redis key prefixes
    LEAD_PREFIX = "features:lead:"
    AGENT_PREFIX = "features:agent:"
    CAMPAIGN_PREFIX = "features:campaign:"

    # Redis TTL (1 hour)
    CACHE_TTL = 3600

    def __init__(self, db: Session):
        self.db = db
        self.redis = redis_service

    # --- Lead Features ---

    def save_lead_features(
        self,
        lead_id: UUID,
        features: Dict[str, Any],
        tenant_id: str,
    ) -> bool:
        """
        Save lead features to store.

        Stores in Redis (fast) and PostgreSQL (persistent).
        """
        key = f"{self.LEAD_PREFIX}{lead_id}"

        # Save to Redis
        self.redis.set_cache(key, features, ttl=self.CACHE_TTL)

        # Save to PostgreSQL
        try:
            snapshot = LeadFeatureSnapshot(
                lead_id=lead_id,
                tenant_id=tenant_id,
                features=features,
            )
            self.db.add(snapshot)
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save lead features: {e}")
            self.db.rollback()
            return False

    def get_lead_features(self, lead_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get lead features from store.

        Tries Redis first, falls back to PostgreSQL.
        """
        key = f"{self.LEAD_PREFIX}{lead_id}"

        # Try Redis
        cached = self.redis.get_cache(key)
        if cached:
            return cached

        # Fall back to PostgreSQL
        snapshot = (
            self.db.query(LeadFeatureSnapshot)
            .filter(LeadFeatureSnapshot.lead_id == lead_id)
            .order_by(LeadFeatureSnapshot.created_at.desc())
            .first()
        )

        if snapshot:
            # Re-cache in Redis
            self.redis.set_cache(key, snapshot.features, ttl=self.CACHE_TTL)
            return snapshot.features

        return None

    # --- Agent Features ---

    def save_agent_features(
        self,
        agent_id: UUID,
        features: Dict[str, Any],
        tenant_id: str,
    ) -> bool:
        """Save agent features to store."""
        key = f"{self.AGENT_PREFIX}{agent_id}"

        # Save to Redis
        self.redis.set_cache(key, features, ttl=self.CACHE_TTL)

        # Save to PostgreSQL
        try:
            snapshot = AgentFeatureSnapshot(
                agent_id=agent_id,
                tenant_id=tenant_id,
                features=features,
            )
            self.db.add(snapshot)
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save agent features: {e}")
            self.db.rollback()
            return False

    def get_agent_features(self, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Get agent features from store."""
        key = f"{self.AGENT_PREFIX}{agent_id}"

        # Try Redis
        cached = self.redis.get_cache(key)
        if cached:
            return cached

        # Fall back to PostgreSQL
        snapshot = (
            self.db.query(AgentFeatureSnapshot)
            .filter(AgentFeatureSnapshot.agent_id == agent_id)
            .order_by(AgentFeatureSnapshot.created_at.desc())
            .first()
        )

        if snapshot:
            self.redis.set_cache(key, snapshot.features, ttl=self.CACHE_TTL)
            return snapshot.features

        return None

    # --- Campaign Features ---

    def save_campaign_features(
        self,
        campaign_id: UUID,
        features: Dict[str, Any],
        tenant_id: str,
    ) -> bool:
        """Save campaign features to store."""
        key = f"{self.CAMPAIGN_PREFIX}{campaign_id}"

        # Save to Redis
        self.redis.set_cache(key, features, ttl=self.CACHE_TTL)

        # Save to PostgreSQL
        try:
            snapshot = CampaignFeatureSnapshot(
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                features=features,
            )
            self.db.add(snapshot)
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save campaign features: {e}")
            self.db.rollback()
            return False

    def get_campaign_features(self, campaign_id: UUID) -> Optional[Dict[str, Any]]:
        """Get campaign features from store."""
        key = f"{self.CAMPAIGN_PREFIX}{campaign_id}"

        # Try Redis
        cached = self.redis.get_cache(key)
        if cached:
            return cached

        # Fall back to PostgreSQL
        snapshot = (
            self.db.query(CampaignFeatureSnapshot)
            .filter(CampaignFeatureSnapshot.campaign_id == campaign_id)
            .order_by(CampaignFeatureSnapshot.created_at.desc())
            .first()
        )

        if snapshot:
            self.redis.set_cache(key, snapshot.features, ttl=self.CACHE_TTL)
            return snapshot.features

        return None

    # --- Batch Operations ---

    def save_batch_lead_features(
        self,
        features_list: List[Dict[str, Any]],
        tenant_id: str,
    ) -> int:
        """
        Save features for multiple leads.

        Args:
            features_list: List of dicts with 'lead_id' key + feature values
            tenant_id: Tenant ID

        Returns:
            Number of leads saved
        """
        saved = 0
        for features in features_list:
            lead_id = features.pop("lead_id", None)
            if lead_id:
                success = self.save_lead_features(UUID(lead_id), features, tenant_id)
                if success:
                    saved += 1
        return saved

    def get_all_lead_features(self, tenant_id: str, limit: int = 1000) -> List[Dict]:
        """Get latest features for all leads in a tenant."""
        snapshots = (
            self.db.query(LeadFeatureSnapshot)
            .filter(LeadFeatureSnapshot.tenant_id == tenant_id)
            .order_by(LeadFeatureSnapshot.created_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {"lead_id": str(s.lead_id), **s.features}
            for s in snapshots
        ]

    # --- Cache Management ---

    def invalidate_lead_cache(self, lead_id: UUID) -> None:
        """Invalidate cached lead features."""
        key = f"{self.LEAD_PREFIX}{lead_id}"
        self.redis.delete_cache(key)

    def invalidate_agent_cache(self, agent_id: UUID) -> None:
        """Invalidate cached agent features."""
        key = f"{self.AGENT_PREFIX}{agent_id}"
        self.redis.delete_cache(key)

    def invalidate_campaign_cache(self, campaign_id: UUID) -> None:
        """Invalidate cached campaign features."""
        key = f"{self.CAMPAIGN_PREFIX}{campaign_id}"
        self.redis.delete_cache(key)

    def get_cache_stats(self) -> Dict[str, int]:
        """Get feature store cache statistics."""
        lead_keys = self.redis.client.keys(f"{self.LEAD_PREFIX}*")
        agent_keys = self.redis.client.keys(f"{self.AGENT_PREFIX}*")
        campaign_keys = self.redis.client.keys(f"{self.CAMPAIGN_PREFIX}*")

        return {
            "cached_leads": len(lead_keys),
            "cached_agents": len(agent_keys),
            "cached_campaigns": len(campaign_keys),
            "total_cached": len(lead_keys) + len(agent_keys) + len(campaign_keys),
        }
