"""
Enhanced Model Registry (Phase 38.5)

Manages ML model lifecycle:
- Model versioning
- Performance metrics tracking
- Model promotion (staging → production)
- Rollback support
- A/B testing
- Model metadata

Storage: JSON files in models/ directory
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Storage directories
REGISTRY_DIR = Path("models/registry")
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE_DIR = REGISTRY_DIR / "active"
ACTIVE_DIR.mkdir(exist_ok=True)

ARCHIVE_DIR = REGISTRY_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)


class ModelVersion:
    """Represents a specific model version."""

    def __init__(
        self,
        model_name: str,
        version: str,
        metrics: Dict[str, Any],
        feature_importance: Dict[str, float],
        model_path: str,
        status: str = "staging",
        metadata: Dict[str, Any] = None,
    ):
        self.id = str(uuid4())
        self.model_name = model_name
        self.version = version
        self.metrics = metrics
        self.feature_importance = feature_importance
        self.model_path = model_path
        self.status = status  # staging, production, archived
        self.metadata = metadata or {}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.promoted_at: Optional[str] = None
        self.archived_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "model_name": self.model_name,
            "version": self.version,
            "metrics": self.metrics,
            "feature_importance": self.feature_importance,
            "model_path": self.model_path,
            "status": self.status,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "promoted_at": self.promoted_at,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ModelVersion":
        """Create from dict."""
        mv = cls(
            model_name=data["model_name"],
            version=data["version"],
            metrics=data.get("metrics", {}),
            feature_importance=data.get("feature_importance", {}),
            model_path=data["model_path"],
            status=data.get("status", "staging"),
            metadata=data.get("metadata", {}),
        )
        mv.id = data.get("id", str(uuid4()))
        mv.created_at = data.get("created_at", datetime.now(timezone.utc).isoformat())
        mv.promoted_at = data.get("promoted_at")
        mv.archived_at = data.get("archived_at")
        return mv


class EnhancedModelRegistry:
    """
    Manages ML model lifecycle.

    Features:
    - Register new model versions
    - Promote models to production
    - Archive old models
    - Rollback to previous version
    - Track performance metrics
    - A/B test management
    """

    def register(
        self,
        model_name: str,
        metrics: Dict[str, Any],
        feature_importance: Dict[str, float],
        model_path: str,
        metadata: Dict[str, Any] = None,
    ) -> ModelVersion:
        """
        Register a new model version.

        Args:
            model_name: Name of the model
            metrics: Training metrics
            feature_importance: Feature importance scores
            model_path: Path to saved model file
            metadata: Additional metadata

        Returns:
            ModelVersion object
        """
        # Get next version number
        existing = self.list_versions(model_name)
        version_num = len(existing) + 1
        version = f"v{version_num}"

        # Create version
        mv = ModelVersion(
            model_name=model_name,
            version=version,
            metrics=metrics,
            feature_importance=feature_importance,
            model_path=model_path,
            status="staging",
            metadata=metadata,
        )

        # Save to registry
        self._save_version(mv)

        logger.info(f"Registered model {model_name} {version}")
        return mv

    def get_production(self, model_name: str) -> Optional[ModelVersion]:
        """Get the current production version of a model."""
        versions = self.list_versions(model_name)
        for v in versions:
            if v.status == "production":
                return v
        return None

    def get_latest(self, model_name: str) -> Optional[ModelVersion]:
        """Get the latest version of a model (any status)."""
        versions = self.list_versions(model_name)
        return versions[-1] if versions else None

    def promote(
        self,
        model_name: str,
        version: str,
    ) -> Optional[ModelVersion]:
        """
        Promote a model version to production.

        Archives the current production version (if any).
        """
        # Find the version
        mv = self._load_version(model_name, version)
        if not mv:
            logger.error(f"Version {version} not found for {model_name}")
            return None

        # Archive current production
        current_prod = self.get_production(model_name)
        if current_prod:
            current_prod.status = "archived"
            current_prod.archived_at = datetime.now(timezone.utc).isoformat()
            self._save_version(current_prod)
            logger.info(f"Archived {current_prod.model_name} {current_prod.version}")

        # Promote new version
        mv.status = "production"
        mv.promoted_at = datetime.now(timezone.utc).isoformat()
        self._save_version(mv)

        logger.info(f"Promoted {model_name} {version} to production")
        return mv

    def rollback(self, model_name: str) -> Optional[ModelVersion]:
        """
        Rollback to the previous production version.

        Finds the most recent archived version and promotes it.
        """
        versions = self.list_versions(model_name)
        archived = [v for v in versions if v.status == "archived"]

        if not archived:
            logger.error(f"No archived versions to rollback to for {model_name}")
            return None

        # Get most recent archived
        previous = archived[-1]

        # Archive current production
        current_prod = self.get_production(model_name)
        if current_prod:
            current_prod.status = "archived"
            current_prod.archived_at = datetime.now(timezone.utc).isoformat()
            self._save_version(current_prod)

        # Promote previous
        previous.status = "production"
        previous.promoted_at = datetime.now(timezone.utc).isoformat()
        self._save_version(previous)

        logger.info(f"Rolled back {model_name} to {previous.version}")
        return previous

    def list_versions(self, model_name: str) -> List[ModelVersion]:
        """List all versions of a model."""
        model_dir = REGISTRY_DIR / model_name
        if not model_dir.exists():
            return []

        versions = []
        for version_file in sorted(model_dir.glob("*.json")):
            try:
                with open(version_file) as f:
                    data = json.load(f)
                versions.append(ModelVersion.from_dict(data))
            except Exception as e:
                logger.warning(f"Failed to load version {version_file}: {e}")

        return versions

    def list_models(self) -> List[str]:
        """List all registered model names."""
        if not REGISTRY_DIR.exists():
            return []

        return [
            d.name for d in REGISTRY_DIR.iterdir()
            if d.is_dir() and d.name not in ("active", "archive")
        ]

    def compare_versions(
        self,
        model_name: str,
        version_a: str,
        version_b: str,
    ) -> Dict[str, Any]:
        """Compare two model versions."""
        mv_a = self._load_version(model_name, version_a)
        mv_b = self._load_version(model_name, version_b)

        if not mv_a or not mv_b:
            return {"error": "One or both versions not found"}

        comparison = {
            "model_name": model_name,
            "version_a": version_a,
            "version_b": version_b,
            "metrics_comparison": {},
            "feature_importance_comparison": {},
            "recommendation": "",
        }

        # Compare metrics
        for metric in set(list(mv_a.metrics.keys()) + list(mv_b.metrics.keys())):
            val_a = mv_a.metrics.get(metric, 0)
            val_b = mv_b.metrics.get(metric, 0)
            diff = val_b - val_a
            comparison["metrics_comparison"][metric] = {
                "version_a": val_a,
                "version_b": val_b,
                "diff": round(diff, 4),
                "better": "b" if diff > 0 else ("a" if diff < 0 else "tie"),
            }

        # Recommendation
        auc_a = mv_a.metrics.get("roc_auc", mv_a.metrics.get("accuracy", 0))
        auc_b = mv_b.metrics.get("roc_auc", mv_b.metrics.get("accuracy", 0))

        if auc_b > auc_a:
            comparison["recommendation"] = f"Version {version_b} is better (higher AUC/accuracy)"
        elif auc_a > auc_b:
            comparison["recommendation"] = f"Version {version_a} is better (higher AUC/accuracy)"
        else:
            comparison["recommendation"] = "Versions are equivalent"

        return comparison

    def get_status(self) -> Dict[str, Any]:
        """Get registry status summary."""
        models = self.list_models()
        status = {
            "total_models": len(models),
            "models": {},
        }

        for model_name in models:
            versions = self.list_versions(model_name)
            prod = next((v for v in versions if v.status == "production"), None)
            status["models"][model_name] = {
                "total_versions": len(versions),
                "production_version": prod.version if prod else None,
                "latest_version": versions[-1].version if versions else None,
            }

        return status

    def _save_version(self, mv: ModelVersion) -> None:
        """Save a model version to disk."""
        model_dir = REGISTRY_DIR / mv.model_name
        model_dir.mkdir(exist_ok=True)

        version_file = model_dir / f"{mv.version}.json"
        with open(version_file, "w") as f:
            json.dump(mv.to_dict(), f, indent=2)

    def _load_version(self, model_name: str, version: str) -> Optional[ModelVersion]:
        """Load a specific model version."""
        version_file = REGISTRY_DIR / model_name / f"{version}.json"

        if not version_file.exists():
            return None

        with open(version_file) as f:
            data = json.load(f)

        return ModelVersion.from_dict(data)


# Singleton
model_registry = EnhancedModelRegistry()
