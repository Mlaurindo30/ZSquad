"""Canonical Test Suite for R2 Event Delivery (Section 41).

Covers:
- PENDING -> PROCESSING (IN_FLIGHT) transition via claim
- PROCESSING -> COMPLETED (DELIVERED) transition via complete
- PROCESSING -> FAILED_RETRYABLE transition via fail
- FAILED_RETRYABLE becomes claimable at correct time (next_attempt_at <= now)
- Retry quota exhaustion -> DEAD_LETTER
- Terminal failure (max_attempts=1) -> DEAD_LETTER
- Illegal state transitions rejected (InvalidStateTransitionError)
- Same delivery cannot be claimed concurrently twice
- Completed delivery is never claimed
- Dead-lettered delivery is never claimed
- Attempt count accurately tracked
- Diagnostic error messages sanitized and truncated
"""

from datetime import datetime, timedelta, timezone
import pytest

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    RetryPolicy,
)
from scripts.runtime.events.engine import EventEngine, sanitize_error_message
from scripts.runtime.events.errors import (
    DeliveryNotFoundError,
    InvalidStateTransitionError,
)
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
        event_type="squad.task.dispatched",
        work_item_id="TASK-R2-200",
        project_id="agent_squad",
        source="engine",
        correlation_id="corr-del-100",
        causation_id="cause-del-100",
        payload={"action": "RUN_TESTS", "suite": "unit"},
    )


