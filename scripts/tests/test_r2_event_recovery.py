"""Canonical Test Suite for R2 Event Recovery (Section 42).

Covers:
- closing and reopening database preserves pending deliveries
- closing and reopening database preserves retryable deliveries
- closing and reopening database preserves dead-letter deliveries
- claim metadata in PROCESSING (IN_FLIGHT) survives reopening
- replay does not duplicate deliveries
- manual stale claim recovery (recover_stale_claims) functions deterministically
- transaction rollback leaves zero orphan events and zero partial deliveries
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import sqlite3

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
    RetryPolicy,
)
from scripts.runtime.events.engine import EventEngine
from scripts.runtime.events.store import SqliteEventStore


@pytest.fixture
def memory_store():
    """Provides an isolated in-memory SqliteEventStore."""
    store = SqliteEventStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def sample_event():
    """Provides a valid canonical DomainEvent."""
    return DomainEvent.create(
        event_type="squad.recovery.test",
        work_item_id="US-R2-300",
        project_id="agent_squad",
        source="recovery-test",
        correlation_id="corr-recov-001",
        causation_id="cause-recov-001",
        payload={"task": "crash_recovery", "priority": "high"},
    )


class TestR2EventRecovery:
    """Rigorous verification of crash recovery, persistence across restarts, and atomic rollback."""

    def test_reopening_database_preserves_pending_deliveries(self, tmp_path, sample_event):
        """Validates that PENDING deliveries survive process shutdown and database reopen."""
        db_path = tmp_path / "recovery_pending.db"

        # Session 1: emit event with pending deliveries
        store1 = SqliteEventStore(db_path)
        engine1 = EventEngine(store=store1)
        engine1.emit(sample_event, subscribers=["service:audit", "service:metrics"])
        store1.close()

        # Session 2: reopen database
        store2 = SqliteEventStore(db_path)
        engine2 = EventEngine(store=store2)
        try:
            pending = engine2.list_pending_deliveries()
            assert len(pending) == 2
            subscribers = {d.subscriber for d in pending}
            assert subscribers == {"service:audit", "service:metrics"}
            assert all(d.status == DeliveryStatus.PENDING for d in pending)
            assert all(d.event_id == sample_event.event_id for d in pending)
        finally:
            store2.close()

    def test_reopening_database_preserves_retryable_deliveries(self, tmp_path, sample_event):
        """Validates that FAILED deliveries with next_attempt_at schedules survive database reopen."""
        db_path = tmp_path / "recovery_retryable.db"

        t0 = datetime(2026, 9, 18, 11, 0, 0, tzinfo=timezone.utc)
        store1 = SqliteEventStore(db_path)
        engine1 = EventEngine(store=store1)
        engine1.emit(sample_event, subscribers=["worker:flaky"])

        claimed = engine1.claim_next_delivery(now=t0)
        assert claimed is not None

        failed = engine1.fail_delivery(
            claimed.delivery_id,
            error_message="Network glitch",
            retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=15.0),
            now=t0,
        )
        assert failed.status == DeliveryStatus.FAILED
        expected_next = t0 + timedelta(seconds=15.0)
        assert failed.next_attempt_at == expected_next
        store1.close()

        # Session 2: reopen database
        store2 = SqliteEventStore(db_path)
        try:
            delivery = store2.get_delivery(claimed.delivery_id)
            assert delivery is not None
            assert delivery.status == DeliveryStatus.FAILED
            assert delivery.attempt_count == 1
            assert delivery.error_message == "Network glitch"
            assert delivery.next_attempt_at.isoformat() == expected_next.isoformat()
        finally:
            store2.close()

    def test_reopening_database_preserves_dead_letter_deliveries(self, tmp_path, sample_event):
        """Validates that quarantined DEAD_LETTER records survive database reopen intact."""
        db_path = tmp_path / "recovery_dlq.db"

        t0 = datetime(2026, 9, 18, 11, 0, 0, tzinfo=timezone.utc)
        store1 = SqliteEventStore(db_path)
        engine1 = EventEngine(store=store1)
        engine1.emit(sample_event, subscribers=["worker:poison"])

        claimed = engine1.claim_next_delivery(now=t0)
        engine1.fail_delivery(
            claimed.delivery_id,
            error_message="Fatal unrecoverable schema mismatch",
            retry_policy=RetryPolicy(max_attempts=1),
            now=t0,
        )
        store1.close()

        # Session 2: reopen database
        store2 = SqliteEventStore(db_path)
        try:
            dlq_item = store2.get_delivery(claimed.delivery_id)
            assert dlq_item is not None
            assert dlq_item.status == DeliveryStatus.DEAD_LETTER
            assert dlq_item.attempt_count == 1
            assert dlq_item.next_attempt_at is None
            assert "Fatal unrecoverable" in dlq_item.error_message
        finally:
            store2.close()

    def test_claim_metadata_in_flight_survives_reopening(self, tmp_path, sample_event):
        """Validates that active lease metadata (claimed_at, claimed_by, IN_FLIGHT) survives crash/reopen."""
        db_path = tmp_path / "recovery_inflight.db"

        t0 = datetime(2026, 9, 18, 11, 0, 0, tzinfo=timezone.utc)
        store1 = SqliteEventStore(db_path)
        engine1 = EventEngine(store=store1)
        engine1.emit(sample_event, subscribers=["worker:crashed"])

        claimed = engine1.claim_next_delivery(claimer_id="node-worker-99", now=t0)
        assert claimed is not None
        assert claimed.status == DeliveryStatus.IN_FLIGHT
        store1.close()

        # Session 2: simulate worker crash and inspect store
        store2 = SqliteEventStore(db_path)
        try:
            active_lease = store2.get_delivery(claimed.delivery_id)
            assert active_lease is not None
            assert active_lease.status == DeliveryStatus.IN_FLIGHT
            assert active_lease.claimed_by == "node-worker-99"
            assert active_lease.claimed_at.isoformat() == t0.isoformat()
            assert active_lease.attempt_count == 1
        finally:
            store2.close()

    def test_replay_does_not_duplicate_delivery(self, tmp_path, sample_event):
        """Validates that re-emitting an event across database reopens never creates duplicate deliveries."""
        db_path = tmp_path / "recovery_replay.db"

        # Session 1: emit event
        store1 = SqliteEventStore(db_path)
        engine1 = EventEngine(store=store1)
        engine1.emit(sample_event, subscribers=["worker:replay-target"])
        store1.close()

        # Session 2: replay emission of exact same event
        store2 = SqliteEventStore(db_path)
        engine2 = EventEngine(store=store2)
        try:
            persisted = engine2.emit(sample_event, subscribers=["worker:replay-target"])
            assert persisted.event_id == sample_event.event_id

            all_deliveries = store2.list_deliveries_by_event(sample_event.event_id)
            assert len(all_deliveries) == 1, "Replay must not duplicate outbox deliveries"
        finally:
            store2.close()

    def test_manual_stale_claim_recovery_deterministic(self, memory_store, sample_event):
        """Validates deterministic stale lease recovery for crashed workers."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["worker:stranded"])

        t0 = datetime(2026, 9, 18, 11, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(claimer_id="worker-died", now=t0)
        assert claimed.status == DeliveryStatus.IN_FLIGHT

        # 100 seconds elapsed (threshold is 300s) -> should NOT recover yet
        t_early = t0 + timedelta(seconds=100)
        recovered_early = engine.recover_stale_claims(stale_threshold_seconds=300.0, now=t_early)
        assert recovered_early == 0
        assert engine.store.get_delivery(claimed.delivery_id).status == DeliveryStatus.IN_FLIGHT

        # 301 seconds elapsed -> should recover cleanly to PENDING
        t_stale = t0 + timedelta(seconds=301)
        recovered = engine.recover_stale_claims(stale_threshold_seconds=300.0, now=t_stale)
        assert recovered == 1

        refreshed = engine.store.get_delivery(claimed.delivery_id)
        assert refreshed.status == DeliveryStatus.PENDING
        assert refreshed.claimed_at is None
        assert refreshed.claimed_by is None

        # Delivery can now be claimed by a healthy worker
        t_recov = t_stale + timedelta(seconds=5)
        new_claim = engine.claim_next_delivery(claimer_id="worker-healthy", now=t_recov)
        assert new_claim is not None
        assert new_claim.delivery_id == claimed.delivery_id
        assert new_claim.claimed_by == "worker-healthy"
        assert new_claim.attempt_count == 2

    def test_transaction_rollback_leaves_zero_orphans_or_partial_deliveries(self, tmp_path):
        """Validates that a failed atomic transaction rolls back completely, leaving no orphan events or partial deliveries."""
        db_path = tmp_path / "recovery_rollback.db"
        store = SqliteEventStore(db_path)

        event = DomainEvent.create(
            event_type="squad.rollback.test",
            work_item_id="US-R2-ROLLBACK",
            project_id="agent_squad",
            source="test",
            correlation_id="corr-rb",
            causation_id="cause-rb",
            payload={"action": "test_atomicity"},
        )

        valid_del = EventDelivery(
            delivery_id="del-good-1",
            event_id=event.event_id,
            subscriber="subscriber-good",
            status=DeliveryStatus.PENDING,
            attempt_count=0,
        )
        # Construct an intentionally invalid delivery that violates table constraints
        # by triggering an integrity error or simulating a mid-transaction crash
        bad_del = EventDelivery(
            delivery_id="del-good-1",  # Same delivery_id violates PRIMARY KEY on event_deliveries
            event_id=event.event_id,
            subscriber="subscriber-duplicate-key",
            status=DeliveryStatus.PENDING,
            attempt_count=0,
        )

        # Attempt atomic emission with conflicting delivery keys
        with pytest.raises(sqlite3.IntegrityError):
            store.save_event(event, deliveries=[valid_del, bad_del])

        # Atomicity guarantee: neither the event nor any deliveries should exist in SQLite
        assert store.get_event(event.event_id) is None
        assert store.get_delivery("del-good-1") is None
        assert len(store.list_events()) == 0
        store.close()
