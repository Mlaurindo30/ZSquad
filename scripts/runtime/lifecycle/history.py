"""SQLite persistence and transition history repository for the Lifecycle Engine.

Strictly stdlib-only. Transactional WAL operations with idempotency.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from scripts.domain.common import canonical_json


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecycleRepository:
    """Repository managing work_item_lifecycle_state and append-only lifecycle_history in SQLite."""

    DDL_STATE = """
    CREATE TABLE IF NOT EXISTS work_item_lifecycle_state (
        work_item_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        cycle_id TEXT NOT NULL,
        current_stage TEXT NOT NULL,
        owner_role TEXT NOT NULL,
        phase_started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_transition_id TEXT NOT NULL
    );
    """

    DDL_HISTORY = """
    CREATE TABLE IF NOT EXISTS lifecycle_history (
        transition_id TEXT PRIMARY KEY,
        work_item_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        cycle_id TEXT NOT NULL,
        from_stage TEXT NOT NULL,
        to_stage TEXT NOT NULL,
        initiated_by TEXT NOT NULL,
        gate_decision_id TEXT,
        handoff_id TEXT,
        duration_seconds REAL,
        idempotency_key TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT
    );
    """

    DDL_INDICES = [
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_state_proj_stage ON work_item_lifecycle_state (project_id, current_stage);",
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_history_item ON lifecycle_history (work_item_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_history_project ON lifecycle_history (project_id, created_at DESC);",
    ]

    def __init__(self, db_path: Optional[Union[str, Path, sqlite3.Connection]] = None) -> None:
        self._external_conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._is_memory = False

        if isinstance(db_path, sqlite3.Connection):
            self._external_conn = db_path
            self._is_memory = True
        elif db_path == ":memory:":
            self._db_path = None
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
                conn.execute(self.DDL_STATE)
                conn.execute(self.DDL_HISTORY)
                for idx in self.DDL_INDICES:
                    conn.execute(idx)

    def get_current_state(
        self,
        work_item_id: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves the current lifecycle state record for a work item."""
        query = """
        SELECT work_item_id, project_id, cycle_id, current_stage,
               owner_role, phase_started_at, updated_at, last_transition_id
        FROM work_item_lifecycle_state
        WHERE work_item_id = ?
        """
        if conn is not None:
            cursor = conn.cursor()
            cursor.execute(query, (work_item_id,))
            row = cursor.fetchone()
            return self._row_to_state(row) if row else None

        with self.connection() as c:
            cursor = c.cursor()
            cursor.execute(query, (work_item_id,))
            row = cursor.fetchone()
            return self._row_to_state(row) if row else None

    def _row_to_state(self, row: tuple) -> Dict[str, Any]:
        return {
            "work_item_id": row[0],
            "project_id": row[1],
            "cycle_id": row[2],
            "current_stage": row[3],
            "owner_role": row[4],
            "phase_started_at": row[5],
            "updated_at": row[6],
            "last_transition_id": row[7],
        }

    def record_transition(
        self,
        conn: sqlite3.Connection,
        transition_id: str,
        work_item_id: str,
        project_id: str,
        cycle_id: str,
        from_stage: str,
        to_stage: str,
        initiated_by: str,
        owner_role: str,
        idempotency_key: str,
        gate_decision_id: Optional[str] = None,
        handoff_id: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        payload: Optional[Dict[str, Any]] = None,
        now_iso: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Atomically records a transition in lifecycle_history and upserts work_item_lifecycle_state.

        Returns:
            Tuple of (transition_dict, was_created). If idempotency_key already exists,
            returns the existing transition and False.
        """
        timestamp = now_iso or _utc_now_iso()
        cursor = conn.cursor()

        # Check idempotency
        cursor.execute(
            """
            SELECT transition_id, work_item_id, project_id, cycle_id, from_stage,
                   to_stage, initiated_by, gate_decision_id, handoff_id, duration_seconds,
                   idempotency_key, created_at, payload_json
            FROM lifecycle_history
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "transition_id": existing[0],
                "work_item_id": existing[1],
                "project_id": existing[2],
                "cycle_id": existing[3],
                "from_stage": existing[4],
                "to_stage": existing[5],
                "initiated_by": existing[6],
                "gate_decision_id": existing[7],
                "handoff_id": existing[8],
                "duration_seconds": existing[9],
                "idempotency_key": existing[10],
                "created_at": existing[11],
            }, False

        # Insert history record
        payload_str = canonical_json(payload or {})
        cursor.execute(
            """
            INSERT INTO lifecycle_history (
                transition_id, work_item_id, project_id, cycle_id,
                from_stage, to_stage, initiated_by, gate_decision_id,
                handoff_id, duration_seconds, idempotency_key, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                work_item_id,
                project_id,
                cycle_id,
                from_stage,
                to_stage,
                initiated_by,
                gate_decision_id,
                handoff_id,
                duration_seconds,
                idempotency_key,
                timestamp,
                payload_str,
            ),
        )

        # Upsert active state projection
        cursor.execute(
            """
            INSERT INTO work_item_lifecycle_state (
                work_item_id, project_id, cycle_id, current_stage,
                owner_role, phase_started_at, updated_at, last_transition_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                cycle_id = excluded.cycle_id,
                current_stage = excluded.current_stage,
                owner_role = excluded.owner_role,
                phase_started_at = excluded.phase_started_at,
                updated_at = excluded.updated_at,
                last_transition_id = excluded.last_transition_id
            """,
            (
                work_item_id,
                project_id,
                cycle_id,
                to_stage,
                owner_role,
                timestamp,
                timestamp,
                transition_id,
            ),
        )

        record = {
            "transition_id": transition_id,
            "work_item_id": work_item_id,
            "project_id": project_id,
            "cycle_id": cycle_id,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "initiated_by": initiated_by,
            "gate_decision_id": gate_decision_id,
            "handoff_id": handoff_id,
            "duration_seconds": duration_seconds,
            "idempotency_key": idempotency_key,
            "created_at": timestamp,
        }
        return record, True

    def get_history(
        self,
        work_item_id: str,
        limit: int = 50,
        conn: Optional[sqlite3.Connection] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves chronological transition history for a work item."""
        query = """
        SELECT transition_id, work_item_id, project_id, cycle_id, from_stage,
               to_stage, initiated_by, gate_decision_id, handoff_id, duration_seconds,
               idempotency_key, created_at, payload_json
        FROM lifecycle_history
        WHERE work_item_id = ?
        ORDER BY created_at ASC
        LIMIT ?
        """
        def _fetch(c: sqlite3.Connection) -> List[Dict[str, Any]]:
            cursor = c.cursor()
            cursor.execute(query, (work_item_id, limit))
            results = []
            for row in cursor.fetchall():
                results.append({
                    "transition_id": row[0],
                    "work_item_id": row[1],
                    "project_id": row[2],
                    "cycle_id": row[3],
                    "from_stage": row[4],
                    "to_stage": row[5],
                    "initiated_by": row[6],
                    "gate_decision_id": row[7],
                    "handoff_id": row[8],
                    "duration_seconds": row[9],
                    "idempotency_key": row[10],
                    "created_at": row[11],
                })
            return results

        if conn is not None:
            return _fetch(conn)

        with self.connection() as c:
            return _fetch(c)
