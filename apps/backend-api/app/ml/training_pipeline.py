"""
ML Training Pipelines (Phase 38.3)

Trains ML models for:
1. Booking Probability — Will this lead book?
2. Conversion Prediction — Will this lead convert?
3. No-Show Prediction — Will this lead show up?
4. Lead Quality — How valuable is this lead?
5. Best Send Time — When should we contact?

Uses pure Python math (no numpy dependency).
Can be upgraded to sklearn/XGBoost when available.

Pipeline:
1. Collect training data from feature store
2. Prepare features and labels
3. Train model with cross-validation
4. Evaluate performance
5. Save to model registry
"""

import json
import logging
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Model storage directory
MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)


def _mean(values: List[float]) -> float:
    """Calculate mean of a list."""
    return sum(values) / len(values) if values else 0


def _std(values: List[float]) -> float:
    """Calculate standard deviation."""
    if len(values) < 2:
        return 0
    m = _mean(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _correlation(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(x) != len(y) or len(x) < 2:
        return 0

    n = len(x)
    mean_x = _mean(x)
    mean_y = _mean(y)
    std_x = _std(x)
    std_y = _std(y)

    if std_x == 0 or std_y == 0:
        return 0

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / (n - 1)
    return cov / (std_x * std_y)


def _dot_product(a: List[float], b: List[float]) -> float:
    """Calculate dot product of two vectors."""
    return sum(x * y for x, y in zip(a, b))


def _normalize(values: List[float]) -> List[float]:
    """Normalize values to unit vector."""
    magnitude = math.sqrt(sum(v * v for v in values))
    if magnitude == 0:
        return values
    return [v / magnitude for v in values]


class TrainingResult:
    """Result of a training run."""

    def __init__(
        self,
        success: bool,
        model_name: str,
        metrics: Dict[str, Any],
        feature_importance: Dict[str, float],
        model_path: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.model_name = model_name
        self.metrics = metrics
        self.feature_importance = feature_importance
        self.model_path = model_path
        self.error = error

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "model_name": self.model_name,
            "metrics": self.metrics,
            "feature_importance": self.feature_importance,
            "model_path": self.model_path,
            "error": self.error,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }


class TrainingPipeline:
    """
    Trains ML models from production data.

    Uses correlation-based feature weighting with pure Python.
    Can be upgraded to sklearn/XGBoost when available.
    """

    def __init__(self, db: Session):
        self.db = db

    def train_booking_model(
        self,
        features: List[List[float]],
        labels: List[int],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """Train booking probability model."""
        return self._train_model(features, labels, "booking_model", feature_names)

    def train_conversion_model(
        self,
        features: List[List[float]],
        labels: List[int],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """Train conversion prediction model."""
        return self._train_model(features, labels, "conversion_model", feature_names)

    def train_noshow_model(
        self,
        features: List[List[float]],
        labels: List[int],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """Train no-show prediction model."""
        return self._train_model(features, labels, "noshow_model", feature_names)

    def train_lead_quality_model(
        self,
        features: List[List[float]],
        labels: List[int],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """Train lead quality scoring model."""
        return self._train_model(features, labels, "lead_quality_model", feature_names)

    def train_send_time_model(
        self,
        features: List[List[float]],
        labels: List[int],
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """Train best send time prediction model."""
        return self._train_model(features, labels, "send_time_model", feature_names)

    def _train_model(
        self,
        features: List[List[float]],
        labels: List[int],
        model_name: str,
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Core training logic using correlation-based feature weighting.

        This is a lightweight ML approach that works without sklearn.
        """
        # Validate inputs
        if len(features) < 10:
            return {
                "success": False,
                "error": "Insufficient data (need >= 10 samples)",
                "model_name": model_name,
            }

        n_features = len(features[0])
        if not feature_names:
            feature_names = [f"feature_{i}" for i in range(n_features)]

        # Calculate feature correlations with label
        correlations = []
        for i in range(n_features):
            feature_col = [row[i] for row in features]
            corr = _correlation(feature_col, [float(l) for l in labels])
            correlations.append(corr if not math.isnan(corr) else 0)

        # Calculate weights from absolute correlations
        abs_corr = [abs(c) for c in correlations]
        total = sum(abs_corr)
        if total > 0:
            weights = [a / total for a in abs_corr]
        else:
            weights = [1.0 / n_features] * n_features

        # Cross-validation (simple k-fold)
        cv_scores = self._cross_validate(features, labels, weights, k=5)

        # Full training accuracy
        predictions = self._predict_with_weights(features, weights)
        accuracy = self._accuracy(predictions, labels)
        precision = self._precision(predictions, labels)
        recall = self._recall(predictions, labels)
        f1 = self._f1(precision, recall)

        # Feature importance
        feature_importance = {}
        for name, weight, corr in zip(feature_names, weights, correlations):
            feature_importance[name] = round(weight, 4)

        # Save model
        model_path = self._save_model(weights, correlations, feature_names, model_name)

        return {
            "success": True,
            "model_name": model_name,
            "model_path": str(model_path),
            "metrics": {
                "accuracy": round(accuracy, 4),
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "cv_accuracy_mean": round(_mean(cv_scores), 4),
                "cv_accuracy_std": round(_std(cv_scores), 4),
                "n_samples": len(labels),
                "n_positive": sum(labels),
                "n_negative": len(labels) - sum(labels),
            },
            "feature_importance": feature_importance,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }

    def _cross_validate(
        self,
        features: List[List[float]],
        labels: List[int],
        weights: List[float],
        k: int = 5,
    ) -> List[float]:
        """Simple k-fold cross-validation."""
        n = len(features)
        fold_size = n // k
        scores = []

        for i in range(k):
            start = i * fold_size
            end = start + fold_size if i < k - 1 else n

            # Test set
            test_features = features[start:end]
            test_labels = labels[start:end]

            # Train set (everything else)
            train_features = features[:start] + features[end:]
            train_labels = labels[:start] + labels[end:]

            # Train weights on train set
            n_features = len(features[0])
            train_weights = []
            for j in range(n_features):
                feature_col = [row[j] for row in train_features]
                corr = _correlation(feature_col, [float(l) for l in train_labels])
                train_weights.append(abs(corr) if not math.isnan(corr) else 0)

            total = sum(train_weights)
            if total > 0:
                train_weights = [w / total for w in train_weights]
            else:
                train_weights = [1.0 / n_features] * n_features

            # Evaluate on test set
            predictions = self._predict_with_weights(test_features, train_weights)
            accuracy = self._accuracy(predictions, test_labels)
            scores.append(accuracy)

        return scores

    def _predict_with_weights(
        self,
        features: List[List[float]],
        weights: List[float],
    ) -> List[int]:
        """Make binary predictions using weights."""
        scores = [_dot_product(row, weights) for row in features]
        threshold = _mean(scores) if scores else 0.5
        return [1 if s > threshold else 0 for s in scores]

    def _predict_proba(
        self,
        features: List[List[float]],
        weights: List[float],
    ) -> List[float]:
        """Make probability predictions using weights."""
        scores = [_dot_product(row, weights) for row in features]
        # Normalize to 0-1
        if scores:
            min_s = min(scores)
            max_s = max(scores)
            range_s = max_s - min_s
            if range_s > 0:
                return [(s - min_s) / range_s for s in scores]
        return [0.5] * len(scores)

    def _accuracy(self, predictions: List[int], labels: List[int]) -> float:
        """Calculate accuracy."""
        correct = sum(1 for p, l in zip(predictions, labels) if p == l)
        return correct / len(labels) if labels else 0

    def _precision(self, predictions: List[int], labels: List[int]) -> float:
        """Calculate precision."""
        tp = sum(1 for p, l in zip(predictions, labels) if p == 1 and l == 1)
        fp = sum(1 for p, l in zip(predictions, labels) if p == 1 and l == 0)
        return tp / (tp + fp) if (tp + fp) > 0 else 0

    def _recall(self, predictions: List[int], labels: List[int]) -> float:
        """Calculate recall."""
        tp = sum(1 for p, l in zip(predictions, labels) if p == 1 and l == 1)
        fn = sum(1 for p, l in zip(predictions, labels) if p == 0 and l == 1)
        return tp / (tp + fn) if (tp + fn) > 0 else 0

    def _f1(self, precision: float, recall: float) -> float:
        """Calculate F1 score."""
        if precision + recall == 0:
            return 0
        return 2 * (precision * recall) / (precision + recall)

    def _save_model(
        self,
        weights: List[float],
        correlations: List[float],
        feature_names: List[str],
        model_name: str,
    ) -> Path:
        """Save trained model to disk."""
        model_path = MODEL_DIR / f"{model_name}.json"

        with open(model_path, "w") as f:
            json.dump({
                "weights": weights,
                "correlations": correlations,
                "feature_names": feature_names,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }, f)

        logger.info(f"Saved model to {model_path}")
        return model_path

    def load_model(self, model_name: str) -> Optional[Dict]:
        """Load a trained model from disk."""
        model_path = MODEL_DIR / f"{model_name}.json"

        if not model_path.exists():
            return None

        with open(model_path) as f:
            return json.load(f)

    def predict(
        self,
        model_name: str,
        features: List[List[float]],
    ) -> Optional[List[float]]:
        """
        Make predictions using a trained model.

        Returns:
            List of prediction scores (0-1) or None if model not found
        """
        model_data = self.load_model(model_name)
        if not model_data:
            return None

        weights = model_data["weights"]
        return self._predict_proba(features, weights)
