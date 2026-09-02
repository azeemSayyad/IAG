"""
Unit tests for ML Pipeline (Phase 38)
"""

import pytest
from unittest.mock import MagicMock

from app.ml.training_pipeline import TrainingPipeline, TrainingResult
from app.ml.feature_pipeline import FeaturePipeline
from app.ml.model_registry_enhanced import EnhancedModelRegistry, ModelVersion
from app.ml.inference_service import OnlineInferenceService, InferenceResult
from app.ml.drift_monitor import DriftMonitor, DriftAlert, DriftReport


class TestTrainingPipeline:
    """Tests for ML training."""

    def test_train_booking_model(self):
        """Should train a booking model."""
        import random
        random.seed(42)

        pipeline = TrainingPipeline(MagicMock())
        features = [[random.gauss(0, 1) for _ in range(3)] for _ in range(100)]
        labels = [1 if f[0] + f[1] > 0 else 0 for f in features]

        result = pipeline.train_booking_model(features, labels, ["f1", "f2", "f3"])
        assert result["success"] == True
        assert result["metrics"]["accuracy"] > 0.7

    def test_insufficient_data(self):
        """Should fail with insufficient data."""
        pipeline = TrainingPipeline(MagicMock())
        result = pipeline.train_booking_model([[1, 2]], [0])
        assert result["success"] == False
        assert "Insufficient" in result["error"]

    def test_single_class_labels(self):
        """Should handle single class labels (zero correlation)."""
        pipeline = TrainingPipeline(MagicMock())
        result = pipeline.train_booking_model(
            [[1, 2]] * 50,
            [0] * 50,
        )
        # With single class, correlations are 0 but training still succeeds
        assert result["success"] == True

    def test_model_save_load(self):
        """Should save and load model."""
        import random
        random.seed(42)

        pipeline = TrainingPipeline(MagicMock())
        features = [[random.gauss(0, 1) for _ in range(3)] for _ in range(100)]
        labels = [1 if f[0] + f[1] > 0 else 0 for f in features]

        pipeline.train_booking_model(features, labels, ["f1", "f2", "f3"])
        loaded = pipeline.load_model("booking_model")
        assert loaded is not None
        assert "weights" in loaded

    def test_predict(self):
        """Should make predictions."""
        import random
        random.seed(42)

        pipeline = TrainingPipeline(MagicMock())
        features = [[random.gauss(0, 1) for _ in range(3)] for _ in range(100)]
        labels = [1 if f[0] + f[1] > 0 else 0 for f in features]

        pipeline.train_booking_model(features, labels, ["f1", "f2", "f3"])
        predictions = pipeline.predict("booking_model", [[0.5, 0.3, 0.1]])
        assert predictions is not None
        assert len(predictions) == 1
        assert 0 <= predictions[0] <= 1


class TestModelRegistry:
    """Tests for model registry."""

    def test_register_model(self):
        """Should register a model version."""
        import uuid
        registry = EnhancedModelRegistry()
        unique_name = f"test_register_{uuid.uuid4().hex[:8]}"
        mv = registry.register(
            model_name=unique_name,
            metrics={"accuracy": 0.85},
            feature_importance={"f1": 0.5},
            model_path="/tmp/test.json",
        )
        assert mv.version == "v1"
        assert mv.status == "staging"

    def test_promote_model(self):
        """Should promote model to production."""
        registry = EnhancedModelRegistry()
        mv = registry.register(
            model_name="test_promote",
            metrics={"accuracy": 0.90},
            feature_importance={"f1": 0.5},
            model_path="/tmp/test.json",
        )
        promoted = registry.promote("test_promote", "v1")
        assert promoted.status == "production"

    def test_rollback_model(self):
        """Should rollback to previous version."""
        registry = EnhancedModelRegistry()

        # Register v1 and promote
        registry.register("test_rollback", {"accuracy": 0.80}, {"f1": 0.5}, "/tmp/v1.json")
        registry.promote("test_rollback", "v1")

        # Register v2 and promote
        registry.register("test_rollback", {"accuracy": 0.85}, {"f1": 0.5}, "/tmp/v2.json")
        registry.promote("test_rollback", "v2")

        # Rollback
        rolled = registry.rollback("test_rollback")
        assert rolled.version == "v1"
        assert rolled.status == "production"

    def test_compare_versions(self):
        """Should compare model versions."""
        registry = EnhancedModelRegistry()
        registry.register("test_compare", {"accuracy": 0.80}, {"f1": 0.5}, "/tmp/v1.json")
        registry.register("test_compare", {"accuracy": 0.90}, {"f1": 0.5}, "/tmp/v2.json")

        comparison = registry.compare_versions("test_compare", "v1", "v2")
        assert "metrics_comparison" in comparison
        assert "recommendation" in comparison


class TestInferenceResult:
    """Tests for inference result."""

    def test_inference_result(self):
        """Should create valid inference result."""
        result = InferenceResult(
            prediction=0.85,
            confidence=0.72,
            model_version="v1",
            features_used={"engagement": 0.8},
            explanation="High probability",
        )
        d = result.to_dict()
        assert d["prediction"] == 0.85
        assert d["confidence"] == 0.72


class TestDriftMonitor:
    """Tests for drift monitoring."""

    def test_drift_alert(self):
        """Should create valid drift alert."""
        alert = DriftAlert(
            alert_type="feature_drift",
            severity="high",
            model_name="booking_model",
            metric="engagement",
            current_value=0.45,
            baseline_value=0.60,
            drift_percentage=25.0,
            message="Engagement dropped",
        )
        d = alert.to_dict()
        assert d["alert_type"] == "feature_drift"
        assert d["severity"] == "high"

    def test_drift_report(self):
        """Should create valid drift report."""
        report = DriftReport(
            tenant_id="t1",
            alerts=[],
            feature_drift={},
            performance_drift={},
            seasonal_patterns={},
            recommendations=["No issues"],
        )
        d = report.to_dict()
        assert d["total_alerts"] == 0
        assert len(d["recommendations"]) == 1
