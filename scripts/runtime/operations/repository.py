"""Operational Repository backed by SQLite WAL Mode (Milestone R13).

Strictly stdlib-only. Fully parameterized queries with atomic multi-process leasing.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional

from scripts.runtime.operations.errors import (
    JobAlreadyExistsError,
    JobNotFoundError,
    LeaseAcquisitionError,
)
from scripts.runtime.operations.models import (
    JobKind,
    JobStatus,
    OperationalLease,
    ScheduledJob,
)


def _utc_now_iso(dt: Optional[datetime] = None) -> str:
    current = dt or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class OperationalRepository:
    """Authoritative repository for scheduled jobs, operational leases, and watchdog checkpoints."""

    DDL_SCHEDULED_JOBS = """
    CREATE TABLE IF NOT EXISTS scheduled_jobs (
        job_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        due_at TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        status TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT,
        last_error TEXT,
        payload TEXT NOT NULL,
        correlation_id TEXT,
        causation_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_due_status ON scheduled_jobs(status, due_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_entity ON scheduled_jobs(entity_type, entity_id);
    """

    DDL_OPERATIONAL_LEASES = """
    CREATE TABLE IF NOT EXISTS operational_leases (
        resource_key TEXT PRIMARY KEY,
        lease_id TEXT NOT NULL,
        owner TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        acquired_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_leases_expiry ON operational_leases(expires_at);
    """

    DDL_WATCHDOG_CHECKPOINTS = """
    CREATE TABLE IF NOT EXISTS watchdog_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        component TEXT NOT NULL,
        cursor_value TEXT,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,  # Autocommit mode for explicit transactions
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.executescript(self.DDL_SCHEDULED_JOBS)
            conn.executescript(self.DDL_OPERATIONAL_LEASES)
            conn.executescript(self.DDL_WATCHDOG_CHECKPOINTS)

    def _row_to_job(self, row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            job_id=row["job_id"],
            kind=JobKind(row["kind"]),
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            due_at=_parse_iso(row["due_at"]),  # type: ignore
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            status=JobStatus(row["status"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=_parse_iso(row["lease_expires_at"]),
            last_error=row["last_error"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            created_at=_parse_iso(row["created_at"]),
            updated_at=_parse_iso(row["updated_at"]),
        )

    # --- Scheduled Jobs Operations ---

    def create_job(self, job: ScheduledJob, now: Optional[datetime] = None) -> ScheduledJob:
        """Inserts a new scheduled job."""
        now_str = _utc_now_iso(now)
        due_at_str = _utc_now_iso(job.due_at)
        lease_exp_str = _utc_now_iso(job.lease_expires_at) if job.lease_expires_at else None

        with self.connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO scheduled_jobs (
                        job_id, kind, entity_type, entity_id, due_at, attempt,
                        max_attempts, status, lease_owner, lease_expires_at,
                        last_error, payload, correlation_id, causation_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.kind.value,
                        job.entity_type,
                        job.entity_id,
                        due_at_str,
                        job.attempt,
                        job.max_attempts,
                        job.status.value,
                        job.lease_owner,
                        lease_exp_str,
                        job.last_error,
                        json.dumps(job.payload, sort_keys=True),
                        job.correlation_id,
                        job.causation_id,
                        now_str,
                        now_str,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise JobAlreadyExistsError(f"Job with ID '{job.job_id}' already exists") from e

        return self.get_job(job.job_id)  # type: ignore

    def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        """Retrieves a scheduled job by its unique ID."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def find_active_job_for_entity(
        self, kind: JobKind, entity_type: str, entity_id: str
    ) -> Optional[ScheduledJob]:
        """Finds any non-terminal job for a specific entity to prevent duplicates."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE kind = ? AND entity_type = ? AND entity_id = ?
                  AND status IN ('PENDING', 'RUNNING', 'FAILED_RETRYABLE')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (kind.value, entity_type, entity_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def claim_due_jobs(
        self,
        worker_id: str,
        limit: int = 10,
        lease_seconds: float = 60.0,
        now: Optional[datetime] = None,
    ) -> List[ScheduledJob]:
        """Atomically leases and claims up to `limit` due jobs for processing."""
        current_dt = now or datetime.now(timezone.utc)
        now_str = _utc_now_iso(current_dt)
        lease_expires = current_dt.timestamp() + lease_seconds
        lease_expires_dt = datetime.fromtimestamp(lease_expires, tz=timezone.utc)
        lease_expires_str = _utc_now_iso(lease_expires_dt)

        claimed_jobs: List[ScheduledJob] = []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Find candidate jobs due for processing
                cursor = conn.execute(
                    """
                    SELECT job_id FROM scheduled_jobs
                    WHERE (
                        (status IN ('PENDING', 'FAILED_RETRYABLE') AND due_at <= ?)
                        OR (status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                    )
                    ORDER BY due_at ASC
                    LIMIT ?
                    """,
                    (now_str, now_str, limit),
                )
                candidate_ids = [r["job_id"] for r in cursor.fetchall()]

                for jid in candidate_ids:
                    # Atomic update per job
                    upd_cursor = conn.execute(
                        """
                        UPDATE scheduled_jobs
                        SET lease_owner = ?,
                            lease_expires_at = ?,
                            status = 'RUNNING',
                            attempt = attempt + 1,
                            updated_at = ?
                        WHERE job_id = ?
                          AND (
                              (status IN ('PENDING', 'FAILED_RETRYABLE') AND due_at <= ?)
                              OR (status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                          )
                        """,
                        (worker_id, lease_expires_str, now_str, jid, now_str, now_str),
                    )
                    if upd_cursor.rowcount > 0:
                        # Successfully claimed
                        job_row = conn.execute("SELECT * FROM scheduled_jobs WHERE job_id = ?", (jid,)).fetchone()
                        if job_row:
                            claimed_jobs.append(self._row_to_job(job_row))

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        return claimed_jobs

    def complete_job(self, job_id: str, now: Optional[datetime] = None) -> ScheduledJob:
        """Marks a job as COMPLETED and releases its lease."""
        now_str = _utc_now_iso(now)
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'COMPLETED',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (now_str, job_id),
            )
        job = self.get_job(job_id)
        if not job:
            raise JobNotFoundError(f"Job '{job_id}' not found")
        return job

    def fail_job(
        self,
        job_id: str,
        error_message: str,
        retryable: bool,
        next_due_at: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> ScheduledJob:
        """Marks a job as FAILED_RETRYABLE or FAILED_TERMINAL."""
        now_str = _utc_now_iso(now)
        new_status = JobStatus.FAILED_RETRYABLE.value if retryable else JobStatus.FAILED_TERMINAL.value
        due_at_str = _utc_now_iso(next_due_at) if next_due_at else now_str

        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = ?,
                    due_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (new_status, due_at_str, error_message, now_str, job_id),
            )
        job = self.get_job(job_id)
        if not job:
            raise JobNotFoundError(f"Job '{job_id}' not found")
        return job

    def cancel_job(self, job_id: str, reason: Optional[str] = None, now: Optional[datetime] = None) -> ScheduledJob:
        """Cancels an active or pending scheduled job."""
        now_str = _utc_now_iso(now)
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'CANCELLED',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (reason, now_str, job_id),
            )
        job = self.get_job(job_id)
        if not job:
            raise JobNotFoundError(f"Job '{job_id}' not found")
        return job

    # --- Operational Leases (Multi-Process Mutex) ---

    def acquire_lease(
        self,
        resource_key: str,
        owner: str,
        lease_seconds: float = 60.0,
        now: Optional[datetime] = None,
    ) -> OperationalLease:
        """Atomically acquires or steals an expired durable lease for a resource."""
        current_dt = now or datetime.now(timezone.utc)
        now_str = _utc_now_iso(current_dt)
        expires_dt = current_dt.timestamp() + lease_seconds
        expires_at = datetime.fromtimestamp(expires_dt, tz=timezone.utc)
        expires_str = _utc_now_iso(expires_at)
        import uuid
        lease_id = str(uuid.uuid4())

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Check existing lease
                row = conn.execute(
                    "SELECT * FROM operational_leases WHERE resource_key = ?",
                    (resource_key,),
                ).fetchone()

                if row:
                    exp_dt = _parse_iso(row["expires_at"])
                    if exp_dt and exp_dt > current_dt and row["owner"] != owner:
                        conn.execute("ROLLBACK")
                        raise LeaseAcquisitionError(
                            f"Resource '{resource_key}' is actively leased by '{row['owner']}' until {row['expires_at']}"
                        )
                    # Update expired lease or re-acquire own lease
                    conn.execute(
                        """
                        UPDATE operational_leases
                        SET lease_id = ?, owner = ?, expires_at = ?, acquired_at = ?
                        WHERE resource_key = ?
                        """,
                        (lease_id, owner, expires_str, now_str, resource_key),
                    )
                else:
                    # Insert new lease
                    conn.execute(
                        """
                        INSERT INTO operational_leases (resource_key, lease_id, owner, expires_at, acquired_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (resource_key, lease_id, owner, expires_str, now_str),
                    )
                conn.execute("COMMIT")
            except LeaseAcquisitionError:
                raise
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        return OperationalLease(
            resource_key=resource_key,
            lease_id=lease_id,
            owner=owner,
            expires_at=expires_at,
            acquired_at=current_dt,
        )

    def release_lease(self, resource_key: str, owner: str) -> bool:
        """Releases an operational lease if currently owned by the caller."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM operational_leases WHERE resource_key = ? AND owner = ?",
                (resource_key, owner),
            )
            return cursor.rowcount > 0

    def get_lease(self, resource_key: str) -> Optional[OperationalLease]:
        """Retrieves current lease for a resource key."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM operational_leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            if not row:
                return None
            return OperationalLease(
                resource_key=row["resource_key"],
                lease_id=row["lease_id"],
                owner=row["owner"],
                expires_at=_parse_iso(row["expires_at"]),  # type: ignore
                acquired_at=_parse_iso(row["acquired_at"]),  # type: ignore
            )

    # --- Watchdog Checkpoints ---

    def get_checkpoint(self, checkpoint_id: str) -> Optional[str]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT cursor_value FROM watchdog_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            return row["cursor_value"] if row else None

    def set_checkpoint(self, checkpoint_id: str, component: str, cursor_value: str, now: Optional[datetime] = None) -> None:
        now_str = _utc_now_iso(now)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO watchdog_checkpoints (checkpoint_id, component, cursor_value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    cursor_value = excluded.cursor_value,
                    updated_at = excluded.updated_at
                """,
                (checkpoint_id, component, cursor_value, now_str),
            )
