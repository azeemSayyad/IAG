"""
Retraining System (Phase 38.7)

Automated model retraining:
- Nightly retraining at 2 AM (via Celery beat)
- Incremental training on new data
- Automatic model promotion if improved
- Retraining status tracking

Schedule:
- 2:00 AM — Collect new training data
- 2:15 AM — Retrain all models
- 2:30 AM — Evaluate and promote if better
- 2:45 AM — Generate drift report
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ml.training_pipeline import TrainingPipeline
from app.ml.feature_pipeline import FeaturePipeline
from app.ml.feature_store import FeatureStore
from app.ml.model_registry_enhanced import model_registry
from app.ml.inference_service import OnlineInferenceService

logger = logging.getLogger(__name__)


class RetrainingResult:
    """Result of a retraining run."""

    def __init__(
        self,
        model_name: str,
        success: bool,
        old_version: Optional[str] = None,
        new_version: Optional[str] = None,
        old_metrics: Optional[Dict] = None,
        new_metrics: Optional[Dict] = None,
        promoted: bool = False,
        error: Optional[str] = None,
    ):
        self.model_name = model_name
        self.success = success
        self.old_version = old_version
        self.new_version = new_version
        self.old_metrics = old_metrics or {}
        self.new_metrics = new_metrics or {}
        self.promoted = promoted
        self.error = error

    def to_dict(self) -> Dict:
        return {
            "model_name": self.model_name,
            "success": self.success,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "old_metrics": self.old_metrics,
            "new_metrics": self.new_metrics,
            "promoted": self.promoted,
            "error": self.error,
            "retrained_at": datetime.now(timezone.utc).isoformat(),
        }


class RetrainingSystem:
    """
    Automated model retraining system.

    Features:
    - Nightly retraining on fresh data
    - Automatic promotion if model improves
    - Rollback if new model is worse
    - Retraining status tracking
    """

    def __init__(self, db: Session):
        self.db = db
        self.training = TrainingPipeline(db)
        self.feature_pipeline = FeaturePipeline(db)
        self.feature_store = FeatureStore(db)

    def retrain_all_models(self, tenant_id: str) -> List[RetrainingResult]:
        """
        Retrain all models with fresh data.

        Called nightly by Celery beat at 2 AM.

        Returns:
            List of RetrainingResult for each model
        """
        results = []

        # Models to retrain
        models = [
            ("booking_model", "booking"),
            ("conversion_model", "conversion"),
            ("noshow_model", "noshow"),
            ("lead_quality_model", "lead_quality"),
        ]

        for model_name, label_type in models:
            try:
                result = self.retrain_model(model_name, tenant_id, label_type)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to retrain {model_name}: {e}")
                results.append(RetrainingResult(
                    model_name=model_name,
                    success=False,
                    error=str(e),
                ))

        return results

    def retrain_model(
        self,
        model_name: str,
        tenant_id: str,
        label_type: str = "booking",
    ) -> RetrainingResult:
        """
        Retrain a single model.

        Args:
            model_name: Name of the model to retrain
            tenant_id: Tenant ID
            label_type: Type of labels to use

        Returns:
            RetrainingResult with comparison
        """
        # 1. Get current production version
        current_prod = model_registry.get_production(model_name)
        old_metrics = current_prod.metrics if current_prod else {}

        # 2. Collect training data
        training_data = self._collect_training_data(tenant_id, label_type)
        if not training_data:
            return RetrainingResult(
                model_name=model_name,
                success=False,
                error="No training data available",
            )

        features, labels, feature_names = training_data

        # 3. Train new model
        train_result = self.training._train_model(
            features=features,
            labels=labels,
            model_name=model_name,
            feature_names=feature_names,
        )

        if not train_result.get("success"):
            return RetrainingResult(
                model_name=model_name,
                success=False,
                error=train_result.get("error", "Training failed"),
            )

        # 4. Register new version
        new_version = model_registry.register(
            model_name=model_name,
            metrics=train_result["metrics"],
            feature_importance=train_result["feature_importance"],
            model_path=train_result["model_path"],
            metadata={"retraining": True, "tenant_id": tenant_id},
        )

        # 5. Compare with current production
        should_promote = self._should_promote(
            old_metrics, train_result["metrics"]
        )

        promoted = False
        if should_promote:
            model_registry.promote(model_name, new_version.version)
            promoted = True
            logger.info(f"Auto-promoted {model_name} {new_version.version}")
        else:
            # Archive the new version (not better)
            new_version.status = "archived"
            logger.info(f"New {model_name} {new_version.version} not better, keeping current")

        return RetrainingResult(
            model_name=model_name,
            success=True,
            old_version=current_prod.version if current_prod else None,
            new_version=new_version.version,
            old_metrics=old_metrics,
            new_metrics=train_result["metrics"],
            promoted=promoted,
        )

    def _collect_training_data(
        self,
        tenant_id: str,
        label_type: str,
    ) -> Optional[tuple]:
        """
        Collect training data from feature store.

        Returns:
            (features, labels, feature_names) or None
        """
        # Get all lead features
        all_features = self.feature_store.get_all_lead_features(tenant_id, limit=5000)

        if len(all_features) < 20:
            logger.warning(f"Insufficient training data: {len(all_features)} samples")
            return None

        # Determine feature names (exclude non-feature fields)
        exclude_keys = {"lead_id", "status", "source", "state", "response_speed_bucket", "current_sentiment", "sentiment_trend"}
        feature_names = [
            k for k in all_features[0].keys()
            if k not in exclude_keys and isinstance(all_features[0].get(k, 0), (int, float))
        ]

        if not feature_names:
            return None

        # Build feature matrix
        features = []
        labels = []

        for feat in all_features:
            # Feature vector
            vector = [float(feat.get(f, 0)) for f in feature_names]
            features.append(vector)

            # Label based on type
            if label_type == "booking":
                label = 1 if feat.get("has_booked", False) else 0
            elif label_type == "conversion":
                label = 1 if feat.get("completed_appointments", 0) > 0 else 0
            elif label_type == "noshow":
                label = 1 if feat.get("no_show_rate", 0) > 0.5 else 0
            elif label_type == "lead_quality":
                label = 1 if feat.get("lead_score", 0) > 60 else 0
            else:
                label = 0

            labels.append(label)

        return features, labels, feature_names

    def _should_promote(
        self,
        old_metrics: Dict,
        new_metrics: Dict,
    ) -> bool:
        """
        Determine if new model should replace production.

        Promotes if new model is better on key metric.
        """
        # Compare on ROC AUC or accuracy
        old_score = old_metrics.get("roc_auc", old_metrics.get("accuracy", 0))
        new_score = new_metrics.get("roc_auc", new_metrics.get("accuracy", 0))

        # Promote if new is at least 1% better
        return new_score > old_score + 0.01

    def get_retraining_status(self) -> Dict[str, Any]:
        """Get retraining system status."""
        models = model_registry.list_models()
        status = {
            "models": {},
            "total_models": len(models),
        }

        for model_name in models:
            versions = model_registry.list_versions(model_name)
            prod = next((v for v in versions if v.status == "production"), None)
            latest = versions[-1] if versions else None

            status["models"][model_name] = {
                "production_version": prod.version if prod else None,
                "production_metrics": prod.metrics if prod else {},
                "latest_version": latest.version if latest else None,
                "total_versions": len(versions),
            }

        return status


# --- Celery Task ---

def create_retraining_task():
    """Create Celery task for nightly retraining."""
    try:
        from app.workers.app.celery_app import celery_app
    except ImportError:
        logger.warning("Celery not available, retraining task not created")
        return None

    @celery_app.task(name="ml.retrain_all_models")
    def retrain_all_models_task():
        """Nightly model retraining task."""
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            system = RetrainingSystem(db)
            # Get all tenant IDs
            from app.models.tenant import Tenant
            tenants = db.query(Tenant).filter(Tenant.status == "active").all()

            all_results = []
            for tenant in tenants:
                results = system.retrain_all_models(str(tenant.id))
                all_results.extend([r.to_dict() for r in results])

            return {
                "status": "completed",
                "tenants_processed": len(tenants),
                "results": all_results,
            }
        except Exception as e:
            logger.error(f"Retraining failed: {e}")
            return {"status": "failed", "error": str(e)}
        finally:
            db.close()

    return retrain_all_models_task
