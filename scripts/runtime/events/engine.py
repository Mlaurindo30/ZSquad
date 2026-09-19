"""Canonical EventEngine orchestrating durable storage, declarative triggers, and outbox delivery.

Strictly stdlib-only.
Enforces zero LLM calls, zero cron loops, zero direct agent dispatching, and zero Azure mutations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional, Set
import uuid

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
    RetryPolicy,
)
from scripts.runtime.events.errors import (
    DeliveryNotFoundError,
    InvalidStateTransitionError,
)
from scripts.runtime.events.store import SqliteEventStore, StoredEventDelivery
from scripts.runtime.events.triggers import TriggerRegistry


def _utc_now() -> datetime:
    """Returns current UTC timestamp with tzinfo."""
    return datetime.now(timezone.utc)


def sanitize_error_message(message: Optional[str], max_length: int = 500) -> Optional[str]:
    """Sanitizes and truncates error messages, redacting sensitive tokens, secrets and passwords."""
    if not message:
        return None
    scrubbed = str(message)
    # Redact bearer tokens
    scrubbed = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1[REDACTED_TOKEN]", scrubbed
    )
    # Redact key/secret/password assignments
    scrubbed = re.sub(
        r"(?i)(api[_-]?key|token|secret|password|pat)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}['\"]?",
        r"\1=[REDACTED]",
        scrubbed,
    )
    # Redact standard OpenAI / provider style keys
    scrubbed = re.sub(r"\bsk-[a-zA-Z0-9_\-]{15,}\b", "[REDACTED_API_KEY]", scrubbed)
    # Enforce maximum length
    if len(scrubbed) > max_length:
        scrubbed = scrubbed[: max_length - 15] + "... [TRUNCATED]"
    return scrubbed


class EventEngine:
    """Core domain engine coordinating event persistence, trigger matching, and reliable outbox delivery."""

    def __init__(
        self,
        store: SqliteEventStore,
        triggers: Optional[TriggerRegistry] = None,
        default_retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        """Initializes the EventEngine with persistence, trigger registry, and default retry policy.

        Args:
            store: Isolated SQLite event and delivery storage.
            triggers: Optional in-memory registry of declarative trigger policies.
            default_retry_policy: Fallback retry policy for failed delivery attempts.
        """
        self._store = store
        self._triggers = triggers or TriggerRegistry()
        self._default_retry_policy = default_retry_policy or RetryPolicy()

    @property
    def store(self) -> SqliteEventStore:
        """Returns the underlying SqliteEventStore instance."""
        return self._store

    @property
    def triggers(self) -> TriggerRegistry:
        """Returns the associated TriggerRegistry instance."""
        return self._triggers

    @property
    def default_retry_policy(self) -> RetryPolicy:
        """Returns the default RetryPolicy configured for the engine."""
        return self._default_retry_policy

    def emit(
        self,
        event: DomainEvent,
        subscribers: Optional[List[str]] = None,
        now: Optional[datetime] = None,
    ) -> DomainEvent:
        """Emits a domain event, matching declarative triggers and persisting outbox deliveries atomically.

        Args:
            event: The canonical DomainEvent to emit.
            subscribers: Optional explicit list of subscriber identifiers.
            now: Optional current timestamp (for deterministic testing and clock control).

        Returns:
            The persisted DomainEvent (either newly created or retrieved via idempotency deduplication).
        """
        current_time = now or _utc_now()
        subscribers_set: Set[str] = set(subscribers or [])

        # Match triggers declaratively without LLM or eval
        matched_triggers = self._triggers.match_triggers(event)
        for trigger in matched_triggers:
            if trigger.target_role:
                subscribers_set.add(f"agent:{trigger.target_role}")
            else:
                subscribers_set.add(f"trigger:{trigger.trigger_id}")

        # Prepare outbox deliveries
        deliveries: List[EventDelivery] = []
        for sub in sorted(subscribers_set):
            delivery_id = str(uuid.uuid4())
            deliveries.append(
                EventDelivery(
                    delivery_id=delivery_id,
                    event_id=event.event_id,
                    subscriber=sub,
                    status=DeliveryStatus.PENDING,
                    attempt_count=0,
                    last_attempt_at=None,
                    error_message=None,
                )
            )

        # Atomic commit of event and all outbox deliveries
        persisted_event, _ = self._store.save_event(
            event, deliveries=deliveries, now=current_time
        )
        return persisted_event

    def get_event(self, event_id: str) -> Optional[DomainEvent]:
        """Fetches an event by ID."""
        return self._store.get_event(event_id)

    def find_event_by_idempotency_key(self, idempotency_key: str) -> Optional[DomainEvent]:
        """Fetches an event by its idempotency key."""
        return self._store.find_event_by_idempotency_key(idempotency_key)

    def list_pending_deliveries(
        self, limit: int = 100, now: Optional[datetime] = None
    ) -> List[StoredEventDelivery]:
        """Lists pending or retry-ready deliveries awaiting execution."""
        return self._store.list_pending_deliveries(limit=limit, now=now)

    def claim_next_delivery(
        self,
        subscriber: Optional[str] = None,
        claimer_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[StoredEventDelivery]:
        """Leases the next eligible delivery for asynchronous processing.

        Transitions status to IN_FLIGHT, increments attempt_count, and sets lease claim metadata.
        """
        return self._store.claim_next_delivery(
            subscriber=subscriber, claimer_id=claimer_id, now=now
        )

    def complete_delivery(
        self, delivery_id: str, now: Optional[datetime] = None
    ) -> StoredEventDelivery:
        """Marks an in-flight delivery as successfully DELIVERED.

        Args:
            delivery_id: The ID of the delivery to complete.
            now: Optional current timestamp.

        Returns:
            The updated StoredEventDelivery.

        Raises:
            DeliveryNotFoundError: If the delivery record is missing.
            InvalidStateTransitionError: If the delivery is already DELIVERED or DEAD_LETTER.
        """
        current_time = now or _utc_now()
        existing = self._store.get_delivery(delivery_id)
        if not existing:
            raise DeliveryNotFoundError(delivery_id)

        if existing.status == DeliveryStatus.DELIVERED:
            raise InvalidStateTransitionError(
                delivery_id,
                existing.status.value,
                DeliveryStatus.DELIVERED.value,
                f"Delivery '{delivery_id}' is already DELIVERED",
            )

        if existing.status == DeliveryStatus.DEAD_LETTER:
            raise InvalidStateTransitionError(
                delivery_id,
                existing.status.value,
                DeliveryStatus.DELIVERED.value,
                f"Cannot complete delivery '{delivery_id}' directly from DEAD_LETTER",
            )

        return self._store.update_delivery_status(
            delivery_id=delivery_id,
            status=DeliveryStatus.DELIVERED,
            attempt_count=existing.attempt_count,
            last_attempt_at=existing.last_attempt_at or current_time,
            claimed_at=None,
            claimed_by=None,
            next_attempt_at=None,
            error_message=None,
            now=current_time,
        )

    def fail_delivery(
        self,
        delivery_id: str,
        error_message: str,
        retry_policy: Optional[RetryPolicy] = None,
        now: Optional[datetime] = None,
    ) -> StoredEventDelivery:
        """Handles a failed delivery attempt, applying exponential backoff or quarantine to DEAD_LETTER.

        Args:
            delivery_id: The ID of the failed delivery.
            error_message: Raw diagnostic error message (will be sanitized and truncated).
            retry_policy: Optional custom RetryPolicy (falls back to default).
            now: Optional current timestamp.

        Returns:
            The updated StoredEventDelivery (status FAILED or DEAD_LETTER).

        Raises:
            DeliveryNotFoundError: If the delivery does not exist.
            InvalidStateTransitionError: If attempting to fail an already completed delivery.
        """
        current_time = now or _utc_now()
        existing = self._store.get_delivery(delivery_id)
        if not existing:
            raise DeliveryNotFoundError(delivery_id)

        if existing.status == DeliveryStatus.DELIVERED:
            raise InvalidStateTransitionError(
                delivery_id,
                existing.status.value,
                DeliveryStatus.FAILED.value,
                f"Cannot fail delivery '{delivery_id}' because it has already been DELIVERED",
            )

        policy = retry_policy or self._default_retry_policy
        clean_error = sanitize_error_message(error_message)

        if existing.attempt_count < policy.max_attempts:
            # Exponential backoff formula
            exponent = max(0, existing.attempt_count - 1)
            backoff_delay = policy.initial_backoff_seconds * (
                policy.backoff_multiplier ** exponent
            )
            delay_seconds = min(backoff_delay, policy.max_backoff_seconds)
            next_attempt = current_time + timedelta(seconds=delay_seconds)
            new_status = DeliveryStatus.FAILED
        else:
            # Exhausted max attempts -> Quarantine to DEAD_LETTER
            next_attempt = None
            new_status = DeliveryStatus.DEAD_LETTER

        return self._store.update_delivery_status(
            delivery_id=delivery_id,
            status=new_status,
            attempt_count=existing.attempt_count,
            last_attempt_at=current_time,
            claimed_at=None,
            claimed_by=None,
            next_attempt_at=next_attempt,
            error_message=clean_error,
            now=current_time,
        )

    def recover_stale_claims(
        self, stale_threshold_seconds: float = 300.0, now: Optional[datetime] = None
    ) -> int:
        """Recovers deliveries that were claimed but never finished within the lease window."""
        return self._store.recover_stale_claims(
            stale_threshold_seconds=stale_threshold_seconds, now=now
        )

    def requeue_ready_deliveries(self, now: Optional[datetime] = None) -> int:
        """Transitions FAILED deliveries whose scheduled retry time has passed back to PENDING."""
        return self._store.requeue_ready_deliveries(now=now)
