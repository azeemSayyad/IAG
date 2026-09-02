"""
ML Model Registry (Step 21.5)

Manages model versions, deployment, and A/B testing.

Features:
- Model versioning
- Model metadata storage
- A/B testing support
- Model promotion (staging → production)
- Performance tracking
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4


class ModelVersion:
    """Represents a model version."""

    def __init__(
        self,
        model_name: str,
        version: str,
        model_path: str,
        metrics: Dict,
        metadata: Optional[Dict] = None,
    ):
        self.model_id = str(uuid4())
        self.model_name = model_name
        self.version = version
        self.model_path = model_path
        self.metrics = metrics
        self.metadata = metadata or {}
        self.status = "staging"  # staging, production, archived
        self.created_at = datetime.now(timezone.utc)
        self.promoted_at: Optional[datetime] = None
        self.prediction_count = 0

    def to_dict(self) -> Dict:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "version": self.version,
            "model_path": self.model_path,
            "metrics": self.metrics,
            "metadata": self.metadata,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "prediction_count": self.prediction_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ModelVersion":
        model = cls(
            model_name=data["model_name"],
            version=data["version"],
            model_path=data["model_path"],
            metrics=data["metrics"],
            metadata=data.get("metadata", {}),
        )
        model.model_id = data["model_id"]
        model.status = data["status"]
        model.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("promoted_at"):
            model.promoted_at = datetime.fromisoformat(data["promoted_at"])
        model.prediction_count = data.get("prediction_count", 0)
        return model


class ModelRegistry:
    """Registry for managing ML models."""

    def __init__(self, registry_dir: str = "model_registry"):
        self.registry_dir = registry_dir
        os.makedirs(registry_dir, exist_ok=True)
        self._models: Dict[str, List[ModelVersion]] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registry from disk."""
        registry_file = os.path.join(self.registry_dir, "registry.json")
        if os.path.exists(registry_file):
            with open(registry_file, "r") as f:
                data = json.load(f)
                for model_name, versions in data.items():
                    self._models[model_name] = [
                        ModelVersion.from_dict(v) for v in versions
                    ]

    def _save_registry(self) -> None:
        """Save registry to disk."""
        registry_file = os.path.join(self.registry_dir, "registry.json")
        data = {}
        for model_name, versions in self._models.items():
            data[model_name] = [v.to_dict() for v in versions]

        with open(registry_file, "w") as f:
            json.dump(data, f, indent=2)

    def register_model(
        self,
        model_name: str,
        version: str,
        model_path: str,
        metrics: Dict,
        metadata: Optional[Dict] = None,
    ) -> ModelVersion:
        """
        Register a new model version.

        Returns the registered ModelVersion.
        """
        model = ModelVersion(
            model_name=model_name,
            version=version,
            model_path=model_path,
            metrics=metrics,
            metadata=metadata,
        )

        if model_name not in self._models:
            self._models[model_name] = []

        self._models[model_name].append(model)
        self._save_registry()

        return model

    def get_model(
        self,
        model_name: str,
        version: Optional[str] = None,
    ) -> Optional[ModelVersion]:
        """
        Get a model version.

        If version is None, returns the production version.
        """
        if model_name not in self._models:
            return None

        versions = self._models[model_name]

        if version:
            # Find specific version
            for v in versions:
                if v.version == version:
                    return v
            return None
        else:
            # Return production version
            for v in versions:
                if v.status == "production":
                    return v
            # Fall back to latest staging
            if versions:
                return versions[-1]
            return None

    def get_all_versions(self, model_name: str) -> List[ModelVersion]:
        """Get all versions of a model."""
        return self._models.get(model_name, [])

    def promote_model(
        self,
        model_name: str,
        version: str,
    ) -> Optional[ModelVersion]:
        """
        Promote a model version to production.

        Archives the current production model.
        """
        model = self.get_model(model_name, version)
        if not model:
            return None

        # Archive current production model
        for v in self._models.get(model_name, []):
            if v.status == "production":
                v.status = "archived"

        # Promote new model
        model.status = "production"
        model.promoted_at = datetime.now(timezone.utc)

        self._save_registry()

        return model

    def archive_model(
        self,
        model_name: str,
        version: str,
    ) -> Optional[ModelVersion]:
        """Archive a model version."""
        model = self.get_model(model_name, version)
        if not model:
            return None

        model.status = "archived"
        self._save_registry()

        return model

    def increment_prediction_count(
        self,
        model_name: str,
        version: str,
    ) -> None:
        """Increment prediction count for a model."""
        model = self.get_model(model_name, version)
        if model:
            model.prediction_count += 1
            self._save_registry()


