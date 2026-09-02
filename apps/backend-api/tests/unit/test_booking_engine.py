"""
Unit tests for Booking Engine (Phase 36.1 + 47)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.booking.services.distributed_booking import (
    DDLConstraints, AtomicBookingEngine, ReservationManager,
    DistributedBookingQueue, DistributedBookingSystem,
    RESERVATION_TTL, QUEUE_LOCK_TTL,
)
from app.booking.services.slots import generate_slots_for_date, TimeSlot
from app.booking.services.locking import acquire_slot_lock, release_slot_lock


class TestDDLConstraints:
    """Tests for database constraints."""

    def test_migration_sql_contains_exclude(self):
        """Migration should contain EXCLUDE constraint."""
        sql = DDLConstraints.get_migration_sql()
        assert "EXCLUDE USING gist" in sql
        assert "btree_gist" in sql

    def test_migration_sql_contains_tstzrange(self):
        """Migration should use tstzrange."""
        sql = DDLConstraints.get_migration_sql()
        assert "tstzrange" in sql

    def test_rollback_sql_removes_constraint(self):
        """Rollback should remove constraint."""
        sql = DDLConstraints.get_rollback_sql()
        assert "DROP CONSTRAINT" in sql
        assert "exclude_overlapping_appointments" in sql


class TestReservationManager:
    """Tests for reservation management."""

    def test_reserve_slot(self):
        """Should reserve a slot."""
        mgr = ReservationManager()
        mgr.redis = MagicMock()
        mgr.redis.client.setex.return_value = True

        reservation_id = mgr.reserve_slot("agent-1", datetime.now(timezone.utc), "lead-1")
        assert reservation_id is not None
        assert len(reservation_id) > 0

    def test_check_reservation(self):
        """Should check if slot is reserved."""
        mgr = ReservationManager()
        mgr.redis = MagicMock()
        mgr.redis.client.get.return_value = None

        result = mgr.check_reservation("agent-1", datetime.now(timezone.utc))
        assert result is None

    def test_release_reservation(self):
        """Should release a reservation."""
        mgr = ReservationManager()
        mgr.redis = MagicMock()
        mgr.redis.client.delete.return_value = 1

        result = mgr.release_reservation("agent-1", datetime.now(timezone.utc))
        assert result == True

    def test_reservation_ttl(self):
        """Reservation TTL should be 5 minutes."""
        assert RESERVATION_TTL == 300


class TestDistributedBookingQueue:
    """Tests for booking queue."""

    def test_enqueue_booking(self):
        """Should enqueue a booking request."""
        queue = DistributedBookingQueue()
        queue.redis = MagicMock()
        queue.redis.client.zadd.return_value = 1

        job_id = queue.enqueue_booking(
            tenant_id="t1",
            lead_id="l1",
            agent_id="a1",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        assert job_id is not None

    def test_dequeue_empty_queue(self):
        """Should return None for empty queue."""
        queue = DistributedBookingQueue()
        queue.redis = MagicMock()
        queue.redis.client.zpopmax.return_value = []

        result = queue.dequeue_booking()
        assert result is None

    def test_queue_size(self):
        """Should return queue size."""
        queue = DistributedBookingQueue()
        queue.redis = MagicMock()
        queue.redis.client.zcard.return_value = 5

        assert queue.get_queue_size() == 5


class TestSlotGeneration:
    """Tests for slot generation."""

    def test_generate_slots_for_date(self):
        """Should generate slots for a date."""
        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)
        assert len(slots) > 0

    def test_slot_duration(self):
        """Slots should be 15 minutes."""
        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)
        if slots:
            slot = slots[0]
            duration = (slot.end_time - slot.start_time).total_seconds()
            assert duration == 900  # 15 minutes

    def test_business_hours(self):
        """Slots should be within business hours (10AM-9PM)."""
        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)
        for slot in slots:
            hour = slot.start_time.hour
            assert 10 <= hour < 21


class TestLocking:
    """Tests for Redis locking."""

    def test_acquire_lock(self):
        """Should acquire a lock."""
        with patch("app.booking.services.locking.redis_service") as mock_redis:
            mock_redis.client.set.return_value = True
            result = acquire_slot_lock("tenant-1", "agent-1", datetime.now(timezone.utc), "lead-1")
            assert result == True

    def test_release_lock(self):
        """Should release a lock."""
        with patch("app.booking.services.locking.redis_service") as mock_redis:
            mock_redis.client.get.return_value = "lead-1:1234567890"
            mock_redis.client.delete.return_value = 1
            result = release_slot_lock("tenant-1", "agent-1", datetime.now(timezone.utc), "lead-1")
            assert result == True
