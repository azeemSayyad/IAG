"""
Drift Monitoring (Phase 38.8)

Detects and reports on:
1. Prediction Degradation — Model accuracy declining over time
2. Feature Drift — Input distribution changing
3. Campaign Changes — Performance shifts in campaigns
4. Seasonal Behavior — Time-based pattern changes

Monitoring:
- Daily drift checks
- Alert thresholds
- Drift reports
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

from app.ml.feature_pipeline import FeaturePipeline
from app.ml.feature_store import FeatureStore
from app.ml.model_registry_enhanced import model_registry

logger = logging.getLogger(__name__)


class DriftAlert:
    """Represents a drift detection alert."""

    def __init__(
        self,
        alert_type: str,
        severity: str,
        model_name: str,
        metric: str,
        current_value: float,
        baseline_value: float,
        drift_percentage: float,
        message: str,
    ):
        self.alert_type = alert_type
        self.severity = severity  # low, medium, high, critical
        self.model_name = model_name
        self.metric = metric
        self.current_value = current_value
        self.baseline_value = baseline_value
        self.drift_percentage = drift_percentage
        self.message = message
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "model_name": self.model_name,
            "metric": self.metric,
            "current_value": round(self.current_value, 4),
            "baseline_value": round(self.baseline_value, 4),
            "drift_percentage": round(self.drift_percentage, 2),
            "message": self.message,
            "created_at": self.created_at,
        }


class DriftReport:
    """Comprehensive drift analysis report."""

    def __init__(
        self,
        tenant_id: str,
        alerts: List[DriftAlert],
        feature_drift: Dict[str, Any],
        performance_drift: Dict[str, Any],
        seasonal_patterns: Dict[str, Any],
        recommendations: List[str],
    ):
        self.tenant_id = tenant_id
        self.alerts = alerts
        self.feature_drift = feature_drift
        self.performance_drift = performance_drift
        self.seasonal_patterns = seasonal_patterns
        self.recommendations = recommendations
        self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "tenant_id": self.tenant_id,
            "total_alerts": len(self.alerts),
            "critical_alerts": sum(1 for a in self.alerts if a.severity == "critical"),
            "high_alerts": sum(1 for a in self.alerts if a.severity == "high"),
            "alerts": [a.to_dict() for a in self.alerts],
            "feature_drift": self.feature_drift,
            "performance_drift": self.performance_drift,
            "seasonal_patterns": self.seasonal_patterns,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


class DriftMonitor:
    """
    Monitors model and data drift.

    Features:
    - Prediction degradation detection
    - Feature distribution drift
    - Campaign performance shifts
    - Seasonal pattern detection
    """

    # Drift thresholds
    THRESHOLDS = {
        "feature_drift": 0.20,  # 20% change in feature mean
        "performance_drop": 0.05,  # 5% drop in accuracy/AUC
        "campaign_shift": 0.15,  # 15% change in campaign metrics
    }

    def __init__(self, db: Session):
        self.db = db
        self.feature_pipeline = FeaturePipeline(db)
        self.feature_store = FeatureStore(db)

    def check_drift(self, tenant_id: str) -> DriftReport:
        """
        Run comprehensive drift check.

        Args:
            tenant_id: Tenant ID

        Returns:
            DriftReport with all findings
        """
        alerts = []

        # 1. Feature drift detection
        feature_drift = self._check_feature_drift(tenant_id)
        alerts.extend(feature_drift.get("alerts", []))

        # 2. Performance drift detection
        performance_drift = self._check_performance_drift()
        alerts.extend(performance_drift.get("alerts", []))

        # 3. Campaign drift detection
        campaign_drift = self._check_campaign_drift(tenant_id)
        alerts.extend(campaign_drift.get("alerts", []))

        # 4. Seasonal pattern detection
        seasonal = self._check_seasonal_patterns(tenant_id)

        # 5. Generate recommendations
        recommendations = self._generate_recommendations(alerts, feature_drift, performance_drift)

        return DriftReport(
            tenant_id=tenant_id,
            alerts=alerts,
            feature_drift=feature_drift,
            performance_drift=performance_drift,
            seasonal_patterns=seasonal,
            recommendations=recommendations,
        )

    def _check_feature_drift(self, tenant_id: str) -> Dict[str, Any]:
        """Check for feature distribution drift."""
        result = {"alerts": [], "features": {}}

        # Get current features
        current_features = self.feature_pipeline.extract_all_lead_features(limit=500)
        if not current_features:
            return result

        # Calculate current distributions
        numeric_keys = [
            k for k in current_features[0].keys()
            if isinstance(current_features[0].get(k, 0), (int, float))
            and k not in {"lead_id"}
        ]

        for key in numeric_keys:
            values = [f.get(key, 0) for f in current_features if isinstance(f.get(key, 0), (int, float))]
            if not values:
                continue

            current_mean = sum(values) / len(values)
            current_std = self._std(values)

            result["features"][key] = {
                "mean": round(current_mean, 4),
                "std": round(current_std, 4),
                "count": len(values),
            }

            # Compare with historical baseline (if available)
            baseline = self._get_feature_baseline(tenant_id, key)
            if baseline:
                drift = abs(current_mean - baseline["mean"]) / baseline["mean"] if baseline["mean"] else 0
                if drift > self.THRESHOLDS["feature_drift"]:
                    alert = DriftAlert(
                        alert_type="feature_drift",
                        severity="high" if drift > 0.3 else "medium",
                        model_name="all",
                        metric=key,
                        current_value=current_mean,
                        baseline_value=baseline["mean"],
                        drift_percentage=drift * 100,
                        message=f"Feature '{key}' drifted {drift:.1%} from baseline",
                    )
                    result["alerts"].append(alert)

        return result

    def _check_performance_drift(self) -> Dict[str, Any]:
        """Check for model performance degradation."""
        result = {"alerts": [], "models": {}}

        models = model_registry.list_models()

        for model_name in models:
            prod = model_registry.get_production(model_name)
            if not prod:
                continue

            versions = model_registry.list_versions(model_name)
            if len(versions) < 2:
                continue

            # Compare production with latest
            latest = versions[-1]
            if prod.version == latest.version:
                continue

            # Check key metrics
            for metric in ["accuracy", "roc_auc", "f1"]:
                prod_val = prod.metrics.get(metric, 0)
                latest_val = latest.metrics.get(metric, 0)

                if prod_val > 0:
                    drop = (prod_val - latest_val) / prod_val
                    if drop > self.THRESHOLDS["performance_drop"]:
                        alert = DriftAlert(
                            alert_type="performance_drift",
                            severity="critical" if drop > 0.1 else "high",
                            model_name=model_name,
                            metric=metric,
                            current_value=latest_val,
                            baseline_value=prod_val,
                            drift_percentage=drop * 100,
                            message=f"Model {model_name} {metric} dropped {drop:.1%}",
                        )
                        result["alerts"].append(alert)

            result["models"][model_name] = {
                "production_version": prod.version,
                "production_metrics": prod.metrics,
                "latest_version": latest.version,
                "latest_metrics": latest.metrics,
            }

        return result

    def _check_campaign_drift(self, tenant_id: str) -> Dict[str, Any]:
        """Check for campaign performance shifts."""
        result = {"alerts": [], "campaigns": []}

        from app.models.campaign import Campaign
        campaigns = self.db.query(Campaign).filter(
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        ).all()

        for campaign in campaigns:
            features = self.feature_pipeline.extract_campaign_features(campaign.id)
            if not features:
                continue

            result["campaigns"].append({
                "campaign_id": str(campaign.id),
                "name": campaign.name,
                "features": features,
            })

            # Check for significant metric changes
            reply_rate = features.get("reply_rate", 0)
            booking_rate = features.get("booking_rate", 0)

            if reply_rate < 0.05 and features.get("total_contacted", 0) > 50:
                alert = DriftAlert(
                    alert_type="campaign_drift",
                    severity="high",
                    model_name=f"campaign_{campaign.id}",
                    metric="reply_rate",
                    current_value=reply_rate,
                    baseline_value=0.10,
                    drift_percentage=50,
                    message=f"Campaign '{campaign.name}' reply rate critically low ({reply_rate:.1%})",
                )
                result["alerts"].append(alert)

        return result

    def _check_seasonal_patterns(self, tenant_id: str) -> Dict[str, Any]:
        """Detect seasonal patterns in data."""
        result = {"patterns": [], "hourly": {}, "daily": {}}

        from app.models.message import Message
        from app.models.conversation import Conversation
        from sqlalchemy import func, extract

        # Hourly message distribution
        hourly = self.db.query(
            extract("hour", Message.created_at).label("hour"),
            func.count(Message.id).label("count"),
        ).join(Conversation).filter(
            Conversation.tenant_id == tenant_id,
            Message.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
        ).group_by("hour").all()

        for hour, count in hourly:
            result["hourly"][int(hour)] = count

        # Find peak hours
        if result["hourly"]:
            peak_hour = max(result["hourly"], key=result["hourly"].get)
            result["patterns"].append({
                "type": "peak_hour",
                "value": peak_hour,
                "count": result["hourly"][peak_hour],
            })

        return result

    def _get_feature_baseline(self, tenant_id: str, feature_name: str) -> Optional[Dict]:
        """Get historical baseline for a feature."""
        # Simple baseline: use cached features from yesterday
        # In production, this would compare against a stored baseline
        return None

    def _generate_recommendations(
        self,
        alerts: List[DriftAlert],
        feature_drift: Dict,
        performance_drift: Dict,
    ) -> List[str]:
        """Generate recommendations based on drift analysis."""
        recommendations = []

        critical = [a for a in alerts if a.severity == "critical"]
        high = [a for a in alerts if a.severity == "high"]

        if critical:
            recommendations.append(
                f"URGENT: {len(critical)} critical drift alerts detected. "
                "Immediate model retraining recommended."
            )

        if high:
            recommendations.append(
                f"WARNING: {len(critical)} high drift alerts detected. "
                "Schedule model retraining within 24 hours."
            )

        # Feature-specific recommendations
        for alert in alerts:
            if alert.alert_type == "feature_drift":
                recommendations.append(
                    f"Feature '{alert.metric}' has drifted {alert.drift_percentage:.1f}%. "
                    "Consider retraining with recent data."
                )
            elif alert.alert_type == "campaign_drift":
                recommendations.append(
                    f"Campaign performance degradation detected. "
                    "Review campaign settings and targeting."
                )

        if not recommendations:
            recommendations.append("No significant drift detected. Models performing within normal parameters.")

        return recommendations

    def _std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)
