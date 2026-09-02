"""
Cost Optimization (Step 26.4)

Tracks and optimizes infrastructure costs.

Cost Categories:
- GPU costs (AI inference)
- SMS costs (Engage Clouds)
- Infrastructure costs (AWS)
- Database costs (RDS, ElastiCache)
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from enum import Enum

from app.core.redis import RedisService


class CostCategory(str, Enum):
    """Cost categories."""
    AI_INFERENCE = "ai_inference"
    SMS = "sms"
    DATABASE = "database"
    REDIS = "redis"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"


# Cost rates (USD)
COST_RATES = {
    CostCategory.AI_INFERENCE: {
        "llama3": 0.0001,  # per 1K tokens
        "mistral": 0.00005,
        "deepseek-llm": 0.00008,
    },
    CostCategory.SMS: {
        "outbound": 0.0079,  # per SMS
        "inbound": 0.0075,
    },
    CostCategory.DATABASE: {
        "rds_hourly": 0.17,  # per hour (db.r6g.large)
        "storage_gb": 0.115,  # per GB-month
        "io_request": 0.10,  # per 1M requests
    },
    CostCategory.REDIS: {
        "hourly": 0.034,  # per hour (cache.t3.medium)
    },
    CostCategory.COMPUTE: {
        "fargate_vcpu_hour": 0.04048,
        "fargate_memory_gb_hour": 0.004445,
    },
    CostCategory.STORAGE: {
        "s3_gb": 0.023,  # per GB-month
        "s3_request": 0.0004,  # per 1K requests
    },
}


class CostTracker:
    """Tracks infrastructure costs."""

    def __init__(self):
        self.redis = RedisService()

    def track_cost(
        self,
        category: CostCategory,
        amount: float,
        details: Optional[Dict] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        """
        Track a cost event.

        Args:
            category: Cost category
            amount: Cost in USD
            details: Additional details
            tenant_id: Tenant ID (for tenant-specific costs)
        """
        timestamp = datetime.now(timezone.utc)
        date_key = timestamp.strftime("%Y-%m-%d")
        hour_key = timestamp.strftime("%Y-%m-%d:%H")

        # Increment daily total
        self.redis.client.incrbyfloat(f"cost:daily:{date_key}", amount)

        # Increment hourly total
        self.redis.client.incrbyfloat(f"cost:hourly:{hour_key}", amount)

        # Increment category total
        self.redis.client.incrbyfloat(f"cost:category:{category.value}:{date_key}", amount)

        # Track tenant-specific cost
        if tenant_id:
            self.redis.client.incrbyfloat(
                f"cost:tenant:{tenant_id}:{date_key}",
                amount
            )

        # Store cost event
        event = {
            "category": category.value,
            "amount": amount,
            "details": details or {},
            "tenant_id": tenant_id,
            "timestamp": timestamp.isoformat(),
        }
        self.redis.client.lpush("cost:events", str(event))
        self.redis.client.ltrim("cost:events", 0, 9999)

    def get_daily_cost(self, date: Optional[str] = None) -> float:
        """Get total cost for a day."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return float(self.redis.client.get(f"cost:daily:{date}") or 0)

    def get_hourly_cost(self, hour: Optional[str] = None) -> float:
        """Get total cost for an hour."""
        if not hour:
            hour = datetime.now(timezone.utc).strftime("%Y-%m-%d:%H")
        return float(self.redis.client.get(f"cost:hourly:{hour}") or 0)

    def get_category_cost(
        self,
        category: CostCategory,
        date: Optional[str] = None,
    ) -> float:
        """Get cost for a category."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return float(self.redis.client.get(f"cost:category:{category.value}:{date}") or 0)

    def get_tenant_cost(
        self,
        tenant_id: str,
        date: Optional[str] = None,
    ) -> float:
        """Get cost for a tenant."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return float(self.redis.client.get(f"cost:tenant:{tenant_id}:{date}") or 0)

    def get_cost_summary(self, days: int = 30) -> Dict:
        """
        Get cost summary for the last N days.
        """
        total_cost = 0
        category_costs = {}
        daily_costs = []

        for i in range(days):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            daily = self.get_daily_cost(date)
            total_cost += daily
            daily_costs.append({"date": date, "cost": daily})

            for category in CostCategory:
                cat_cost = self.get_category_cost(category, date)
                category_costs[category.value] = category_costs.get(category.value, 0) + cat_cost

        return {
            "total_cost": round(total_cost, 2),
            "daily_average": round(total_cost / days, 2) if days > 0 else 0,
            "category_costs": {k: round(v, 2) for k, v in category_costs.items()},
            "daily_costs": daily_costs[:7],  # Last 7 days
        }


