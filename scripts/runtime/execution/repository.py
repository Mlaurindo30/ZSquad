"""SQLite persistence repository for execution and validation receipts.

Strictly stdlib-only.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Union

from scripts.domain.common import canonical_json
from scripts.domain.receipts import (
    BaseReceipt,
    ExecutionReceipt,
    GovernanceReceipt,
    QAReceipt,
    ReceiptType,
    ReviewReceipt,
    SecurityReceipt,
    TestReceipt,
)


def _resolve_default_db_path() -> Path:
    squad_root = Path(os.environ.get("SQUAD_RUNTIME", Path.cwd()))
    return squad_root / "banco" / "squad.db"


def _row_to_dict(cursor: sqlite3.Cursor, row: Any) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if cursor.description:
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    return {}


class ExecutionReceiptRepository:
    """Manages SQLite storage for execution and validation receipts in WAL mode."""

    def __init__(self, db_path: Optional[Union[str, Path, sqlite3.Connection]] = None) -> None:
        if isinstance(db_path, sqlite3.Connection):
            self._shared_conn: Optional[sqlite3.Connection] = db_path
            self._db_path: Optional[Path] = None
        else:
            self._shared_conn = None
            self._db_path = Path(db_path) if db_path else _resolve_default_db_path()

        self._ensure_tables()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        if self._shared_conn is not None:
            yield self._shared_conn
        else:
            assert self._db_path is not None
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=30000;")
                yield conn
            finally:
                conn.close()

    def _ensure_tables(self) -> None:
        with self.connection() as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        work_item_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        receipt_type TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        instruction_hash TEXT NOT NULL,
                        evidence_hash TEXT NOT NULL,
                        files_modified TEXT NOT NULL,
                        tests_executed TEXT NOT NULL,
                        test_exit_code INTEGER NOT NULL,
                        diff_summary TEXT NOT NULL,
                        raw_payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_exec_receipts_work_item 
                    ON execution_receipts(work_item_id, stage);
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS validation_receipts (
                        receipt_id TEXT PRIMARY KEY,
                        work_item_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        receipt_type TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        instruction_hash TEXT NOT NULL,
                        evidence_hash TEXT NOT NULL,
                        role TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        details TEXT NOT NULL,
                        raw_payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_val_receipts_work_item 
                    ON validation_receipts(work_item_id, stage, receipt_type);
                    """
                )

    def save_execution_receipt(
        self,
        receipt: ExecutionReceipt,
        project_id: str,
        stage: str,
    ) -> None:
        payload_dict = {
            "receipt_id": receipt.receipt_id,
            "receipt_type": receipt.receipt_type,
            "work_item_id": receipt.work_item_id,
            "agent_id": receipt.agent_id,
            "instruction_hash": receipt.instruction_hash,
            "evidence_hash": receipt.evidence_hash,
            "files_modified": receipt.files_modified,
            "tests_executed": receipt.tests_executed,
            "test_exit_code": receipt.test_exit_code,
            "diff_summary": receipt.diff_summary,
            "created_at": receipt.created_at.isoformat(),
        }
        with self.connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO execution_receipts (
                        receipt_id, work_item_id, project_id, agent_id, receipt_type,
                        stage, instruction_hash, evidence_hash, files_modified,
                        tests_executed, test_exit_code, diff_summary, raw_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.receipt_id,
                        receipt.work_item_id,
                        project_id,
                        receipt.agent_id,
                        receipt.receipt_type,
                        stage,
                        receipt.instruction_hash,
                        receipt.evidence_hash,
                        json.dumps(receipt.files_modified),
                        json.dumps(receipt.tests_executed),
                        receipt.test_exit_code,
                        receipt.diff_summary,
                        canonical_json(payload_dict),
                        receipt.created_at.isoformat(),
                    ),
                )

    def save_validation_receipt(
        self,
        receipt: BaseReceipt,
        project_id: str,
        stage: str,
        role: str,
        verdict: str,
        details: Dict[str, Any],
    ) -> None:
        payload_dict = {
            "receipt_id": receipt.receipt_id,
            "receipt_type": receipt.receipt_type,
            "work_item_id": receipt.work_item_id,
            "agent_id": receipt.agent_id,
            "instruction_hash": receipt.instruction_hash,
            "evidence_hash": receipt.evidence_hash,
            "role": role,
            "verdict": verdict,
            "details": details,
            "created_at": receipt.created_at.isoformat(),
        }
        with self.connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO validation_receipts (
                        receipt_id, work_item_id, project_id, agent_id, receipt_type,
                        stage, instruction_hash, evidence_hash, role, verdict,
                        details, raw_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.receipt_id,
                        receipt.work_item_id,
                        project_id,
                        receipt.agent_id,
                        receipt.receipt_type,
                        stage,
                        receipt.instruction_hash,
                        receipt.evidence_hash,
                        role,
                        verdict,
                        json.dumps(details),
                        canonical_json(payload_dict),
                        receipt.created_at.isoformat(),
                    ),
                )

    def get_latest_execution_receipt(
        self,
        work_item_id: str,
        stage: Optional[str] = None,
    ) -> Optional[ExecutionReceipt]:
        with self.connection() as conn:
            query = "SELECT * FROM execution_receipts WHERE work_item_id = ?"
            params: List[Any] = [work_item_id]
            if stage:
                query += " AND stage = ?"
                params.append(stage)
            query += " ORDER BY created_at DESC LIMIT 1"

            cursor = conn.execute(query, params)
            raw_row = cursor.fetchone()
            if not raw_row:
                return None
            row = _row_to_dict(cursor, raw_row)

            return ExecutionReceipt(
                receipt_id=row["receipt_id"],
                receipt_type=row["receipt_type"],
                work_item_id=row["work_item_id"],
                agent_id=row["agent_id"],
                instruction_hash=row["instruction_hash"],
                evidence_hash=row["evidence_hash"],
                files_modified=json.loads(row["files_modified"]),
                tests_executed=json.loads(row["tests_executed"]),
                test_exit_code=row["test_exit_code"],
                diff_summary=row["diff_summary"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def get_validation_receipts(
        self,
        work_item_id: str,
        receipt_type: Optional[Union[str, ReceiptType]] = None,
        stage: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            query = "SELECT * FROM validation_receipts WHERE work_item_id = ?"
            params: List[Any] = [work_item_id]
            if receipt_type:
                r_type = receipt_type.value if isinstance(receipt_type, ReceiptType) else str(receipt_type)
                query += " AND receipt_type = ?"
                params.append(r_type)
            if stage:
                query += " AND stage = ?"
                params.append(stage)
            query += " ORDER BY created_at DESC"

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for raw_row in rows:
                row = _row_to_dict(cursor, raw_row)
                results.append({
                    "receipt_id": row["receipt_id"],
                    "work_item_id": row["work_item_id"],
                    "project_id": row["project_id"],
                    "agent_id": row["agent_id"],
                    "receipt_type": row["receipt_type"],
                    "stage": row["stage"],
                    "instruction_hash": row["instruction_hash"],
                    "evidence_hash": row["evidence_hash"],
                    "role": row["role"],
                    "verdict": row["verdict"],
                    "details": json.loads(row["details"]),
                    "created_at": row["created_at"],
                })
            return results

    def get_work_item_current_stage(self, work_item_id: str) -> Optional[str]:
        """Queries current lifecycle stage for work item from work_item_lifecycle_state if present."""
        with self.connection() as conn:
            try:
                cursor = conn.execute(
                    "SELECT current_stage FROM work_item_lifecycle_state WHERE work_item_id = ?",
                    (work_item_id,),
                )
                row = cursor.fetchone()
                if row:
                    return row[0]
            except sqlite3.OperationalError:
                return None
        return None
