"""
Integration tests for Booking Flow

Tests the complete booking pipeline:
1. Slot generation
2. Agent assignment
3. Lock acquisition
4. Appointment creation
5. Confirmation
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.booking.services.distributed_booking import (
    AtomicBookingEngine, ReservationManager, DistributedBookingSystem,
)


class TestBookingFlowIntegration:
    """Integration tests for the booking flow."""

    def test_complete_booking_flow(self, mock_db, make_lead, make_agent):
        """Should complete full booking flow."""
        lead = make_lead()
        agent = make_agent()

        # Mock database queries
        mock_db.query.return_value.filter.return_value.first.return_value = None  # No conflict
        mock_db.query.return_value.filter.return_value.all.return_value = [agent]

        engine = AtomicBookingEngine(mock_db)

        with patch.object(engine, '_check_conflict', return_value=None):
            with patch.object(engine, '_check_agent_available', return_value=True):
                with patch.object(engine, '_release_lock'):
                    with patch.object(engine.redis.client, 'set', return_value=True):
                        result = engine.book_appointment_atomic(
                            tenant_id=str(lead.tenant_id),
                            lead_id=lead.id,
                            agent_id=agent.id,
                            start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                            end_time=datetime.now(timezone.utc) + timedelta(hours=1, minutes=15),
                        )

                        # Should succeed
                        assert mock_db.add.called
                        assert mock_db.commit.called

    def test_booking_with_conflict(self, mock_db, make_lead, make_agent, make_appointment):
        """Should fail when slot is already booked."""
        lead = make_lead()
        agent = make_agent()
        existing = make_appointment(agent_id=agent.id)

        engine = AtomicBookingEngine(mock_db)

        with patch.object(engine, '_check_conflict', return_value=existing):
            with patch.object(engine.redis.client, 'set', return_value=True):
                with patch.object(engine, '_release_lock'):
                    result = engine.book_appointment_atomic(
                        tenant_id=str(lead.tenant_id),
                        lead_id=lead.id,
                        agent_id=agent.id,
                        start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                        end_time=datetime.now(timezone.utc) + timedelta(hours=1, minutes=15),
                    )

                    assert result["success"] == False
                    assert result["error_type"] == "conflict"

    def test_reservation_prevents_double_booking(self, mock_db):
        """Should prevent double booking via reservation."""
        system = DistributedBookingSystem(mock_db)
        system.reservations.redis = MagicMock()
        system.reservations.redis.client.get.return_value = '{"reservation_id": "r1", "lead_id": "other-lead"}'

        now = datetime.now(timezone.utc) + timedelta(hours=1)
        result = system.reservations.check_reservation("agent-1", now)
        assert result is not None
        assert result["lead_id"] == "other-lead"


class TestSlotGeneration:
    """Integration tests for slot generation."""

    def test_slots_within_business_hours(self):
        """All slots should be within 10AM-9PM."""
        from app.booking.services.slots import generate_slots_for_date

        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)

        for slot in slots:
            assert 10 <= slot.start_time.hour < 21

    def test_slots_are_15_minutes(self):
        """All slots should be 15 minutes."""
        from app.booking.services.slots import generate_slots_for_date

        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)

        for slot in slots:
            duration = (slot.end_time - slot.start_time).total_seconds()
            assert duration == 900

    def test_slots_do_not_overlap(self):
        """Slots should not overlap."""
        from app.booking.services.slots import generate_slots_for_date

        today = datetime.now(timezone.utc).date()
        slots = generate_slots_for_date(today)

        for i in range(len(slots) - 1):
            assert slots[i].end_time <= slots[i + 1].start_time


class TestLocking:
    """Integration tests for distributed locking."""

    def test_lock_prevents_concurrent_access(self):
        """Lock should prevent concurrent access."""
        with patch("app.booking.services.locking.redis_service") as mock_redis:
            mock_redis.client.set.return_value = False  # Lock already held

            from app.booking.services.locking import acquire_slot_lock
            result = acquire_slot_lock("t1", "a1", datetime.now(timezone.utc), "l1")
            assert result == False

    def test_lock_owner_can_release(self):
        """Only lock owner should be able to release."""
        with patch("app.booking.services.locking.redis_service") as mock_redis:
            mock_redis.client.get.return_value = "lead-1:1234567890"
            mock_redis.client.delete.return_value = 1

            from app.booking.services.locking import release_slot_lock
            result = release_slot_lock("t1", "a1", datetime.now(timezone.utc), "lead-1")
            assert result == True

    def test_non_owner_cannot_release(self):
        """Non-owner should not be able to release lock."""
        with patch("app.booking.services.locking.redis_service") as mock_redis:
            mock_redis.client.get.return_value = "other-lead:1234567890"

            from app.booking.services.locking import release_slot_lock
            result = release_slot_lock("t1", "a1", datetime.now(timezone.utc), "lead-1")
            assert result == False
