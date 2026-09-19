"""SQLite persistence repository for Project Delivery Bindings and Revision History.

Strictly stdlib-only. Enforces WAL mode, foreign keys, SHA-256 idempotency,
monotonic revision tracking, and absolute credential isolation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
import uuid

from scripts.domain.common import canonical_json
from scripts.runtime.delivery.errors import (
    BindingConflictError,
    DeliveryBindingError,
)

# Regex to detect inline basic auth / tokens in URLs: https://user:pass@host or https://token@host
_URL_CREDENTIALS_RE = re.compile(r"https?://([^/@:]+(:[^/@:]+)?@)")


def sanitize_credentials(value: Optional[str]) -> Optional[str]:
    """Strips any inline credentials or tokens from URLs and strings.
    
    SEC-R1-01: Plaintext tokens, PATs, or passwords must never be stored.
    """
    if not value or not isinstance(value, str):
        return value
    # Strip inline basic auth or pat from URL: https://pat@dev.azure.com -> https://dev.azure.com
    sanitized = _URL_CREDENTIALS_RE.sub(lambda m: m.group(0).split("://")[0] + "://", value)
    return sanitized


def compute_binding_fingerprint(binding_data: Dict[str, Any]) -> str:
    """Computes deterministic SHA-256 hash over canonical JSON representation.
    
    In accordance with Section 19.1 of R5 Specification.
    """
    normalized_payload = {
        "project_id": str(binding_data.get("project_id", "")).strip(),
        "project_root": str(binding_data.get("project_root", "")).strip(),
        "display_name": str(binding_data.get("display_name", "")).strip(),
        "delivery_backend_kind": str(binding_data.get("delivery_backend_kind", "")).strip(),
        "delivery_binding_ref": str(binding_data.get("delivery_binding_ref", "")).strip(),
        "organization_url": (str(binding_data.get("organization_url") or "")).strip().lower(),
        "team_project_name": (str(binding_data.get("team_project_name") or "")).strip().lower(),
        "repository_name": (str(binding_data.get("repository_name") or "")).strip().lower(),
        "assigned_team_name": (str(binding_data.get("assigned_team_name") or "")).strip().lower(),
        "area_path": (str(binding_data.get("area_path") or "")).strip().lower(),
        "iteration_path": (str(binding_data.get("iteration_path") or "")).strip().lower(),
    }
    encoded = canonical_json(normalized_payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProjectBindingRecord:
    """Represents an authoritative project binding record in the database."""

    project_id: str
    project_root: str
    display_name: str
    delivery_backend_kind: str
    delivery_binding_ref: str
    binding_status: str
    is_governed: bool = True
    organization_url: Optional[str] = None
    team_project_id: Optional[str] = None
    team_project_name: Optional[str] = None
    repository_id: Optional[str] = None
    repository_name: Optional[str] = None
    assigned_team_id: Optional[str] = None
    assigned_team_name: Optional[str] = None
    area_path: Optional[str] = None
    iteration_path: Optional[str] = None
    process_template: Optional[str] = None
    service_hook_secret_ref: Optional[str] = None
    fingerprint: Optional[str] = None
    revision: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def sanitized_dict(self) -> Dict[str, Any]:
        """Returns sanitized dictionary representation with zero plain-text credentials."""
        data = asdict(self)
        if data.get("organization_url"):
            data["organization_url"] = sanitize_credentials(data["organization_url"])
        if data.get("delivery_binding_ref"):
            data["delivery_binding_ref"] = sanitize_credentials(data["delivery_binding_ref"])
        return data


@dataclass(frozen=True)
class BindingHistoryRecord:
    """Represents an append-only audit ledger entry for project binding mutations."""

    history_id: str
    project_id: str
    revision: int
    action: str
    from_status: Optional[str]
    to_status: str
    changed_by: str
    fingerprint: str
    created_at: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkItemBindingRecord:
    """Represents an authoritative work item to Azure DevOps binding."""

    work_item_id: str
    project_id: str
    ado_id: Optional[int]
    remote_url: str
    remote_rev: int = 0
    sync_status: str = "PENDING_CREATE"
    sync_hash: str = ""
    last_synced_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncOutboxRecord:
    """Represents an outbox queue entry for external delivery synchronization."""

    outbox_id: str
    work_item_id: str
    project_id: str
    operation: str  # 'CREATE', 'UPDATE', 'DELETE', 'RECONCILE'
    payload_json: str
    status: str = "PENDING"  # 'PENDING', 'IN_FLIGHT', 'COMPLETED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL'
    expected_rev: Optional[int] = None
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: str = ""
    last_attempt_at: Optional[str] = None
    error_message: Optional[str] = None
    correlation_id: str = ""
    causation_id: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class InboundEventRecord:
    """Represents an ingested inbound webhook event for replay protection and deduplication."""

    event_id: str
    subscription_id: str
    event_type: str
    ado_id: int
    remote_rev: int
    received_at: str
    payload_hash: str


class SqliteBindingRepository:
    """Authoritative persistence repository for project delivery bindings backed by SQLite."""

    DDL_BINDINGS = """
    CREATE TABLE IF NOT EXISTS project_bindings (
        project_id TEXT PRIMARY KEY,
        project_root TEXT NOT NULL,
        display_name TEXT NOT NULL,
        delivery_backend_kind TEXT NOT NULL,
        delivery_binding_ref TEXT NOT NULL,
        binding_status TEXT NOT NULL,
        is_governed INTEGER NOT NULL DEFAULT 1,
        organization_url TEXT,
        team_project_id TEXT,
        team_project_name TEXT,
        repository_id TEXT,
        repository_name TEXT,
        assigned_team_id TEXT,
        assigned_team_name TEXT,
        area_path TEXT,
        iteration_path TEXT,
        process_template TEXT,
        service_hook_secret_ref TEXT,
        fingerprint TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT
    );
    """

    DDL_HISTORY = """
    CREATE TABLE IF NOT EXISTS project_binding_history (
        history_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        action TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        created_at TEXT NOT NULL,
        details_json TEXT,
        FOREIGN KEY (project_id) REFERENCES project_bindings(project_id) ON DELETE CASCADE
    );
    """

    DDL_INDICES = [
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_status ON project_bindings (binding_status);",
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_backend ON project_bindings (delivery_backend_kind);",
        "CREATE INDEX IF NOT EXISTS idx_binding_history_project ON project_binding_history (project_id, revision DESC);",
        "CREATE INDEX IF NOT EXISTS idx_binding_history_created ON project_binding_history (created_at DESC);",
    ]

    DDL_WORK_ITEM_BINDINGS = """
    CREATE TABLE IF NOT EXISTS delivery_work_item_bindings (
        work_item_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        ado_id INTEGER UNIQUE,
        remote_url TEXT NOT NULL,
        remote_rev INTEGER NOT NULL DEFAULT 0,
        sync_status TEXT NOT NULL,
        sync_hash TEXT NOT NULL,
        last_synced_at TEXT NOT NULL,
        metadata_json TEXT,
        FOREIGN KEY (project_id) REFERENCES project_bindings(project_id) ON DELETE CASCADE
    );
    """

    DDL_SYNC_OUTBOX = """
    CREATE TABLE IF NOT EXISTS delivery_sync_outbox (
        outbox_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        expected_rev INTEGER,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        next_attempt_at TEXT NOT NULL,
        last_attempt_at TEXT,
        error_message TEXT,
        correlation_id TEXT NOT NULL,
        causation_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (work_item_id) REFERENCES delivery_work_item_bindings(work_item_id) ON DELETE CASCADE
    );
    """

    DDL_INBOUND_EVENTS = """
    CREATE TABLE IF NOT EXISTS delivery_inbound_events (
        event_id TEXT PRIMARY KEY,
        subscription_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ado_id INTEGER NOT NULL,
        remote_rev INTEGER NOT NULL,
        received_at TEXT NOT NULL,
        payload_hash TEXT NOT NULL
    );
    """

    DDL_SYNC_INDICES = [
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_polling ON delivery_sync_outbox (status, next_attempt_at) WHERE status IN ('PENDING', 'FAILED_RETRYABLE');",
        "CREATE INDEX IF NOT EXISTS idx_work_item_bindings_ado ON delivery_work_item_bindings (ado_id);",
        "CREATE INDEX IF NOT EXISTS idx_work_item_bindings_project ON delivery_work_item_bindings (project_id);",
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_work_item ON delivery_sync_outbox (work_item_id);",
        "CREATE INDEX IF NOT EXISTS idx_inbound_events_ado_rev ON delivery_inbound_events (ado_id, remote_rev);",
    ]

    def __init__(self, db_path: Optional[Union[str, Path, sqlite3.Connection]] = None) -> None:
        """Initializes the SqliteBindingRepository with schema bootstrap.
        
        Args:
            db_path: Path to database file, ':memory:', or existing sqlite3.Connection.
                     If None, resolves to runtime '%SQUAD_RUNTIME%/banco/squad.db'.
        """
        self._external_conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._is_memory = False

        if isinstance(db_path, sqlite3.Connection):
            self._external_conn = db_path
            self._is_memory = True
        elif db_path == ":memory:":
            self._is_memory = True
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

    def _configure_pragmas(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding managed connection with PRAGMA enforcement."""
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

    def _bootstrap_schema(self) -> None:
        with self.connection() as conn:
            with conn:
                conn.execute(self.DDL_BINDINGS)
                conn.execute(self.DDL_HISTORY)
                conn.execute(self.DDL_WORK_ITEM_BINDINGS)
                conn.execute(self.DDL_SYNC_OUTBOX)
                conn.execute(self.DDL_INBOUND_EVENTS)
                for index_sql in self.DDL_INDICES:
                    conn.execute(index_sql)
                for index_sql in self.DDL_SYNC_INDICES:
                    conn.execute(index_sql)

    def close(self) -> None:
        """Closes any persistent external or in-memory SQLite connection."""
        if self._external_conn is not None:
            try:
                self._external_conn.close()
            except Exception:
                pass
            self._external_conn = None

    def get_binding(self, project_id: str) -> Optional[ProjectBindingRecord]:
        """Fetches the current binding record for the given project_id."""
        query = "SELECT * FROM project_bindings WHERE project_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (project_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(cursor, row)

    def list_bindings(self) -> List[ProjectBindingRecord]:
        """Lists all registered project bindings."""
        query = "SELECT * FROM project_bindings ORDER BY project_id ASC;"
        with self.connection() as conn:
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            return [self._row_to_record(cursor, row) for row in rows]

    def upsert_binding(
        self,
        record: ProjectBindingRecord,
        changed_by: str = "system",
        action: str = "UPSERT",
        details: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ProjectBindingRecord, bool]:
        """Atomically inserts or updates a project binding record with revision tracking.

        Returns:
            Tuple[ProjectBindingRecord, bool]: (persisted_record, changed)
            where changed is False if fingerprint matched existing record (idempotent no-op).
        """
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        
        # Calculate fingerprint
        dict_rep = record.sanitized_dict()
        fingerprint = record.fingerprint or compute_binding_fingerprint(dict_rep)

        with self.connection() as conn:
            with conn:
                # 1. Inspect current state under transaction
                cursor = conn.execute("SELECT * FROM project_bindings WHERE project_id = ?;", (record.project_id,))
                existing_row = cursor.fetchone()

                if existing_row:
                    existing = self._row_to_record(cursor, existing_row)
                    # Idempotency check: if fingerprint matches and status is identical, no mutation needed
                    if existing.fingerprint == fingerprint and existing.binding_status == record.binding_status:
                        return existing, False

                    new_revision = existing.revision + 1
                    created_at = existing.created_at or now_iso
                    updated_at = now_iso
                    from_status = existing.binding_status

                    update_sql = """
                    UPDATE project_bindings SET
                        project_root = ?,
                        display_name = ?,
                        delivery_backend_kind = ?,
                        delivery_binding_ref = ?,
                        binding_status = ?,
                        is_governed = ?,
                        organization_url = ?,
                        team_project_id = ?,
                        team_project_name = ?,
                        repository_id = ?,
                        repository_name = ?,
                        assigned_team_id = ?,
                        assigned_team_name = ?,
                        area_path = ?,
                        iteration_path = ?,
                        process_template = ?,
                        service_hook_secret_ref = ?,
                        fingerprint = ?,
                        revision = ?,
                        updated_at = ?,
                        metadata_json = ?
                    WHERE project_id = ?;
                    """
                    conn.execute(
                        update_sql,
                        (
                            record.project_root,
                            record.display_name,
                            record.delivery_backend_kind,
                            record.delivery_binding_ref,
                            record.binding_status,
                            1 if record.is_governed else 0,
                            record.organization_url,
                            record.team_project_id,
                            record.team_project_name,
                            record.repository_id,
                            record.repository_name,
                            record.assigned_team_id,
                            record.assigned_team_name,
                            record.area_path,
                            record.iteration_path,
                            record.process_template,
                            record.service_hook_secret_ref,
                            fingerprint,
                            new_revision,
                            updated_at,
                            json.dumps(record.metadata, ensure_ascii=False),
                            record.project_id,
                        ),
                    )
                else:
                    new_revision = 1
                    created_at = now_iso
                    updated_at = now_iso
                    from_status = None

                    insert_sql = """
                    INSERT INTO project_bindings (
                        project_id, project_root, display_name, delivery_backend_kind,
                        delivery_binding_ref, binding_status, is_governed, organization_url,
                        team_project_id, team_project_name, repository_id, repository_name,
                        assigned_team_id, assigned_team_name, area_path, iteration_path,
                        process_template, service_hook_secret_ref, fingerprint, revision,
                        created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """
                    conn.execute(
                        insert_sql,
                        (
                            record.project_id,
                            record.project_root,
                            record.display_name,
                            record.delivery_backend_kind,
                            record.delivery_binding_ref,
                            record.binding_status,
                            1 if record.is_governed else 0,
                            record.organization_url,
                            record.team_project_id,
                            record.team_project_name,
                            record.repository_id,
                            record.repository_name,
                            record.assigned_team_id,
                            record.assigned_team_name,
                            record.area_path,
                            record.iteration_path,
                            record.process_template,
                            record.service_hook_secret_ref,
                            fingerprint,
                            new_revision,
                            created_at,
                            updated_at,
                            json.dumps(record.metadata, ensure_ascii=False),
                        ),
                    )

                # Append to immutable audit history
                history_id = f"hist-{uuid.uuid4()}"
                history_sql = """
                INSERT INTO project_binding_history (
                    history_id, project_id, revision, action, from_status, to_status,
                    changed_by, fingerprint, created_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                conn.execute(
                    history_sql,
                    (
                        history_id,
                        record.project_id,
                        new_revision,
                        action,
                        from_status,
                        record.binding_status,
                        changed_by,
                        fingerprint,
                        now_iso,
                        json.dumps(details or {}, ensure_ascii=False),
                    ),
                )

                persisted_record = ProjectBindingRecord(
                    project_id=record.project_id,
                    project_root=record.project_root,
                    display_name=record.display_name,
                    delivery_backend_kind=record.delivery_backend_kind,
                    delivery_binding_ref=record.delivery_binding_ref,
                    binding_status=record.binding_status,
                    is_governed=record.is_governed,
                    organization_url=record.organization_url,
                    team_project_id=record.team_project_id,
                    team_project_name=record.team_project_name,
                    repository_id=record.repository_id,
                    repository_name=record.repository_name,
                    assigned_team_id=record.assigned_team_id,
                    assigned_team_name=record.assigned_team_name,
                    area_path=record.area_path,
                    iteration_path=record.iteration_path,
                    process_template=record.process_template,
                    service_hook_secret_ref=record.service_hook_secret_ref,
                    fingerprint=fingerprint,
                    revision=new_revision,
                    created_at=created_at,
                    updated_at=updated_at,
                    metadata=record.metadata,
                )
                return persisted_record, True

    def get_history(self, project_id: str) -> List[BindingHistoryRecord]:
        """Retrieves audit trail of mutations for a project binding, ordered by revision descending."""
        query = """
        SELECT * FROM project_binding_history 
        WHERE project_id = ? 
        ORDER BY revision DESC;
        """
        with self.connection() as conn:
            cursor = conn.execute(query, (project_id,))
            cols = [c[0] for c in cursor.description]
            records = []
            for row in cursor.fetchall():
                row_dict = dict(zip(cols, row))
                details = {}
                if row_dict.get("details_json"):
                    try:
                        details = json.loads(row_dict["details_json"])
                    except Exception:
                        pass
                records.append(
                    BindingHistoryRecord(
                        history_id=row_dict["history_id"],
                        project_id=row_dict["project_id"],
                        revision=row_dict["revision"],
                        action=row_dict["action"],
                        from_status=row_dict.get("from_status"),
                        to_status=row_dict["to_status"],
                        changed_by=row_dict["changed_by"],
                        fingerprint=row_dict["fingerprint"],
                        created_at=row_dict["created_at"],
                        details=details,
                    )
                )
            return records

    def delete_binding(self, project_id: str, changed_by: str = "system") -> bool:
        """Deletes a binding record, cascading history removal via foreign key."""
        with self.connection() as conn:
            with conn:
                cursor = conn.execute("DELETE FROM project_bindings WHERE project_id = ?;", (project_id,))
                return cursor.rowcount > 0

    def _row_to_record(self, cursor: sqlite3.Cursor, row: sqlite3.Row | tuple) -> ProjectBindingRecord:
        cols = [c[0] for c in cursor.description]
        d = dict(zip(cols, row))
        meta = {}
        if d.get("metadata_json"):
            try:
                meta = json.loads(d["metadata_json"])
            except Exception:
                meta = {}
        return ProjectBindingRecord(
            project_id=d["project_id"],
            project_root=d["project_root"],
            display_name=d["display_name"],
            delivery_backend_kind=d["delivery_backend_kind"],
            delivery_binding_ref=d["delivery_binding_ref"],
            binding_status=d["binding_status"],
            is_governed=bool(d["is_governed"]),
            organization_url=d.get("organization_url"),
            team_project_id=d.get("team_project_id"),
            team_project_name=d.get("team_project_name"),
            repository_id=d.get("repository_id"),
            repository_name=d.get("repository_name"),
            assigned_team_id=d.get("assigned_team_id"),
            assigned_team_name=d.get("assigned_team_name"),
            area_path=d.get("area_path"),
            iteration_path=d.get("iteration_path"),
            process_template=d.get("process_template"),
            service_hook_secret_ref=d.get("service_hook_secret_ref"),
            fingerprint=d["fingerprint"],
            revision=d["revision"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            metadata=meta,
        )

    # --- Delivery Work Item Bindings ---

    def get_work_item_binding(self, work_item_id: str) -> Optional[WorkItemBindingRecord]:
        """Fetches the authoritative binding record for a canonical work item ID."""
        query = "SELECT * FROM delivery_work_item_bindings WHERE work_item_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (work_item_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_work_item_binding(cursor, row)

    def get_work_item_binding_by_ado_id(self, ado_id: int) -> Optional[WorkItemBindingRecord]:
        """Fetches the binding record by remote Azure DevOps work item ID."""
        query = "SELECT * FROM delivery_work_item_bindings WHERE ado_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (ado_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_work_item_binding(cursor, row)

    def list_work_item_bindings(self, project_id: Optional[str] = None) -> List[WorkItemBindingRecord]:
        """Lists work item bindings, optionally filtered by project_id."""
        if project_id:
            query = "SELECT * FROM delivery_work_item_bindings WHERE project_id = ? ORDER BY work_item_id ASC;"
            params: Tuple[Any, ...] = (project_id,)
        else:
            query = "SELECT * FROM delivery_work_item_bindings ORDER BY work_item_id ASC;"
            params = ()
        with self.connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_work_item_binding(cursor, row) for row in rows]

    def upsert_work_item_binding(self, record: WorkItemBindingRecord) -> WorkItemBindingRecord:
        """Atomically creates or updates a delivery work item binding."""
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        last_synced = record.last_synced_at or now_iso
        query = """
        INSERT INTO delivery_work_item_bindings (
            work_item_id, project_id, ado_id, remote_url, remote_rev, sync_status,
            sync_hash, last_synced_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(work_item_id) DO UPDATE SET
            project_id = excluded.project_id,
            ado_id = COALESCE(excluded.ado_id, delivery_work_item_bindings.ado_id),
            remote_url = excluded.remote_url,
            remote_rev = excluded.remote_rev,
            sync_status = excluded.sync_status,
            sync_hash = excluded.sync_hash,
            last_synced_at = excluded.last_synced_at,
            metadata_json = excluded.metadata_json;
        """
        with self.connection() as conn:
            with conn:
                conn.execute(
                    query,
                    (
                        record.work_item_id,
                        record.project_id,
                        record.ado_id,
                        record.remote_url,
                        record.remote_rev,
                        record.sync_status,
                        record.sync_hash,
                        last_synced,
                        json.dumps(record.metadata, ensure_ascii=False),
                    ),
                )
        return WorkItemBindingRecord(
            work_item_id=record.work_item_id,
            project_id=record.project_id,
            ado_id=record.ado_id,
            remote_url=record.remote_url,
            remote_rev=record.remote_rev,
            sync_status=record.sync_status,
            sync_hash=record.sync_hash,
            last_synced_at=last_synced,
            metadata=record.metadata,
        )

    def delete_work_item_binding(self, work_item_id: str) -> bool:
        """Deletes a delivery work item binding."""
        with self.connection() as conn:
            with conn:
                cursor = conn.execute("DELETE FROM delivery_work_item_bindings WHERE work_item_id = ?;", (work_item_id,))
                return cursor.rowcount > 0

    def _row_to_work_item_binding(self, cursor: sqlite3.Cursor, row: sqlite3.Row | tuple) -> WorkItemBindingRecord:
        cols = [c[0] for c in cursor.description]
        d = dict(zip(cols, row))
        meta = {}
        if d.get("metadata_json"):
            try:
                meta = json.loads(d["metadata_json"])
            except Exception:
                meta = {}
        return WorkItemBindingRecord(
            work_item_id=d["work_item_id"],
            project_id=d["project_id"],
            ado_id=d.get("ado_id"),
            remote_url=d.get("remote_url", ""),
            remote_rev=d.get("remote_rev", 0),
            sync_status=d.get("sync_status", "PENDING_CREATE"),
            sync_hash=d.get("sync_hash", ""),
            last_synced_at=d.get("last_synced_at", ""),
            metadata=meta,
        )

    # --- Delivery Sync Outbox ---

    def enqueue_sync_outbox(self, record: SyncOutboxRecord) -> SyncOutboxRecord:
        """Enqueues an operation into the transactional delivery sync outbox."""
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        created_at = record.created_at or now_iso
        updated_at = record.updated_at or now_iso
        next_attempt = record.next_attempt_at or now_iso

        query = """
        INSERT INTO delivery_sync_outbox (
            outbox_id, work_item_id, project_id, operation, payload_json, status,
            expected_rev, attempt_count, max_attempts, next_attempt_at, last_attempt_at,
            error_message, correlation_id, causation_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.connection() as conn:
            with conn:
                conn.execute(
                    query,
                    (
                        record.outbox_id,
                        record.work_item_id,
                        record.project_id,
                        record.operation,
                        record.payload_json,
                        record.status,
                        record.expected_rev,
                        record.attempt_count,
                        record.max_attempts,
                        next_attempt,
                        record.last_attempt_at,
                        record.error_message,
                        record.correlation_id,
                        record.causation_id,
                        created_at,
                        updated_at,
                    ),
                )
        return SyncOutboxRecord(
            outbox_id=record.outbox_id,
            work_item_id=record.work_item_id,
            project_id=record.project_id,
            operation=record.operation,
            payload_json=record.payload_json,
            status=record.status,
            expected_rev=record.expected_rev,
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
            next_attempt_at=next_attempt,
            last_attempt_at=record.last_attempt_at,
            error_message=record.error_message,
            correlation_id=record.correlation_id,
            causation_id=record.causation_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    def get_sync_outbox(self, outbox_id: str) -> Optional[SyncOutboxRecord]:
        """Retrieves a sync outbox entry by outbox ID or work item ID fallback."""
        query = "SELECT * FROM delivery_sync_outbox WHERE outbox_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (outbox_id,))
            row = cursor.fetchone()
            if not row:
                # Fallback to lookup by work_item_id
                return self.get_sync_outbox_by_work_item(outbox_id)
            return self._row_to_sync_outbox(cursor, row)

    def get_sync_outbox_by_work_item(self, work_item_id: str) -> Optional[SyncOutboxRecord]:
        """Retrieves the most recent sync outbox entry for a work item ID."""
        query = "SELECT * FROM delivery_sync_outbox WHERE work_item_id = ? ORDER BY created_at DESC LIMIT 1;"
        with self.connection() as conn:
            cursor = conn.execute(query, (work_item_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_sync_outbox(cursor, row)

    def update_sync_outbox_status(
        self,
        outbox_id: str,
        status: str,
        error_message: Optional[str] = None,
        attempt_count: Optional[int] = None,
        next_attempt_at: Optional[str] = None,
        last_attempt_at: Optional[str] = None,
    ) -> bool:
        """Updates the status and retry metadata of an outbox item."""
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        last_attempt = last_attempt_at or now_iso
        clauses = ["status = ?", "updated_at = ?"]
        params: List[Any] = [status, now_iso]

        if error_message is not None:
            clauses.append("error_message = ?")
            params.append(error_message)
        if attempt_count is not None:
            clauses.append("attempt_count = ?")
            params.append(attempt_count)
        if next_attempt_at is not None:
            clauses.append("next_attempt_at = ?")
            params.append(next_attempt_at)
        clauses.append("last_attempt_at = ?")
        params.append(last_attempt)

        params.append(outbox_id)
        query = f"UPDATE delivery_sync_outbox SET {', '.join(clauses)} WHERE outbox_id = ?;"
        with self.connection() as conn:
            with conn:
                cursor = conn.execute(query, params)
                return cursor.rowcount > 0

    def get_pending_sync_outbox(
        self,
        limit: int = 50,
        project_id: Optional[str] = None,
        as_of_iso: Optional[str] = None,
    ) -> List[SyncOutboxRecord]:
        """Queries pending or retryable outbox items scheduled for execution."""
        cutoff = as_of_iso or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if project_id:
            query = """
            SELECT * FROM delivery_sync_outbox
            WHERE project_id = ? AND status IN ('PENDING', 'FAILED_RETRYABLE') AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, created_at ASC
            LIMIT ?;
            """
            params: Tuple[Any, ...] = (project_id, cutoff, limit)
        else:
            query = """
            SELECT * FROM delivery_sync_outbox
            WHERE status IN ('PENDING', 'FAILED_RETRYABLE') AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, created_at ASC
            LIMIT ?;
            """
            params = (cutoff, limit)

        with self.connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_sync_outbox(cursor, row) for row in rows]

    def _row_to_sync_outbox(self, cursor: sqlite3.Cursor, row: sqlite3.Row | tuple) -> SyncOutboxRecord:
        cols = [c[0] for c in cursor.description]
        d = dict(zip(cols, row))
        return SyncOutboxRecord(
            outbox_id=d["outbox_id"],
            work_item_id=d["work_item_id"],
            project_id=d["project_id"],
            operation=d["operation"],
            payload_json=d["payload_json"],
            status=d["status"],
            expected_rev=d.get("expected_rev"),
            attempt_count=d.get("attempt_count", 0),
            max_attempts=d.get("max_attempts", 3),
            next_attempt_at=d.get("next_attempt_at", ""),
            last_attempt_at=d.get("last_attempt_at"),
            error_message=d.get("error_message"),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    # --- Delivery Inbound Events ---

    def record_inbound_event(self, record: InboundEventRecord) -> bool:
        """Records an incoming webhook event. Returns True if newly inserted, False if duplicate."""
        query = """
        INSERT INTO delivery_inbound_events (
            event_id, subscription_id, event_type, ado_id, remote_rev, received_at, payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO NOTHING;
        """
        with self.connection() as conn:
            with conn:
                cursor = conn.execute(
                    query,
                    (
                        record.event_id,
                        record.subscription_id,
                        record.event_type,
                        record.ado_id,
                        record.remote_rev,
                        record.received_at,
                        record.payload_hash,
                    ),
                )
                return cursor.rowcount > 0

    def get_inbound_event(self, event_id: str) -> Optional[InboundEventRecord]:
        """Retrieves an inbound event by its unique ID."""
        query = "SELECT * FROM delivery_inbound_events WHERE event_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (event_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_inbound_event(cursor, row)

    def get_latest_inbound_rev(self, ado_id: int) -> Optional[int]:
        """Returns the highest revision recorded for a given remote ADO ID."""
        query = "SELECT MAX(remote_rev) FROM delivery_inbound_events WHERE ado_id = ?;"
        with self.connection() as conn:
            cursor = conn.execute(query, (ado_id,))
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            return int(row[0])

    def _row_to_inbound_event(self, cursor: sqlite3.Cursor, row: sqlite3.Row | tuple) -> InboundEventRecord:
        cols = [c[0] for c in cursor.description]
        d = dict(zip(cols, row))
        return InboundEventRecord(
            event_id=d["event_id"],
            subscription_id=d["subscription_id"],
            event_type=d["event_type"],
            ado_id=d["ado_id"],
            remote_rev=d["remote_rev"],
            received_at=d["received_at"],
            payload_hash=d["payload_hash"],
        )

