"""Isolated SQLite Event and Delivery Outbox Store.

Strictly stdlib-only. Fully parameterized queries with atomic transactional integrity.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from scripts.domain.common import canonical_hash, canonical_json
from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
)
from scripts.runtime.events.errors import (
    DeliveryNotFoundError,
    EventAlreadyExistsError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
)


def _utc_now() -> datetime:
    """Returns current UTC timestamp."""
    return datetime.now(timezone.utc)


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    """Safely parses ISO formatted string to datetime."""
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str)


@dataclass(frozen=True)
class StoredEventDelivery(EventDelivery):
    """EventDelivery extended with claim leasing, retry scheduling, and auditing timestamps."""

    claimed_at: Optional[datetime] = None
    claimed_by: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SqliteEventStore:
    """High-performance, isolated transactional outbox and event persistence backed by SQLite."""

    DDL_EVENTS = """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        source TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT NOT NULL,
        idempotency_key TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL,
        payload TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """

    DDL_DELIVERIES = """
    CREATE TABLE IF NOT EXISTS event_deliveries (
        delivery_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        subscriber TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT,
        claimed_at TEXT,
        claimed_by TEXT,
        next_attempt_at TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    DDL_INDICES = [
        "CREATE INDEX IF NOT EXISTS idx_events_work_item ON events(work_item_id);",
        "CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);",
        "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
        "CREATE INDEX IF NOT EXISTS idx_events_idempotency ON events(idempotency_key);",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_status_next ON event_deliveries(status, next_attempt_at);",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_event_id ON event_deliveries(event_id);",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_subscriber ON event_deliveries(subscriber);",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_claimed_at ON event_deliveries(status, claimed_at);",
    ]

    def __init__(self, db_path: Optional[Union[str, Path, sqlite3.Connection]] = None) -> None:
        """Initializes the SqliteEventStore with strict WAL, foreign keys and schema bootstrap.

        Args:
            db_path: Path to SQLite database file, ':memory:', or an existing sqlite3.Connection.
                     If None, resolves to runtime '%SQUAD_RUNTIME%/banco/squad.db'.
        """
        self._external_conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._is_memory = False

        if isinstance(db_path, sqlite3.Connection):
            self._external_conn = db_path
            self._is_memory = True
        elif db_path == ":memory:":
            self._db_path = None
            self._is_memory = True
            # For persistent in-memory database across calls, keep a connection alive
            self._external_conn = sqlite3.connect(":memory:", check_same_thread=False)
        elif db_path is not None:
            self._db_path = Path(db_path)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            runtime_root = Path(os.environ.get("SQUAD_RUNTIME", Path.cwd()))
            banco_dir = runtime_root / "banco"
            banco_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = banco_dir / "squad.db"

        self._bootstrap_schema()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provides a managed SQLite connection with PRAGMA enforcement and atomic transactions."""
        if self._external_conn is not None:
            self._configure_pragmas(self._external_conn)
            yield self._external_conn
            return

        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        try:
            self._configure_pragmas(conn)
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Closes any persistent external or in-memory SQLite connection."""
        if self._external_conn is not None:
            try:
                self._external_conn.close()
            except Exception:
                pass
            self._external_conn = None

    def _configure_pragmas(self, conn: sqlite3.Connection) -> None:
        """Applies essential PRAGMAs: Foreign Keys, WAL mode (if disk-backed), and busy timeout."""
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")

    def _bootstrap_schema(self) -> None:
        """Applies idempotent DDL creating tables and indexes."""
        with self.connection() as conn:
            with conn:
                conn.execute(self.DDL_EVENTS)
                conn.execute(self.DDL_DELIVERIES)
                for index_sql in self.DDL_INDICES:
                    conn.execute(index_sql)

    def save_event(
        self,
        event: DomainEvent,
        deliveries: Optional[List[EventDelivery]] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[DomainEvent, bool]:
        """Atomically persists a domain event and its outbox deliveries with idempotency verification.

        Returns:
            Tuple of (persisted_event, was_created). If idempotency key matches with identical payload,
            returns (existing_event, False).

        Raises:
            IdempotencyConflictError: If idempotency key exists with a different payload hash.
            EventAlreadyExistsError: If event_id exists with a different idempotency key.
        """
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()
        current_hash = canonical_hash(event.payload)

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()

                # Check if idempotency key exists
                cursor.execute(
                    """
                    SELECT event_id, event_type, work_item_id, project_id, source,
                           correlation_id, causation_id, idempotency_key, timestamp,
                           payload, payload_hash
                    FROM events
                    WHERE idempotency_key = ?
                    """,
                    (event.idempotency_key,),
                )
                existing_row = cursor.fetchone()

                if existing_row:
                    existing_hash = existing_row[10]
                    if existing_hash != current_hash:
                        raise IdempotencyConflictError(
                            event.idempotency_key,
                            f"Idempotency conflict: key '{event.idempotency_key}' exists with payload hash '{existing_hash}', "
                            f"received payload hash '{current_hash}'",
                        )
                    return self._row_to_event(existing_row), False

                # Check if event_id collision exists
                cursor.execute("SELECT 1 FROM events WHERE event_id = ?", (event.event_id,))
                if cursor.fetchone():
                    raise EventAlreadyExistsError(event.event_id)

                # Insert event record
                ts_val = (
                    event.timestamp.isoformat()
                    if isinstance(event.timestamp, datetime)
                    else str(event.timestamp)
                )
                cursor.execute(
                    """
                    INSERT INTO events (
                        event_id, event_type, work_item_id, project_id, source,
                        correlation_id, causation_id, idempotency_key, timestamp,
                        payload, payload_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.work_item_id,
                        event.project_id,
                        event.source,
                        event.correlation_id,
                        event.causation_id,
                        event.idempotency_key,
                        ts_val,
                        canonical_json(event.payload),
                        current_hash,
                        now_iso,
                    ),
                )

                # Insert associated deliveries atomically
                if deliveries:
                    for delivery in deliveries:
                        self._insert_delivery_cursor(cursor, delivery, now_iso)

        return event, True

    def _insert_delivery_cursor(
        self, cursor: sqlite3.Cursor, delivery: EventDelivery, now_iso: str
    ) -> None:
        """Inserts delivery record using an active database cursor."""
        last_attempt = (
            delivery.last_attempt_at.isoformat()
            if isinstance(delivery.last_attempt_at, datetime)
            else None
        )
        status_val = (
            delivery.status.value
            if isinstance(delivery.status, DeliveryStatus)
            else str(delivery.status)
        )
        cursor.execute(
            """
            INSERT INTO event_deliveries (
                delivery_id, event_id, subscriber, status, attempt_count,
                last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                delivery.delivery_id,
                delivery.event_id,
                delivery.subscriber,
                status_val,
                delivery.attempt_count,
                last_attempt,
                delivery.error_message,
                now_iso,
                now_iso,
            ),
        )

    def get_event(self, event_id: str) -> Optional[DomainEvent]:
        """Retrieves a single domain event by its unique ID."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, event_type, work_item_id, project_id, source,
                       correlation_id, causation_id, idempotency_key, timestamp,
                       payload, payload_hash
                FROM events
                WHERE event_id = ?
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def find_event_by_idempotency_key(self, idempotency_key: str) -> Optional[DomainEvent]:
        """Retrieves a domain event by its canonical idempotency key."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, event_type, work_item_id, project_id, source,
                       correlation_id, causation_id, idempotency_key, timestamp,
                       payload, payload_hash
                FROM events
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def list_events_by_work_item(
        self, work_item_id: str, limit: int = 100
    ) -> List[DomainEvent]:
        """Lists events associated with a specific work item, ordered by timestamp ascending."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, event_type, work_item_id, project_id, source,
                       correlation_id, causation_id, idempotency_key, timestamp,
                       payload, payload_hash
                FROM events
                WHERE work_item_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (work_item_id, limit),
            )
            rows = cursor.fetchall()
            return [self._row_to_event(row) for row in rows]

    def list_events(self, limit: int = 100, offset: int = 0) -> List[DomainEvent]:
        """Lists events chronologically with pagination."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, event_type, work_item_id, project_id, source,
                       correlation_id, causation_id, idempotency_key, timestamp,
                       payload, payload_hash
                FROM events
                ORDER BY timestamp ASC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()
            return [self._row_to_event(row) for row in rows]

    def insert_delivery(
        self, delivery: EventDelivery, now: Optional[datetime] = None
    ) -> None:
        """Manually records an individual delivery target for an existing event."""
        current_time = now or _utc_now()
        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()
                self._insert_delivery_cursor(cursor, delivery, current_time.isoformat())

    def get_delivery(self, delivery_id: str) -> Optional[StoredEventDelivery]:
        """Retrieves an individual delivery record by ID."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT delivery_id, event_id, subscriber, status, attempt_count,
                       last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                       error_message, created_at, updated_at
                FROM event_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_delivery(row)

    def list_deliveries_by_event(self, event_id: str) -> List[StoredEventDelivery]:
        """Lists all delivery attempts and subscriptions for a specific event."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT delivery_id, event_id, subscriber, status, attempt_count,
                       last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                       error_message, created_at, updated_at
                FROM event_deliveries
                WHERE event_id = ?
                ORDER BY created_at ASC
                """,
                (event_id,),
            )
            return [self._row_to_delivery(r) for r in cursor.fetchall()]

    def list_pending_deliveries(
        self, limit: int = 100, now: Optional[datetime] = None
    ) -> List[StoredEventDelivery]:
        """Lists deliveries currently pending execution or eligible for retry."""
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()

        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT delivery_id, event_id, subscriber, status, attempt_count,
                       last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                       error_message, created_at, updated_at
                FROM event_deliveries
                WHERE status IN ('PENDING', 'FAILED')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (now_iso, limit),
            )
            return [self._row_to_delivery(r) for r in cursor.fetchall()]

    def claim_next_delivery(
        self,
        subscriber: Optional[str] = None,
        claimer_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[StoredEventDelivery]:
        """Atomically leases the next eligible pending delivery for processing.

        Transitions delivery status to IN_FLIGHT, increments attempt count,
        and sets claimed_at and claimed_by metadata.
        """
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()
        worker = claimer_id or "event-engine-worker"

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()

                # Find candidate delivery
                if subscriber:
                    cursor.execute(
                        """
                        SELECT delivery_id FROM event_deliveries
                        WHERE status IN ('PENDING', 'FAILED')
                          AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                          AND subscriber = ?
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        (now_iso, subscriber),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT delivery_id FROM event_deliveries
                        WHERE status IN ('PENDING', 'FAILED')
                          AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        (now_iso,),
                    )

                row = cursor.fetchone()
                if not row:
                    return None

                target_delivery_id = row[0]

                # Update target delivery to IN_FLIGHT
                cursor.execute(
                    """
                    UPDATE event_deliveries
                    SET status = 'IN_FLIGHT',
                        attempt_count = attempt_count + 1,
                        claimed_at = ?,
                        claimed_by = ?,
                        last_attempt_at = ?,
                        next_attempt_at = NULL,
                        updated_at = ?
                    WHERE delivery_id = ?
                      AND status IN ('PENDING', 'FAILED')
                    """,
                    (now_iso, worker, now_iso, now_iso, target_delivery_id),
                )

                if cursor.rowcount == 0:
                    return None

                cursor.execute(
                    """
                    SELECT delivery_id, event_id, subscriber, status, attempt_count,
                           last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                           error_message, created_at, updated_at
                    FROM event_deliveries
                    WHERE delivery_id = ?
                    """,
                    (target_delivery_id,),
                )
                updated_row = cursor.fetchone()
                return self._row_to_delivery(updated_row) if updated_row else None

    def update_delivery_status(
        self,
        delivery_id: str,
        status: DeliveryStatus,
        attempt_count: int,
        last_attempt_at: Optional[datetime] = None,
        claimed_at: Optional[datetime] = None,
        claimed_by: Optional[str] = None,
        next_attempt_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> StoredEventDelivery:
        """Updates the status and state metadata of an existing delivery."""
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()
        status_val = status.value if isinstance(status, DeliveryStatus) else str(status)

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE event_deliveries
                    SET status = ?,
                        attempt_count = ?,
                        last_attempt_at = ?,
                        claimed_at = ?,
                        claimed_by = ?,
                        next_attempt_at = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE delivery_id = ?
                    """,
                    (
                        status_val,
                        attempt_count,
                        last_attempt_at.isoformat() if last_attempt_at else None,
                        claimed_at.isoformat() if claimed_at else None,
                        claimed_by,
                        next_attempt_at.isoformat() if next_attempt_at else None,
                        error_message,
                        now_iso,
                        delivery_id,
                    ),
                )

                if cursor.rowcount == 0:
                    raise DeliveryNotFoundError(delivery_id)

                cursor.execute(
                    """
                    SELECT delivery_id, event_id, subscriber, status, attempt_count,
                           last_attempt_at, claimed_at, claimed_by, next_attempt_at,
                           error_message, created_at, updated_at
                    FROM event_deliveries
                    WHERE delivery_id = ?
                    """,
                    (delivery_id,),
                )
                row = cursor.fetchone()
                return self._row_to_delivery(row)

    def recover_stale_claims(
        self, stale_threshold_seconds: float = 300.0, now: Optional[datetime] = None
    ) -> int:
        """Recovers deliveries that have been stuck in IN_FLIGHT past the lease threshold.

        Resets their status back to PENDING and clears claimed metadata.
        """
        current_time = now or _utc_now()
        cutoff_timestamp = current_time.timestamp() - stale_threshold_seconds
        cutoff_dt = datetime.fromtimestamp(cutoff_timestamp, tz=timezone.utc)
        cutoff_iso = cutoff_dt.isoformat()
        now_iso = current_time.isoformat()

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE event_deliveries
                    SET status = 'PENDING',
                        claimed_at = NULL,
                        claimed_by = NULL,
                        updated_at = ?
                    WHERE status = 'IN_FLIGHT'
                      AND claimed_at IS NOT NULL
                      AND claimed_at <= ?
                    """,
                    (now_iso, cutoff_iso),
                )
                return cursor.rowcount

    def requeue_ready_deliveries(self, now: Optional[datetime] = None) -> int:
        """Transitions FAILED deliveries whose retry delay has elapsed back to PENDING."""
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE event_deliveries
                    SET status = 'PENDING',
                        updated_at = ?
                    WHERE status = 'FAILED'
                      AND next_attempt_at IS NOT NULL
                      AND next_attempt_at <= ?
                    """,
                    (now_iso, now_iso),
                )
                return cursor.rowcount

    @staticmethod
    def _row_to_event(row: Any) -> DomainEvent:
        """Converts raw SQLite row tuple to canonical DomainEvent."""
        raw_payload = json.loads(row[9])
        ts = datetime.fromisoformat(row[8])
        return DomainEvent(
            event_id=row[0],
            event_type=row[1],
            work_item_id=row[2],
            project_id=row[3],
            source=row[4],
            correlation_id=row[5],
            causation_id=row[6],
            idempotency_key=row[7],
            timestamp=ts,
            payload=raw_payload,
        )

    @staticmethod
    def _row_to_delivery(row: Any) -> StoredEventDelivery:
        """Converts raw SQLite row tuple to StoredEventDelivery."""
        return StoredEventDelivery(
            delivery_id=row[0],
            event_id=row[1],
            subscriber=row[2],
            status=DeliveryStatus(row[3]),
            attempt_count=int(row[4]),
            last_attempt_at=_parse_iso(row[5]),
            claimed_at=_parse_iso(row[6]),
            claimed_by=row[7],
            next_attempt_at=_parse_iso(row[8]),
            error_message=row[9],
            created_at=_parse_iso(row[10]),
            updated_at=_parse_iso(row[11]),
        )