class ABTestConfig:
    """Configuration for A/B testing."""

    def __init__(
        self,
        test_name: str,
        model_a: str,
        model_b: str,
        traffic_split: float = 0.5,
        metric: str = "f1_score",
    ):
        self.test_id = str(uuid4())
        self.test_name = test_name
        self.model_a = model_a  # Version A
        self.model_b = model_b  # Version B
        self.traffic_split = traffic_split  # % to model A
        self.metric = metric
        self.status = "active"  # active, completed, cancelled
        self.created_at = datetime.now(timezone.utc)
        self.results: Dict = {}

    def to_dict(self) -> Dict:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "traffic_split": self.traffic_split,
            "metric": self.metric,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "results": self.results,
        }


class ABTestManager:
    """Manages A/B tests for models."""

    def __init__(self, registry: ModelRegistry, test_dir: str = "ab_tests"):
        self.registry = registry
        self.test_dir = test_dir
        os.makedirs(test_dir, exist_ok=True)
        self._tests: Dict[str, ABTestConfig] = {}
        self._load_tests()

    def _load_tests(self) -> None:
        """Load tests from disk."""
        test_file = os.path.join(self.test_dir, "tests.json")
        if os.path.exists(test_file):
            with open(test_file, "r") as f:
                data = json.load(f)
                for test_id, test_data in data.items():
                    config = ABTestConfig(
                        test_name=test_data["test_name"],
                        model_a=test_data["model_a"],
                        model_b=test_data["model_b"],
                        traffic_split=test_data["traffic_split"],
                        metric=test_data["metric"],
                    )
                    config.test_id = test_id
                    config.status = test_data["status"]
                    config.results = test_data.get("results", {})
                    self._tests[test_id] = config

    def _save_tests(self) -> None:
        """Save tests to disk."""
        test_file = os.path.join(self.test_dir, "tests.json")
        data = {tid: t.to_dict() for tid, t in self._tests.items()}
        with open(test_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_test(
        self,
        test_name: str,
        model_a: str,
        model_b: str,
        traffic_split: float = 0.5,
        metric: str = "f1_score",
    ) -> ABTestConfig:
        """Create a new A/B test."""
        config = ABTestConfig(
            test_name=test_name,
            model_a=model_a,
            model_b=model_b,
            traffic_split=traffic_split,
            metric=metric,
        )
        self._tests[config.test_id] = config
        self._save_tests()
        return config

    def get_test(self, test_id: str) -> Optional[ABTestConfig]:
        """Get an A/B test."""
        return self._tests.get(test_id)

    def get_active_tests(self) -> List[ABTestConfig]:
        """Get all active A/B tests."""
        return [t for t in self._tests.values() if t.status == "active"]

    def select_model(self, test_id: str) -> str:
        """
        Select which model to use for a request.

        Returns model version based on traffic split.
        """
        import random

        test = self._tests.get(test_id)
        if not test or test.status != "active":
            return None

        if random.random() < test.traffic_split:
            return test.model_a
        else:
            return test.model_b

    def record_result(
        self,
        test_id: str,
        model_version: str,
        metric_value: float,
    ) -> None:
        """Record a result for an A/B test."""
        test = self._tests.get(test_id)
        if not test:
            return

        if model_version not in test.results:
            test.results[model_version] = {
                "count": 0,
                "total_metric": 0.0,
                "values": [],
            }

        result = test.results[model_version]
        result["count"] += 1
        result["total_metric"] += metric_value
        result["values"].append(metric_value)

        self._save_tests()

    def get_test_results(self, test_id: str) -> Dict:
        """Get A/B test results."""
        test = self._tests.get(test_id)
        if not test:
            return {}

        results = {}
        for version, data in test.results.items():
            if data["count"] > 0:
                results[version] = {
                    "count": data["count"],
                    "mean_metric": data["total_metric"] / data["count"],
                }

        return {
            "test_id": test_id,
            "test_name": test.test_name,
            "status": test.status,
            "results": results,
        }

    def complete_test(self, test_id: str) -> Optional[Dict]:
        """
        Complete an A/B test and determine winner.
        """
        test = self._tests.get(test_id)
        if not test:
            return None

        test.status = "completed"

        # Determine winner
        results = self.get_test_results(test_id)
        results_list = results.get("results", {})

        if not results_list:
            return results

        best_version = max(
            results_list.items(),
            key=lambda x: x[1]["mean_metric"],
        )

        results["winner"] = best_version[0]
        results["winner_metric"] = best_version[1]["mean_metric"]

        self._save_tests()

        return results


# Global instances
model_registry = ModelRegistry()
ab_test_manager = ABTestManager(model_registry)
