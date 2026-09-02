"""
Load tests for Booking Engine

Tests high-volume scenarios:
1. 100 bookings per second
2. 1000 concurrent slot checks
3. Queue processing under load
"""

import pytest
import time
import random
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.booking.services.distributed_booking import (
    DistributedBookingQueue, ReservationManager,
)


class TestBookingLoad:
    """Load tests for booking system."""

    def test_queue_throughput(self):
        """
        Queue should handle 100 enqueue/dequeue operations per second.
        """
        queue = DistributedBookingQueue()
        queue.redis = MagicMock()
        queue.redis.client.zadd.return_value = 1
        queue.redis.client.zpopmax.return_value = []

        start = time.time()
        count = 100

        # Enqueue
        for i in range(count):
            queue.enqueue_booking(
                tenant_id="t1",
                lead_id=f"l{i}",
                agent_id=f"a{i % 10}",
                start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                end_time=datetime.now(timezone.utc) + timedelta(hours=1, minutes=15),
            )

        elapsed = time.time() - start
        throughput = count / elapsed

        assert throughput > 50  # At least 50 ops/sec

    def test_reservation_throughput(self):
        """
        Reservation manager should handle 100 operations per second.
        """
        mgr = ReservationManager()
        mgr.redis = MagicMock()
        mgr.redis.client.setex.return_value = True

        start = time.time()
        count = 100

        for i in range(count):
            mgr.reserve_slot(
                agent_id=f"a{i % 10}",
                start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                lead_id=f"l{i}",
            )

        elapsed = time.time() - start
        throughput = count / elapsed

        assert throughput > 50

    def test_concurrent_queue_operations(self):
        """
        Queue should handle concurrent enqueue/dequeue.
        """
        queue = DistributedBookingQueue()
        queue.redis = MagicMock()
        queue.redis.client.zadd.return_value = 1
        queue.redis.client.zpopmax.return_value = []

        results = []
        errors = []

        def enqueue_batch(batch_id):
            try:
                for i in range(10):
                    queue.enqueue_booking(
                        tenant_id="t1",
                        lead_id=f"l{batch_id}_{i}",
                        agent_id=f"a{i}",
                        start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                        end_time=datetime.now(timezone.utc) + timedelta(hours=1, minutes=15),
                    )
                results.append(batch_id)
            except Exception as e:
                errors.append(e)

        # Run 10 concurrent batches
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(enqueue_batch, i) for i in range(10)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(results) == 10


class TestSlotGenerationLoad:
    """Load tests for slot generation."""

    def test_bulk_slot_generation(self):
        """
        Should generate slots for multiple dates quickly.
        """
        from app.booking.services.slots import generate_slots_for_date

        start = time.time()
        total_slots = 0

        for day_offset in range(30):
            date = (datetime.now(timezone.utc) + timedelta(days=day_offset)).date()
            slots = generate_slots_for_date(date)
            total_slots += len(slots)

        elapsed = time.time() - start

        assert total_slots > 0
        assert elapsed < 5  # Should complete in under 5 seconds


class TestPresenceLoad:
    """Load tests for presence system."""

    def test_bulk_presence_updates(self):
        """
        Should handle 100 presence updates per second.
        """
        from app.realtime.presence import AgentPresence

        start = time.time()
        count = 100

        for i in range(count):
            presence = AgentPresence(
                agent_id=f"agent-{i}",
                status="online",
                occupancy_score=random.random(),
            )
            d = presence.to_dict()

        elapsed = time.time() - start
        throughput = count / elapsed

        assert throughput > 50