class TestR2EventDeliveryLifecycle:
    """Rigorous verification of the Outbox Delivery State Machine and leasing protocol."""

    def test_pending_to_in_flight_transition(self, memory_store, sample_event):
        """Validates PENDING -> IN_FLIGHT state transition upon claim."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["worker-agent-1"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(claimer_id="worker-node-a", now=t0)

        assert claimed is not None
        assert claimed.status == DeliveryStatus.IN_FLIGHT
        assert claimed.attempt_count == 1
        assert claimed.claimed_by == "worker-node-a"
        assert claimed.claimed_at == t0
        assert claimed.last_attempt_at == t0

    def test_in_flight_to_delivered_completion(self, memory_store, sample_event):
        """Validates IN_FLIGHT -> DELIVERED transition upon complete_delivery."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["worker-agent-1"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(claimer_id="worker-node-a", now=t0)
        assert claimed is not None

        t1 = t0 + timedelta(seconds=12)
        completed = engine.complete_delivery(claimed.delivery_id, now=t1)

        assert completed.status == DeliveryStatus.DELIVERED
        assert completed.claimed_at is None
        assert completed.claimed_by is None
        assert completed.error_message is None
        assert completed.last_attempt_at is not None

    def test_in_flight_to_failed_retryable_transition(self, memory_store, sample_event):
        """Validates IN_FLIGHT -> FAILED transition with scheduled exponential backoff."""
        retry_policy = RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=2.0,
            backoff_multiplier=2.0,
            max_backoff_seconds=30.0,
        )
        engine = EventEngine(store=memory_store, default_retry_policy=retry_policy)
        engine.emit(sample_event, subscribers=["worker-retryable"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(now=t0)
        assert claimed is not None

        failed = engine.fail_delivery(
            claimed.delivery_id,
            error_message="Database transient lock timeout",
            now=t0,
        )

        assert failed.status == DeliveryStatus.FAILED
        assert failed.attempt_count == 1
        assert failed.error_message == "Database transient lock timeout"
        # Backoff: 2.0 * (2.0 ** 0) = 2.0s
        assert failed.next_attempt_at == t0 + timedelta(seconds=2.0)
        assert failed.claimed_at is None
        assert failed.claimed_by is None

    def test_failed_retryable_becomes_claimable_at_correct_time(self, memory_store, sample_event):
        """Validates that a failed delivery is not claimable before next_attempt_at, but claimable after."""
        retry_policy = RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=5.0,
            backoff_multiplier=2.0,
        )
        engine = EventEngine(store=memory_store, default_retry_policy=retry_policy)
        engine.emit(sample_event, subscribers=["worker-backoff"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed1 = engine.claim_next_delivery(now=t0)
        engine.fail_delivery(claimed1.delivery_id, "Glitch 1", now=t0)

        # Before next_attempt_at (t0 + 2s < t0 + 5s): not claimable
        early_claim = engine.claim_next_delivery(now=t0 + timedelta(seconds=2.0))
        assert early_claim is None

        # At or after next_attempt_at (t0 + 5.5s): claimable
        t_due = t0 + timedelta(seconds=5.5)
        claimed2 = engine.claim_next_delivery(now=t_due)
        assert claimed2 is not None
        assert claimed2.delivery_id == claimed1.delivery_id
        assert claimed2.attempt_count == 2
        assert claimed2.status == DeliveryStatus.IN_FLIGHT

    def test_retry_exhaustion_advances_to_dead_letter(self, memory_store, sample_event):
        """Validates that exhausting configured max_attempts transitions delivery to DEAD_LETTER."""
        retry_policy = RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=1.0,
            backoff_multiplier=1.0,
        )
        engine = EventEngine(store=memory_store, default_retry_policy=retry_policy)
        engine.emit(sample_event, subscribers=["flaky-subscriber"])

        t = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)

        # Attempt 1 -> Fail
        c1 = engine.claim_next_delivery(now=t)
        engine.fail_delivery(c1.delivery_id, "Err 1", now=t)

        # Attempt 2 -> Fail
        t += timedelta(seconds=2)
        c2 = engine.claim_next_delivery(now=t)
        engine.fail_delivery(c2.delivery_id, "Err 2", now=t)

        # Attempt 3 -> Fail (Quota reached: attempt_count == 3 == max_attempts)
        t += timedelta(seconds=2)
        c3 = engine.claim_next_delivery(now=t)
        dlq = engine.fail_delivery(c3.delivery_id, "Fatal Error 3", now=t)

        assert dlq.status == DeliveryStatus.DEAD_LETTER
        assert dlq.attempt_count == 3
        assert dlq.next_attempt_at is None
        assert "Fatal Error 3" in dlq.error_message

    def test_terminal_failure_advances_to_dead_letter(self, memory_store, sample_event):
        """Validates that a terminal failure policy (max_attempts=1) routes immediately to DEAD_LETTER."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["one-shot-target"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(now=t0)

        terminal_policy = RetryPolicy(max_attempts=1)
        dead = engine.fail_delivery(
            claimed.delivery_id,
            error_message="Terminal unrecoverable exception",
            retry_policy=terminal_policy,
            now=t0,
        )

        assert dead.status == DeliveryStatus.DEAD_LETTER
        assert dead.attempt_count == 1
        assert dead.next_attempt_at is None

    def test_illegal_state_transitions_rejected(self, memory_store, sample_event):
        """Validates that illegal transitions raise InvalidStateTransitionError."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["strict-lifecycle"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(now=t0)
        engine.complete_delivery(claimed.delivery_id, now=t0)

        # Cannot complete an already DELIVERED delivery
        with pytest.raises(InvalidStateTransitionError, match="already DELIVERED"):
            engine.complete_delivery(claimed.delivery_id, now=t0)

        # Cannot fail an already DELIVERED delivery
        with pytest.raises(InvalidStateTransitionError, match="already been DELIVERED"):
            engine.fail_delivery(claimed.delivery_id, "Late failure", now=t0)

        # Cannot complete a DEAD_LETTER delivery
        event_dlq = DomainEvent.create(
            event_type="squad.task.dispatched",
            work_item_id="TASK-R2-DLQ",
            project_id="agent_squad",
            source="engine",
            correlation_id="corr-dlq-1",
            causation_id="cause-dlq-1",
        )
        engine.emit(event_dlq, subscribers=["dlq-target"])
        claimed_dlq = engine.claim_next_delivery(subscriber="dlq-target", now=t0)
        assert claimed_dlq is not None
        dead = engine.fail_delivery(
            claimed_dlq.delivery_id,
            "Fatal",
            retry_policy=RetryPolicy(max_attempts=1),
            now=t0,
        )
        with pytest.raises(InvalidStateTransitionError, match="Cannot complete"):
            engine.complete_delivery(dead.delivery_id, now=t0)

        # Missing delivery raises DeliveryNotFoundError
        with pytest.raises(DeliveryNotFoundError):
            engine.complete_delivery("missing-uuid-123")

    def test_same_delivery_not_claimed_concurrently_twice(self, memory_store, sample_event):
        """Validates atomic lease isolation: once claimed, item is IN_FLIGHT and invisible to other workers."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["single-queue"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        worker1_claim = engine.claim_next_delivery(claimer_id="worker-1", now=t0)
        assert worker1_claim is not None

        # Second worker attempts to claim concurrently
        worker2_claim = engine.claim_next_delivery(claimer_id="worker-2", now=t0)
        assert worker2_claim is None, "In-flight delivery must not be claimable by another worker"

    def test_completed_and_deadletter_deliveries_never_claimed(self, memory_store, sample_event):
        """Validates that terminal states (DELIVERED, DEAD_LETTER) are excluded from claiming."""
        engine = EventEngine(store=memory_store)
        engine.emit(sample_event, subscribers=["terminal-queue"])

        t0 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        claimed = engine.claim_next_delivery(now=t0)
        engine.complete_delivery(claimed.delivery_id, now=t0)

        # Completed delivery is never claimed
        assert engine.claim_next_delivery(now=t0 + timedelta(days=1)) is None

        # DEAD_LETTER delivery is never claimed
        event_dlq2 = DomainEvent.create(
            event_type="squad.task.dispatched",
            work_item_id="TASK-R2-DLQ-2",
            project_id="agent_squad",
            source="engine",
            correlation_id="corr-dlq-2",
            causation_id="cause-dlq-2",
        )
        engine.emit(event_dlq2, subscribers=["dlq-queue"])
        c_dlq = engine.claim_next_delivery(subscriber="dlq-queue", now=t0)
        assert c_dlq is not None
        engine.fail_delivery(
            c_dlq.delivery_id,
            "Terminal",
            retry_policy=RetryPolicy(max_attempts=1),
            now=t0,
        )
        assert engine.claim_next_delivery(subscriber="dlq-queue", now=t0 + timedelta(days=1)) is None

    def test_attempt_count_accuracy(self, memory_store, sample_event):
        """Validates that attempt_count increments deterministically across retries."""
        retry_policy = RetryPolicy(max_attempts=4, initial_backoff_seconds=1.0)
        engine = EventEngine(store=memory_store, default_retry_policy=retry_policy)
        engine.emit(sample_event, subscribers=["attempt-counter"])

        t = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
        for expected_attempt in range(1, 4):
            c = engine.claim_next_delivery(now=t)
            assert c is not None
            assert c.attempt_count == expected_attempt
            engine.fail_delivery(c.delivery_id, f"Error at attempt {expected_attempt}", now=t)
            t += timedelta(seconds=10)

    def test_last_error_sanitization_and_bounding(self):
        """Validates secret scrubbing (Bearer, API keys, passwords) and 500-char length truncation."""
        raw_error = (
            "Failed request with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123 "
            "and api_key='secret-production-key-999' password=TopSecretPassword123! "
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456"
        )
        sanitized = sanitize_error_message(raw_error)

        assert "Bearer [REDACTED_TOKEN]" in sanitized
        assert "secret-production-key-999" not in sanitized
        assert "TopSecretPassword123!" not in sanitized
        assert "sk-ant-api03" not in sanitized
        assert "[REDACTED]" in sanitized

        # Length limit truncation
        long_message = "X" * 1200
        truncated = sanitize_error_message(long_message, max_length=500)
        assert len(truncated) <= 500
        assert "... [TRUNCATED]" in truncated
