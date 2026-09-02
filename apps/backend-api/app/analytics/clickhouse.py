"""
ClickHouse Analytics Service

Provides:
- Event tracking
- Fast aggregations
- Time-series analysis
- Materialized views
"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from uuid import UUID

import httpx

from app.core.config import settings


class ClickHouseClient:
    """ClickHouse HTTP client."""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or "http://clickhouse:8123"
        self.database = "analytics"

    async def execute(self, query: str, params: Dict = None) -> List[Dict]:
        """Execute a query and return results."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}",
                params={"database": self.database},
                content=query,
                headers={"Content-Type": "text/plain"},
            )
            if response.status_code != 200:
                raise Exception(f"ClickHouse error: {response.text}")

            if query.strip().upper().startswith("SELECT"):
                # Parse tab-separated response
                lines = response.text.strip().split("\n")
                if len(lines) < 2:
                    return []

                headers = lines[0].split("\t")
                results = []
                for line in lines[1:]:
                    values = line.split("\t")
                    row = dict(zip(headers, values))
                    results.append(row)
                return results

            return [{"status": "ok"}]

    async def insert_event(
        self,
        tenant_id: str,
        event_type: str,
        event_category: str,
        user_id: str = None,
        lead_id: str = None,
        appointment_id: str = None,
        campaign_id: str = None,
        agent_id: str = None,
        properties: Dict = None,
    ):
        """Insert an analytics event."""
        import json

        query = """
        INSERT INTO events (tenant_id, event_type, event_category, user_id, lead_id, appointment_id, campaign_id, agent_id, properties)
        VALUES
        """

        props_map = ", ".join([f"'{k}', '{v}'" for k, v in (properties or {}).items()])
        values = f"""
        ('{tenant_id}', '{event_type}', '{event_category}', '{user_id or ""}', '{lead_id or ""}', '{appointment_id or ""}', '{campaign_id or ""}', '{agent_id or ""}', {{{props_map}}})
        """

        await self.execute(query + values)

    async def get_event_counts(
        self,
        tenant_id: str,
        event_type: str = None,
        start_date: date = None,
        end_date: date = None,
    ) -> Dict:
        """Get event counts for a date range."""
        if not start_date:
            start_date = date.today() - timedelta(days=30)
        if not end_date:
            end_date = date.today()

        where_clauses = [
            f"tenant_id = '{tenant_id}'",
            f"date >= '{start_date}'",
            f"date <= '{end_date}'",
        ]

        if event_type:
            where_clauses.append(f"event_type = '{event_type}'")

        where = " AND ".join(where_clauses)

        query = f"""
        SELECT
            event_type,
            count() as count
        FROM events
        WHERE {where}
        GROUP BY event_type
        ORDER BY count DESC
        """

        results = await self.execute(query)
        return {r["event_type"]: int(r["count"]) for r in results}

    async def get_hourly_metrics(
        self,
        tenant_id: str,
        event_type: str,
        hours: int = 24,
    ) -> List[Dict]:
        """Get hourly metrics for the last N hours."""
        query = f"""
        SELECT
            hour,
            event_count
        FROM hourly_metrics
        WHERE tenant_id = '{tenant_id}'
            AND event_type = '{event_type}'
            AND hour >= now() - INTERVAL {hours} HOUR
        ORDER BY hour
        """

        return await self.execute(query)


# Singleton
clickhouse_client = ClickHouseClient()
