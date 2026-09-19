"""Canonical Test Suite for R2 Event Store (Section 39).

Covers:
- event insert succeeds
- event round-trip preserves canonical fields
- same idempotency key + same event: does not duplicate (returns was_created=False)
- same idempotency key + different event: fails closed (IdempotencyConflictError)
- correlation_id and causation_id preserved
- payload canonical JSON preserved
- database reopen preserves events
- foreign keys enabled and cascade deletes work
- schema initialization idempotent
- zero additional SQLite databases created
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest
import sqlite3

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
)
from scripts.runtime.events.errors import (
    EventAlreadyExistsError,
    IdempotencyConflictError,
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
    """Provides a valid canonical DomainEvent with comprehensive fields."""
    return DomainEvent.create(
        event_type="squad.work_item.created",
        work_item_id="US-R2-001",
        project_id="agent_squad",
        source="cli",
        correlation_id="corr-r2-12345",
        causation_id="cause-r2-67890",
        payload={
            "title": "Build Event Store",
            "story_points": 5,
            "risk": "medium",
            "tags": ["core", "events", "storage"],
            "metadata": {"author": "06-software-engineer", "active": True},
        },
    )


class TestR2EventStore:
    """Rigorous verification of the SQLite Event Store persistence layer."""

    def test_event_insert_succeeds(self, memory_store, sample_event):
        """Validates that saving a new canonical DomainEvent succeeds and returns was_created=True."""
        saved_event, created = memory_store.save_event(sample_event)
        assert created is True
        assert saved_event.event_id == sample_event.event_id
        assert saved_event.event_type == sample_event.event_type

        # Verify retrieval by event_id
        fetched = memory_store.get_event(sample_event.event_id)
        assert fetched is not None
        assert fetched.event_id == sample_event.event_id

    def test_event_round_trip_preserves_canonical_fields(self, memory_store, sample_event):
        """Validates that all canonical DomainEvent fields survive storage and deserialization."""
        memory_store.save_event(sample_event)
        fetched = memory_store.get_event(sample_event.event_id)

        assert fetched is not None
        assert fetched.event_id == sample_event.event_id
        assert fetched.event_type == sample_event.event_type
        assert fetched.work_item_id == sample_event.work_item_id
        assert fetched.project_id == sample_event.project_id
        assert fetched.source == sample_event.source
        assert fetched.correlation_id == sample_event.correlation_id
        assert fetched.causation_id == sample_event.causation_id
        assert fetched.idempotency_key == sample_event.idempotency_key
        assert fetched.payload == sample_event.payload

        # Timestamps match up to seconds / ISO fidelity
        if isinstance(sample_event.timestamp, datetime):
            assert fetched.timestamp.isoformat() == sample_event.timestamp.isoformat()

    def test_same_idempotency_key_same_event_does_not_duplicate(self, memory_store, sample_event):
        """Validates that re-saving an event with identical idempotency key and payload returns was_created=False."""
        first_event, created1 = memory_store.save_event(sample_event)
        assert created1 is True

        # Re-save identical event
        second_event, created2 = memory_store.save_event(sample_event)
        assert created2 is False
        assert second_event.event_id == first_event.event_id
        assert second_event.idempotency_key == first_event.idempotency_key

        # Ensure table contains exactly 1 record
        events = memory_store.list_events()
        assert len(events) == 1

    def test_same_idempotency_key_different_event_fails_closed(self, memory_store, sample_event):
        """Validates that presenting the same idempotency key with a differing payload raises IdempotencyConflictError."""
        memory_store.save_event(sample_event)

        # Forge conflicting event with same idempotency key but mutated payload
        conflicting_event = DomainEvent(
            event_id="evt-different-uuid-888",
            event_type=sample_event.event_type,
            work_item_id=sample_event.work_item_id,
            project_id=sample_event.project_id,
            source=sample_event.source,
            correlation_id=sample_event.correlation_id,
            causation_id=sample_event.causation_id,
            idempotency_key=sample_event.idempotency_key,  # Identical key
            timestamp=sample_event.timestamp,
            payload={"tampered": "different content", "risk": "critical"},
        )

        with pytest.raises(IdempotencyConflictError, match="Idempotency conflict"):
            memory_store.save_event(conflicting_event)

    def test_correlation_id_and_causation_id_preserved(self, memory_store):
        """Validates that causal tracing identifiers are immutably preserved."""
        event = DomainEvent.create(
            event_type="squad.gate.evaluated",
            work_item_id="US-R2-002",
            project_id="agent_squad",
            source="mcp",
            correlation_id="trace-root-alpha-999",
            causation_id="cmd-eval-gate-g3",
            payload={"gate_id": "G3", "verdict": "PASSED"},
        )
        memory_store.save_event(event)

        fetched = memory_store.get_event(event.event_id)
        assert fetched is not None
        assert fetched.correlation_id == "trace-root-alpha-999"
        assert fetched.causation_id == "cmd-eval-gate-g3"

    def test_payload_canonical_json_preserved(self, memory_store):
        """Validates that structured payload types (nested dicts, lists, booleans, ints) are preserved identically."""
        complex_payload = {
            "b_field": 42,
            "a_field": "alphabetical",
            "nested": {"z": True, "x": None, "numbers": [1, 2, 3]},
            "empty_list": [],
            "empty_dict": {},
        }
        event = DomainEvent.create(
            event_type="squad.payload.test",
            work_item_id="US-R2-003",
            project_id="agent_squad",
            source="test",
            correlation_id="corr-json",
            causation_id="cause-json",
            payload=complex_payload,
        )
        memory_store.save_event(event)

        fetched = memory_store.get_event(event.event_id)
        assert fetched is not None
        assert fetched.payload == complex_payload

    def test_database_reopen_preserves_events(self, tmp_path, sample_event):
        """Validates that closing and reopening a disk-backed SQLite database retains all events and deliveries."""
        db_path = tmp_path / "squad_reopen_test.db"

        # Session 1: write event and delivery
        store1 = SqliteEventStore(db_path)
        delivery = EventDelivery(
            delivery_id="del-persist-1",
            event_id=sample_event.event_id,
            subscriber="agent:06-software-engineer",
            status=DeliveryStatus.PENDING,
            attempt_count=0,
        )
        store1.save_event(sample_event, deliveries=[delivery])
        store1.close()

        # Session 2: open new store instance on same file
        store2 = SqliteEventStore(db_path)
        try:
            persisted_event = store2.get_event(sample_event.event_id)
            assert persisted_event is not None
            assert persisted_event.event_id == sample_event.event_id
            assert persisted_event.payload == sample_event.payload

            persisted_del = store2.get_delivery("del-persist-1")
            assert persisted_del is not None
            assert persisted_del.subscriber == "agent:06-software-engineer"
            assert persisted_del.status == DeliveryStatus.PENDING
        finally:
            store2.close()

    def test_foreign_keys_enabled_and_cascade_delete(self, memory_store, sample_event):
        """Validates PRAGMA foreign_keys = ON and ON DELETE CASCADE on event_deliveries."""
        # Check PRAGMA foreign_keys status directly
        with memory_store.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys;")
            fk_status = cursor.fetchone()[0]
            assert fk_status == 1, "Foreign keys must be enabled (PRAGMA foreign_keys = 1)"

        # Save event with attached delivery
        delivery = EventDelivery(
            delivery_id="del-fk-cascade",
            event_id=sample_event.event_id,
            subscriber="worker:test",
            status=DeliveryStatus.PENDING,
            attempt_count=0,
        )
        memory_store.save_event(sample_event, deliveries=[delivery])

        assert memory_store.get_delivery("del-fk-cascade") is not None

        # Attempting direct delivery insert with non-existent foreign key event_id must fail
        orphan_delivery = EventDelivery(
            delivery_id="del-orphan",
            event_id="non-existent-event-id-999",
            subscriber="worker:test",
            status=DeliveryStatus.PENDING,
            attempt_count=0,
        )
        with pytest.raises(sqlite3.IntegrityError):
            memory_store.insert_delivery(orphan_delivery)

        # Deleting parent event cascades and removes the child delivery
        with memory_store.connection() as conn:
            with conn:
                conn.execute("DELETE FROM events WHERE event_id = ?", (sample_event.event_id,))

        assert memory_store.get_event(sample_event.event_id) is None
        assert memory_store.get_delivery("del-fk-cascade") is None

    def test_schema_initialization_idempotent(self, tmp_path):
        """Validates that initializing schema multiple times does not corrupt or error on existing schema."""
        db_path = tmp_path / "idempotent_schema.db"
        store1 = SqliteEventStore(db_path)
        store1.close()

        # Reopen on same database file without error
        store2 = SqliteEventStore(db_path)
        # Call explicit bootstrap multiple times
        store2._bootstrap_schema()
        store2._bootstrap_schema()
        store2.close()

    def test_zero_additional_sqlite_databases_created(self, tmp_path, sample_event):
        """Validates that SqliteEventStore only operates on the single designated database file."""
        db_path = tmp_path / "single_store.db"
        store = SqliteEventStore(db_path)
        try:
            store.save_event(sample_event)
        finally:
            store.close()

        # Verify only single_store.db (and transient WAL/SHM files) exist; no events.db or triggers.db
        all_files = [p.name for p in tmp_path.iterdir() if p.is_file()]
        for filename in all_files:
            assert filename.startswith("single_store.db"), f"Unexpected auxiliary database created: {filename}"
        assert not (tmp_path / "events.db").exists()
        assert not (tmp_path / "triggers.db").exists()

    def test_event_id_collision_detection(self, memory_store, sample_event):
        """Validates that colliding event_ids with different idempotency keys raise EventAlreadyExistsError."""
        memory_store.save_event(sample_event)

        colliding_event = DomainEvent(
            event_id=sample_event.event_id,
            event_type=sample_event.event_type,
            work_item_id=sample_event.work_item_id,
            project_id=sample_event.project_id,
            source=sample_event.source,
            correlation_id=sample_event.correlation_id,
            causation_id=sample_event.causation_id,
            idempotency_key="distinct_idempotency_key_000000000000000000000000000000000",
            timestamp=sample_event.timestamp,
            payload=sample_event.payload,
        )

        with pytest.raises(EventAlreadyExistsError, match="already exists"):
            memory_store.save_event(colliding_event)
