"""
Online Inference Service (Phase 38.6)

Real-time prediction APIs:
- /predict/booking — Booking probability for a lead
- /predict/conversion — Conversion probability
- /predict/no-show — No-show probability
- /predict/batch — Batch predictions
- /predict/lead-score — Composite lead score

Flow:
1. Load features from feature store
2. Load model from registry
3. Run inference
4. Return predictions with confidence
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ml.training_pipeline import TrainingPipeline
from app.ml.feature_store import FeatureStore
from app.ml.feature_pipeline import FeaturePipeline
from app.ml.model_registry_enhanced import model_registry

logger = logging.getLogger(__name__)


class InferenceResult:
    """Result of a single inference."""

    def __init__(
        self,
        prediction: float,
        confidence: float,
        model_version: str,
        features_used: Dict[str, float],
        explanation: str,
    ):
        self.prediction = prediction
        self.confidence = confidence
        self.model_version = model_version
        self.features_used = features_used
        self.explanation = explanation

    def to_dict(self) -> Dict:
        return {
            "prediction": round(self.prediction, 4),
            "confidence": round(self.confidence, 4),
            "model_version": self.model_version,
            "features_used": {k: round(v, 4) for k, v in self.features_used.items()},
            "explanation": self.explanation,
        }


class OnlineInferenceService:
    """
    Real-time ML inference service.

    Uses trained models to make predictions on live data.
    """

    def __init__(self, db: Session):
        self.db = db
        self.training = TrainingPipeline(db)
        self.feature_store = FeatureStore(db)
        self.feature_pipeline = FeaturePipeline(db)

    # --- Single Predictions ---

    def predict_booking(self, lead_id: UUID, tenant_id: str) -> Optional[InferenceResult]:
        """
        Predict booking probability for a lead.

        Args:
            lead_id: Lead UUID
            tenant_id: Tenant ID

        Returns:
            InferenceResult with prediction and explanation
        """
        return self._predict("booking_model", lead_id, tenant_id)

    def predict_conversion(self, lead_id: UUID, tenant_id: str) -> Optional[InferenceResult]:
        """Predict conversion probability for a lead."""
        return self._predict("conversion_model", lead_id, tenant_id)

    def predict_noshow(self, lead_id: UUID, tenant_id: str) -> Optional[InferenceResult]:
        """Predict no-show probability for a lead."""
        return self._predict("noshow_model", lead_id, tenant_id)

    def predict_lead_score(self, lead_id: UUID, tenant_id: str) -> Dict[str, Any]:
        """
        Compute composite lead score from all models.

        Combines booking, conversion, and quality predictions.
        """
        booking = self._predict("booking_model", lead_id, tenant_id)
        conversion = self._predict("conversion_model", lead_id, tenant_id)
        quality = self._predict("lead_quality_model", lead_id, tenant_id)

        scores = {}
        weights = {"booking": 0.4, "conversion": 0.4, "quality": 0.2}
        total_weight = 0
        composite = 0

        if booking:
            scores["booking_probability"] = booking.prediction
            composite += booking.prediction * weights["booking"]
            total_weight += weights["booking"]

        if conversion:
            scores["conversion_probability"] = conversion.prediction
            composite += conversion.prediction * weights["conversion"]
            total_weight += weights["conversion"]

        if quality:
            scores["quality_score"] = quality.prediction
            composite += quality.prediction * weights["quality"]
            total_weight += weights["quality"]

        if total_weight > 0:
            composite = composite / total_weight

        # Determine tier
        if composite >= 0.8:
            tier = "hot"
        elif composite >= 0.6:
            tier = "warm"
        elif composite >= 0.4:
            tier = "cool"
        else:
            tier = "cold"

        return {
            "lead_id": str(lead_id),
            "composite_score": round(composite, 4),
            "tier": tier,
            "component_scores": scores,
            "model_versions": {
                "booking": booking.model_version if booking else None,
                "conversion": conversion.model_version if conversion else None,
                "quality": quality.model_version if quality else None,
            },
        }

    # --- Batch Predictions ---

    def predict_batch(
        self,
        model_name: str,
        lead_ids: List[UUID],
        tenant_id: str,
    ) -> List[Dict]:
        """
        Batch predictions for multiple leads.

        Args:
            model_name: Model to use
            lead_ids: List of lead UUIDs
            tenant_id: Tenant ID

        Returns:
            List of prediction results
        """
        results = []
        for lead_id in lead_ids:
            result = self._predict(model_name, lead_id, tenant_id)
            if result:
                results.append({
                    "lead_id": str(lead_id),
                    **result.to_dict(),
                })
            else:
                results.append({
                    "lead_id": str(lead_id),
                    "prediction": None,
                    "error": "Model not available",
                })

        return results

    # --- Best Agent Match ---

    def predict_best_agent(
        self,
        lead_id: UUID,
        tenant_id: str,
    ) -> Optional[Dict]:
        """
        Find the best agent for a lead based on features.

        Uses agent performance features to find optimal match.
        """
        from app.models.agent import Agent

        agents = self.db.query(Agent).filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        ).all()

        if not agents:
            return None

        best_agent = None
        best_score = -1

        for agent in agents:
            agent_features = self.feature_pipeline.extract_agent_features(agent.id)
            if not agent_features:
                continue

            # Simple scoring: win_rate * 0.6 + utilization * 0.4
            score = (
                agent_features.get("win_rate", 0) * 0.6
                + (1 - agent_features.get("no_show_rate", 0)) * 0.4
            )

            if score > best_score:
                best_score = score
                best_agent = agent

        if best_agent:
            features = self.feature_pipeline.extract_agent_features(best_agent.id)
            return {
                "agent_id": str(best_agent.id),
                "score": round(best_score, 4),
                "features": features,
            }

        return None

    # --- Optimization Recommendations ---

    def get_optimization_recommendations(
        self,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """
        Generate optimization recommendations based on ML analysis.

        Analyzes patterns and suggests improvements.
        """
        recommendations = []

        # Analyze lead features
        lead_features = self.feature_pipeline.extract_all_lead_features(limit=100)
        if lead_features:
            # Check reply rates
            reply_rates = [f.get("reply_ratio", 0) for f in lead_features]
            avg_reply = sum(reply_rates) / len(reply_rates) if reply_rates else 0

            if avg_reply < 0.3:
                recommendations.append({
                    "area": "engagement",
                    "priority": "high",
                    "finding": f"Average reply rate is {avg_reply:.1%}",
                    "suggestion": "Improve outreach message quality and personalization",
                })

            # Check response times
            response_times = [f.get("avg_response_time_seconds", 0) for f in lead_features if f.get("avg_response_time_seconds", 0) > 0]
            if response_times:
                avg_time = sum(response_times) / len(response_times)
                if avg_time > 3600:
                    recommendations.append({
                        "area": "response_time",
                        "priority": "medium",
                        "finding": f"Average response time is {avg_time/3600:.1f} hours",
                        "suggestion": "Implement faster follow-up or AI auto-response",
                    })

        # Analyze agent features
        agent_features = self.feature_pipeline.extract_all_agent_features()
        if agent_features:
            # Check utilization
            utils = [f.get("utilization_rate", 0) for f in agent_features]
            avg_util = sum(utils) / len(utils) if utils else 0

            if avg_util < 0.5:
                recommendations.append({
                    "area": "utilization",
                    "priority": "high",
                    "finding": f"Average agent utilization is {avg_util:.1%}",
                    "suggestion": "Implement emergency fill engine or reduce agent count",
                })

            # Check no-show rates
            noshow_rates = [f.get("no_show_rate", 0) for f in agent_features]
            avg_noshow = sum(noshow_rates) / len(noshow_rates) if noshow_rates else 0

            if avg_noshow > 0.2:
                recommendations.append({
                    "area": "no_show",
                    "priority": "high",
                    "finding": f"Average no-show rate is {avg_noshow:.1%}",
                    "suggestion": "Implement reminder system and no-show prediction",
                })

        return {
            "tenant_id": tenant_id,
            "recommendations": recommendations,
            "total": len(recommendations),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # --- Internal Methods ---

    def _predict(
        self,
        model_name: str,
        lead_id: UUID,
        tenant_id: str,
    ) -> Optional[InferenceResult]:
        """Internal prediction method."""
        # 1. Get features from store
        features_dict = self.feature_store.get_lead_features(lead_id)

        # 2. If not cached, extract fresh
        if not features_dict:
            features_dict = self.feature_pipeline.extract_lead_features(lead_id)
            if features_dict:
                self.feature_store.save_lead_features(lead_id, features_dict, tenant_id)

        if not features_dict:
            return None

        # 3. Get model
        model_data = self.training.load_model(model_name)
        if not model_data:
            return None

        # 4. Prepare features for model
        feature_names = model_data.get("feature_names", [])
        weights = model_data.get("weights", [])

        if not weights:
            return None

        # Build feature vector (in order)
        feature_vector = []
        used_features = {}
        for name in feature_names:
            value = features_dict.get(name, 0)
            feature_vector.append(float(value))
            used_features[name] = float(value)

        # 5. Make prediction
        predictions = self.training.predict(model_name, [feature_vector])
        if not predictions:
            return None

        prediction = predictions[0]

        # 6. Calculate confidence
        # Higher weight features → higher confidence
        top_features = sorted(
            zip(feature_names, weights),
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:3]
        confidence = sum(abs(w) for _, w in top_features) / len(weights) if weights else 0.5

        # 7. Generate explanation
        explanation = self._generate_explanation(
            model_name, prediction, top_features, used_features
        )

        # Get version
        version_info = model_registry.get_production(model_name)
        version = version_info.version if version_info else "v0"

        return InferenceResult(
            prediction=prediction,
            confidence=min(confidence, 1.0),
            model_version=version,
            features_used=used_features,
            explanation=explanation,
        )

    def _generate_explanation(
        self,
        model_name: str,
        prediction: float,
        top_features: List[tuple],
        features: Dict[str, float],
    ) -> str:
        """Generate human-readable explanation of prediction."""
        parts = []

        # Prediction summary
        if prediction > 0.7:
            parts.append(f"High {model_name.replace('_', ' ')} probability ({prediction:.0%})")
        elif prediction > 0.4:
            parts.append(f"Moderate {model_name.replace('_', ' ')} probability ({prediction:.0%})")
        else:
            parts.append(f"Low {model_name.replace('_', ' ')} probability ({prediction:.0%})")

        # Top contributing factors
        if top_features:
            factors = []
            for name, weight in top_features:
                value = features.get(name, 0)
                if weight > 0:
                    factors.append(f"{name}={value:.2f} (positive)")
                else:
                    factors.append(f"{name}={value:.2f} (negative)")
            parts.append(f"Key factors: {', '.join(factors[:3])}")

        return " | ".join(parts)