class CostOptimizer:
    """Optimizes costs through various strategies."""

    def __init__(self):
        self.tracker = CostTracker()

    def calculate_sms_cost(self, count: int, direction: str = "outbound") -> float:
        """Calculate SMS cost."""
        rate = COST_RATES[CostCategory.SMS].get(direction, 0.0079)
        cost = count * rate
        self.tracker.track_cost(
            CostCategory.SMS,
            cost,
            {"count": count, "direction": direction}
        )
        return cost

    def calculate_ai_cost(
        self,
        model: str,
        tokens: int,
    ) -> float:
        """Calculate AI inference cost."""
        rate = COST_RATES[CostCategory.AI_INFERENCE].get(model, 0.0001)
        cost = (tokens / 1000) * rate
        self.tracker.track_cost(
            CostCategory.AI_INFERENCE,
            cost,
            {"model": model, "tokens": tokens}
        )
        return cost

    def get_optimization_recommendations(self) -> List[Dict]:
        """
        Get cost optimization recommendations.
        """
        recommendations = []

        # Check SMS costs
        sms_cost = self.tracker.get_category_cost(CostCategory.SMS)
        if sms_cost > 100:  # $100/day threshold
            recommendations.append({
                "category": "sms",
                "current_cost": sms_cost,
                "recommendation": "Consider batch SMS sending during off-peak hours",
                "potential_savings": sms_cost * 0.1,
            })

        # Check AI costs
        ai_cost = self.tracker.get_category_cost(CostCategory.AI_INFERENCE)
        if ai_cost > 50:  # $50/day threshold
            recommendations.append({
                "category": "ai_inference",
                "current_cost": ai_cost,
                "recommendation": "Enable response caching to reduce API calls",
                "potential_savings": ai_cost * 0.2,
            })

        # Check compute costs
        compute_cost = self.tracker.get_category_cost(CostCategory.COMPUTE)
        if compute_cost > 200:  # $200/day threshold
            recommendations.append({
                "category": "compute",
                "current_cost": compute_cost,
                "recommendation": "Right-size instances based on utilization",
                "potential_savings": compute_cost * 0.15,
            })

        return recommendations

    def calculate_cost_per_lead(self, tenant_id: str) -> float:
        """Calculate cost per lead for a tenant."""
        from app.models.lead import Lead
        from app.core.database import get_db

        db = next(get_db())
        try:
            # Get lead count for today
            today = datetime.now(timezone.utc).date()
            lead_count = db.query(Lead).filter(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= today,
            ).count()

            if lead_count == 0:
                return 0

            # Get tenant cost
            tenant_cost = self.tracker.get_tenant_cost(tenant_id)

            return tenant_cost / lead_count

        finally:
            db.close()

    def calculate_cost_per_appointment(self, tenant_id: str) -> float:
        """Calculate cost per appointment for a tenant."""
        from app.models.appointment import Appointment
        from app.core.database import get_db

        db = next(get_db())
        try:
            # Get appointment count for today
            today = datetime.now(timezone.utc).date()
            appt_count = db.query(Appointment).filter(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= today,
            ).count()

            if appt_count == 0:
                return 0

            # Get tenant cost
            tenant_cost = self.tracker.get_tenant_cost(tenant_id)

            return tenant_cost / appt_count

        finally:
            db.close()


class BudgetAlert:
    """Budget monitoring and alerting."""

    def __init__(self):
        self.tracker = CostTracker()
        self.redis = RedisService()

    def set_budget(
        self,
        category: CostCategory,
        daily_limit: float,
        monthly_limit: float,
    ) -> None:
        """Set budget limits."""
        self.redis.set_cache(
            f"budget:{category.value}:daily",
            daily_limit,
            ttl=86400 * 30,
        )
        self.redis.set_cache(
            f"budget:{category.value}:monthly",
            monthly_limit,
            ttl=86400 * 30,
        )

    def check_budget(self, category: CostCategory) -> Dict:
        """
        Check if budget is exceeded.
        """
        daily_limit = float(self.redis.get_cache(f"budget:{category.value}:daily") or 0)
        monthly_limit = float(self.redis.get_cache(f"budget:{category.value}:monthly") or 0)

        daily_cost = self.tracker.get_category_cost(category)

        # Calculate monthly cost
        monthly_cost = 0
        for i in range(30):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            monthly_cost += self.tracker.get_category_cost(category, date)

        return {
            "category": category.value,
            "daily_cost": daily_cost,
            "daily_limit": daily_limit,
            "daily_exceeded": daily_cost > daily_limit if daily_limit > 0 else False,
            "daily_percentage": (daily_cost / daily_limit * 100) if daily_limit > 0 else 0,
            "monthly_cost": monthly_cost,
            "monthly_limit": monthly_limit,
            "monthly_exceeded": monthly_cost > monthly_limit if monthly_limit > 0 else False,
            "monthly_percentage": (monthly_cost / monthly_limit * 100) if monthly_limit > 0 else 0,
        }

    def get_alerts(self) -> List[Dict]:
        """
        Get budget alerts.
        """
        alerts = []

        for category in CostCategory:
            status = self.check_budget(category)

            if status["daily_exceeded"]:
                alerts.append({
                    "category": category.value,
                    "type": "daily_exceeded",
                    "message": f"{category.value} daily budget exceeded: ${status['daily_cost']:.2f} / ${status['daily_limit']:.2f}",
                    "severity": "critical",
                })
            elif status["daily_percentage"] > 80:
                alerts.append({
                    "category": category.value,
                    "type": "daily_warning",
                    "message": f"{category.value} daily budget at {status['daily_percentage']:.0f}%",
                    "severity": "warning",
                })

            if status["monthly_exceeded"]:
                alerts.append({
                    "category": category.value,
                    "type": "monthly_exceeded",
                    "message": f"{category.value} monthly budget exceeded: ${status['monthly_cost']:.2f} / ${status['monthly_limit']:.2f}",
                    "severity": "critical",
                })

        return alerts


# Global instances
cost_tracker = CostTracker()
cost_optimizer = CostOptimizer()
budget_alert = BudgetAlert()
