"""
Shared test fixtures and configuration.

Provides:
- Database session (mock or test DB)
- Redis client (mock or test Redis)
- Test data factories
- Common assertions
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.database import Base


# --- Database Fixtures ---

@pytest.fixture
def mock_db():
    """Mock database session."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.scalar.return_value = 0
    return db


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = MagicMock()
    redis.client = MagicMock()
    redis.client.get.return_value = None
    redis.client.set.return_value = True
    redis.client.setex.return_value = True
    redis.client.delete.return_value = 1
    redis.client.exists.return_value = 0
    redis.client.keys.return_value = []
    return redis


# --- Test Data Factories ---

@pytest.fixture
def make_lead():
    """Factory for creating test leads."""
    def _make(**kwargs):
        lead = MagicMock()
        lead.id = kwargs.get("id", uuid4())
        lead.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        lead.first_name = kwargs.get("first_name", "John")
        lead.last_name = kwargs.get("last_name", "Doe")
        lead.phone = kwargs.get("phone", "+1234567890")
        lead.email = kwargs.get("email", "john@example.com")
        lead.source = kwargs.get("source", "google")
        lead.state = kwargs.get("state", "FL")
        lead.lead_score = kwargs.get("lead_score", 75)
        lead.status = kwargs.get("status", "new")
        lead.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        return lead
    return _make


@pytest.fixture
def make_agent():
    """Factory for creating test agents."""
    def _make(**kwargs):
        agent = MagicMock()
        agent.id = kwargs.get("id", uuid4())
        agent.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        agent.user_id = kwargs.get("user_id", uuid4())
        agent.timezone = kwargs.get("timezone", "America/New_York")
        agent.daily_capacity = kwargs.get("daily_capacity", 8)
        agent.weight = kwargs.get("weight", 100)
        agent.status = kwargs.get("status", "active")
        return agent
    return _make


@pytest.fixture
def make_appointment():
    """Factory for creating test appointments."""
    def _make(**kwargs):
        now = datetime.now(timezone.utc)
        appt = MagicMock()
        appt.id = kwargs.get("id", uuid4())
        appt.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        appt.lead_id = kwargs.get("lead_id", uuid4())
        appt.agent_id = kwargs.get("agent_id", uuid4())
        appt.start_time = kwargs.get("start_time", now + timedelta(hours=1))
        appt.end_time = kwargs.get("end_time", now + timedelta(hours=1, minutes=15))
        appt.status = kwargs.get("status", "confirmed")
        appt.disposition = kwargs.get("disposition", None)
        appt.call_duration_seconds = kwargs.get("call_duration_seconds", None)
        appt.booking_source = kwargs.get("booking_source", "ai")
        appt.created_at = kwargs.get("created_at", now)
        return appt
    return _make


@pytest.fixture
def make_conversation():
    """Factory for creating test conversations."""
    def _make(**kwargs):
        conv = MagicMock()
        conv.id = kwargs.get("id", uuid4())
        conv.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        conv.lead_id = kwargs.get("lead_id", uuid4())
        conv.status = kwargs.get("status", "active")
        conv.message_count = kwargs.get("message_count", 0)
        conv.ai_context = kwargs.get("ai_context", {})
        conv.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        return conv
    return _make


@pytest.fixture
def make_message():
    """Factory for creating test messages."""
    def _make(**kwargs):
        msg = MagicMock()
        msg.id = kwargs.get("id", uuid4())
        msg.conversation_id = kwargs.get("conversation_id", uuid4())
        msg.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        msg.sender = kwargs.get("sender", "customer")
        msg.content = kwargs.get("content", "Hello, I need insurance")
        msg.message_type = kwargs.get("message_type", "sms")
        msg.intent = kwargs.get("intent", None)
        msg.sentiment = kwargs.get("sentiment", None)
        msg.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        return msg
    return _make


@pytest.fixture
def make_campaign():
    """Factory for creating test campaigns."""
    def _make(**kwargs):
        campaign = MagicMock()
        campaign.id = kwargs.get("id", uuid4())
        campaign.tenant_id = kwargs.get("tenant_id", str(uuid4()))
        campaign.name = kwargs.get("name", "Test Campaign")
        campaign.tone = kwargs.get("tone", "friendly")
        campaign.status = kwargs.get("status", "active")
        campaign.total_leads = kwargs.get("total_leads", 100)
        campaign.total_contacted = kwargs.get("total_contacted", 80)
        campaign.total_replied = kwargs.get("total_replied", 40)
        campaign.total_booked = kwargs.get("total_booked", 20)
        campaign.total_won = kwargs.get("total_won", 10)
        return campaign
    return _make


# --- Common Assertions ---

def assert_valid_uuid(value):
    """Assert value is a valid UUID string."""
    from uuid import UUID
    UUID(str(value))


def assert_valid_timestamp(value):
    """Assert value is a valid ISO timestamp."""
    if isinstance(value, str):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        assert value.tzinfo is not None


def assert_valid_score(value, min_val=0, max_val=1):
    """Assert value is a valid score between min and max."""
    assert isinstance(value, (int, float))
    assert min_val <= value <= max_val
