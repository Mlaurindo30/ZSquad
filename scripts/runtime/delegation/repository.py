"""Delegation Repository for Milestone R10.

Persists and retrieves DelegationEnvelopes and associated metadata in SQLite squad.db.
Provides deterministic idempotency and query capabilities. Strictly stdlib-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

from scripts.domain.delegation import DelegationEnvelope


class DelegationRepository:
    """Authoritative repository for storing and querying delegation envelopes in SQLite."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delegation_envelopes (
                    delegation_id TEXT PRIMARY KEY,
                    activation_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_revision INTEGER NOT NULL DEFAULT 1,
                    project_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    selected_agent_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    instruction_hash TEXT NOT NULL,
                    context_fingerprint TEXT NOT NULL,
                    preflight_id TEXT NOT NULL,
                    preflight_decision TEXT NOT NULL,
                    envelope_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_del_work_item ON delegation_envelopes(work_item_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_del_session ON delegation_envelopes(session_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_del_activation ON delegation_envelopes(activation_id);"
            )
            conn.commit()

    def save(
        self,
        envelope: DelegationEnvelope,
        activation_id: str,
        assignment_id: str,
        session_id: str,
        session_revision: int,
        project_id: str,
        selected_agent_id: str,
        stage: str,
        status: str,
        context_fingerprint: str,
        preflight_id: str,
        preflight_decision: str,
    ) -> None:
        """Persists a new or updated delegation envelope record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(envelope.to_dict())

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO delegation_envelopes (
                    delegation_id, activation_id, assignment_id, session_id, session_revision,
                    project_id, work_item_id, selected_agent_id, stage, status,
                    instruction_hash, context_fingerprint, preflight_id, preflight_decision,
                    envelope_payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.delegation_id,
                    activation_id,
                    assignment_id,
                    session_id,
                    session_revision,
                    project_id,
                    envelope.work_item_id,
                    selected_agent_id,
                    stage,
                    status,
                    envelope.instruction_hash,
                    context_fingerprint,
                    preflight_id,
                    preflight_decision,
                    payload_json,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()

    def find_existing(
        self,
        activation_id: str,
        session_id: str,
        session_revision: int,
    ) -> Optional[DelegationEnvelope]:
        """Finds an existing ready delegation envelope for idempotent reuse."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT envelope_payload FROM delegation_envelopes
                WHERE activation_id = ? AND session_id = ? AND session_revision = ?
                  AND status = 'READY_FOR_DISPATCH'
                ORDER BY created_at DESC LIMIT 1
                """,
                (activation_id, session_id, session_revision),
            )
            row = cur.fetchone()

        if not row:
            return None

        try:
            data = json.loads(row["envelope_payload"])
            return DelegationEnvelope(**data)
        except Exception:
            return None

    def get_by_id(self, delegation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves raw delegation row by ID."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM delegation_envelopes WHERE delegation_id = ?",
                (delegation_id,),
            )
            row = cur.fetchone()

        if not row:
            return None
        return dict(row)

    def get_envelope(self, delegation_id: str) -> Optional[DelegationEnvelope]:
        """Retrieves deserialized DelegationEnvelope by ID."""
        row = self.get_by_id(delegation_id)
        if not row:
            return None
        data = json.loads(row["envelope_payload"])
        return DelegationEnvelope(**data)

    def list_by_work_item(self, work_item_id: str) -> List[Dict[str, Any]]:
        """Lists all delegation records associated with a work item."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM delegation_envelopes WHERE work_item_id = ? ORDER BY created_at ASC",
                (work_item_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def update_status(self, delegation_id: str, status: str) -> bool:
        """Updates the status of an existing delegation envelope."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE delegation_envelopes
                SET status = ?, updated_at = ?
                WHERE delegation_id = ?
                """,
                (status, now_iso, delegation_id),
            )
            conn.commit()
            return cur.rowcount > 0

