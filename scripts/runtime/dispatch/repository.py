"""Durable SQLite Persistence for Host-Native Specialist Dispatch (Milestone R11).

Strictly stdlib-only. Manages dispatch_attempts table in banco/squad.db with ACID guarantees.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union
import uuid

from scripts.domain.common import canonical_json
from scripts.domain.receipts import DispatchReceipt
from scripts.runtime.dispatch.receipts import DispatchStatus


class DispatchRepository:
    """Authoritative repository for storing and querying dispatch attempts in SQLite."""

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
                CREATE TABLE IF NOT EXISTS dispatch_attempts (
                    dispatch_id TEXT PRIMARY KEY,
                    delegation_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    sender_role TEXT NOT NULL,
                    target_role TEXT NOT NULL,
                    host_kind TEXT NOT NULL,
                    host_execution_id TEXT,
                    attempt_number INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    instruction_hash TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    receipt_payload TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    dispatched_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disp_delegation ON dispatch_attempts(delegation_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disp_work_item ON dispatch_attempts(work_item_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disp_session ON dispatch_attempts(session_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disp_status ON dispatch_attempts(status);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disp_instruction ON dispatch_attempts(instruction_hash);"
            )
            conn.commit()

    def record_attempt(
        self,
        delegation_id: str,
        session_id: str,
        work_item_id: str,
        sender_role: str,
        target_role: str,
        host_kind: str,
        instruction_hash: str,
        status: DispatchStatus = DispatchStatus.PENDING,
        host_execution_id: Optional[str] = None,
        evidence_hash: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        dispatch_id: Optional[str] = None,
    ) -> str:
        """Records a new dispatch attempt row with monotonic attempt_number."""
        assigned_id = dispatch_id or f"DSP-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        metadata_json = canonical_json(metadata or {})

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_num FROM dispatch_attempts WHERE delegation_id = ?",
                (delegation_id,),
            )
            next_num = cur.fetchone()["next_num"]

            conn.execute(
                """
                INSERT INTO dispatch_attempts (
                    dispatch_id, delegation_id, session_id, work_item_id,
                    sender_role, target_role, host_kind, host_execution_id,
                    attempt_number, status, instruction_hash, evidence_hash,
                    error_code, error_message, receipt_payload, metadata,
                    dispatched_at, acknowledged_at, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assigned_id,
                    delegation_id,
                    session_id,
                    work_item_id,
                    sender_role,
                    target_role,
                    host_kind,
                    host_execution_id,
                    next_num,
                    status.value if isinstance(status, DispatchStatus) else str(status),
                    instruction_hash,
                    evidence_hash,
                    None,
                    None,
                    None,
                    metadata_json,
                    now_iso,
                    None,
                    None,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
            return assigned_id

    def update_attempt_success(
        self,
        dispatch_id: str,
        host_execution_id: str,
        evidence_hash: str,
        receipt: DispatchReceipt,
        status: DispatchStatus = DispatchStatus.DISPATCHED,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Updates attempt to DISPATCHED or ACKNOWLEDGED with receipt payload."""
        now_iso = datetime.now(timezone.utc).isoformat()
        receipt_json = canonical_json(receipt.to_dict())

        with self._get_connection() as conn:
            if metadata is not None:
                cur = conn.execute(
                    """
                    UPDATE dispatch_attempts
                    SET status = ?, host_execution_id = ?, evidence_hash = ?,
                        receipt_payload = ?, metadata = ?, updated_at = ?
                    WHERE dispatch_id = ?
                    """,
                    (
                        status.value if isinstance(status, DispatchStatus) else str(status),
                        host_execution_id,
                        evidence_hash,
                        receipt_json,
                        canonical_json(metadata),
                        now_iso,
                        dispatch_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE dispatch_attempts
                    SET status = ?, host_execution_id = ?, evidence_hash = ?,
                        receipt_payload = ?, updated_at = ?
                    WHERE dispatch_id = ?
                    """,
                    (
                        status.value if isinstance(status, DispatchStatus) else str(status),
                        host_execution_id,
                        evidence_hash,
                        receipt_json,
                        now_iso,
                        dispatch_id,
                    ),
                )
            conn.commit()
            return cur.rowcount > 0

    def update_attempt_failure(
        self,
        dispatch_id: str,
        error_code: str,
        error_message: str,
        status: DispatchStatus = DispatchStatus.FAILED_TERMINAL,
    ) -> bool:
        """Updates attempt with failure details."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE dispatch_attempts
                SET status = ?, error_code = ?, error_message = ?, completed_at = ?, updated_at = ?
                WHERE dispatch_id = ?
                """,
                (
                    status.value if isinstance(status, DispatchStatus) else str(status),
                    error_code,
                    error_message,
                    now_iso,
                    now_iso,
                    dispatch_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_attempt_status(
        self,
        dispatch_id: str,
        status: DispatchStatus,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Updates status of a dispatch attempt."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE dispatch_attempts
                SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                WHERE dispatch_id = ?
                """,
                (
                    status.value if isinstance(status, DispatchStatus) else str(status),
                    error_code,
                    error_message,
                    now_iso,
                    dispatch_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_attempt(self, dispatch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves raw dispatch row by dispatch_id."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM dispatch_attempts WHERE dispatch_id = ?",
                (dispatch_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_active_attempt_for_delegation(self, delegation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent active/successful dispatch attempt for delegation."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM dispatch_attempts
                WHERE delegation_id = ? AND status IN ('DISPATCHED', 'ACKNOWLEDGED', 'PENDING')
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (delegation_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_attempts_for_delegation(self, delegation_id: str) -> List[Dict[str, Any]]:
        """Lists all attempts for a delegation ordered by attempt_number."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM dispatch_attempts
                WHERE delegation_id = ?
                ORDER BY attempt_number ASC
                """,
                (delegation_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_attempts_for_work_item(self, work_item_id: str) -> List[Dict[str, Any]]:
        """Lists all dispatch attempts for a work item."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM dispatch_attempts
                WHERE work_item_id = ?
                ORDER BY created_at ASC
                """,
                (work_item_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_receipt_for_delegation(self, delegation_id: str) -> Optional[DispatchReceipt]:
        """Retrieves deserialized canonical DispatchReceipt for a delegation if available."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT receipt_payload FROM dispatch_attempts
                WHERE delegation_id = ? AND receipt_payload IS NOT NULL
                  AND status IN ('DISPATCHED', 'ACKNOWLEDGED')
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (delegation_id,),
            )
            row = cur.fetchone()

        if not row or not row["receipt_payload"]:
            return None

        try:
            data = json.loads(row["receipt_payload"])
            return DispatchReceipt(
                receipt_id=data["receipt_id"],
                receipt_type=data.get("receipt_type", "DISPATCH"),
                work_item_id=data["work_item_id"],
                agent_id=data["agent_id"],
                instruction_hash=data["instruction_hash"],
                evidence_hash=data["evidence_hash"],
                target_agent_id=data["target_agent_id"],
                delegation_id=data["delegation_id"],
                dispatched_at=datetime.fromisoformat(data["dispatched_at"])
                if isinstance(data.get("dispatched_at"), str)
                else data.get("dispatched_at"),
            )
        except Exception:
            return None

    def get_attempt_count(self, delegation_id: str) -> int:
        """Returns total count of attempts for a delegation."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) AS total FROM dispatch_attempts WHERE delegation_id = ?",
                (delegation_id,),
            )
            return cur.fetchone()["total"]
