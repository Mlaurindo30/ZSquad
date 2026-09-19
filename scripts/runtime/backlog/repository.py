"""Authoritative SQLite WAL Repository for Backlog Planning, Items and QBC Decisions.

Strictly stdlib-only.
Enforces foreign keys, WAL pragma journal mode, and atomic transaction semantics.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Set, Union
import uuid

from scripts.domain.backlog import BacklogPlan, BacklogPlanItem, BacklogPlanStatus
from scripts.domain.common import ValidationError, canonical_json
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.lifecycle.history import LifecycleRepository

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class QbcDecisionRecord:
    """Record representing a QBC evaluation decision."""

    decision_id: str
    plan_id: str
    proposed_id: str
    kind: str
    title: str
    matched_id: Optional[str]
    matched_source: str  # SQLITE, FILESYSTEM, AZURE_BOARDS, IN_FLIGHT_PLAN, NONE
    similarity_score: float
    decision_status: str  # PASSED, AMBIGUITY_DETECTED, DUPLICATE_REJECTED, OVERRIDDEN
    evaluated_at: str = field(default_factory=_utc_now_iso)
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None


class BacklogPlanRepository:
    """Repository managing backlog_plans, backlog_plan_items and backlog_qbc_decisions."""

    DDL_PLANS = """
    CREATE TABLE IF NOT EXISTS backlog_plans (
        plan_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('DRAFT', 'VALIDATED', 'APPROVED', 'MATERIALIZED', 'REJECTED')),
        created_by TEXT NOT NULL,
        approved_by TEXT,
        content_hash TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    DDL_ITEMS = """
    CREATE TABLE IF NOT EXISTS backlog_plan_items (
        item_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        proposed_id TEXT NOT NULL,
        canonical_id TEXT,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        story_points INTEGER,
        parent_id TEXT,
        sequence_order INTEGER NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES backlog_plans(plan_id) ON DELETE CASCADE
    );
    """

    DDL_QBC_DECISIONS = """
    CREATE TABLE IF NOT EXISTS backlog_qbc_decisions (
        decision_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        proposed_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        matched_id TEXT,
        matched_source TEXT CHECK(matched_source IN ('SQLITE', 'FILESYSTEM', 'AZURE_BOARDS', 'IN_FLIGHT_PLAN', 'NONE')),
        similarity_score REAL NOT NULL DEFAULT 0.0,
        decision_status TEXT NOT NULL CHECK(decision_status IN ('PASSED', 'AMBIGUITY_DETECTED', 'DUPLICATE_REJECTED', 'OVERRIDDEN')),
        resolution_notes TEXT,
        resolved_by TEXT,
        evaluated_at TEXT NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES backlog_plans(plan_id) ON DELETE CASCADE
    );
    """

    # Reused authoritative schema from R4 LifecycleRepository (Single Authority)
    DDL_LIFECYCLE_STATE = LifecycleRepository.DDL_STATE
    DDL_LIFECYCLE_HISTORY = LifecycleRepository.DDL_HISTORY

    DDL_INDICES = [
        "CREATE INDEX IF NOT EXISTS idx_backlog_plans_proj_status ON backlog_plans(project_id, status);",
        "CREATE INDEX IF NOT EXISTS idx_plan_items_plan_seq ON backlog_plan_items(plan_id, sequence_order);",
        "CREATE INDEX IF NOT EXISTS idx_plan_items_proposed ON backlog_plan_items(proposed_id);",
        "CREATE INDEX IF NOT EXISTS idx_qbc_decisions_plan ON backlog_qbc_decisions(plan_id);",
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_state_proj_stage ON work_item_lifecycle_state (project_id, current_stage);",
    ]

    def __init__(self, db_path: Optional[Union[str, Path, sqlite3.Connection]] = None) -> None:
        """Initializes the BacklogPlanRepository with schema bootstrap.
        
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
                conn.execute(self.DDL_PLANS)
                conn.execute(self.DDL_ITEMS)
                conn.execute(self.DDL_QBC_DECISIONS)
                conn.execute(self.DDL_LIFECYCLE_STATE)
                conn.execute(self.DDL_LIFECYCLE_HISTORY)
                for index_sql in self.DDL_INDICES:
                    conn.execute(index_sql)

    def close(self) -> None:
        """Closes any persistent external or in-memory SQLite connection."""
        if self._external_conn is not None:
            try:
                self._external_conn.close()
            except Exception:
                pass
            self._external_conn = None

    @staticmethod
    def compute_content_hash(plan: BacklogPlan) -> str:
        """Computes a deterministic SHA-256 hash of a plan and its items."""
        data = {
            "plan_id": plan.plan_id,
            "project_id": plan.project_id,
            "created_by": plan.created_by,
            "items": [
                {
                    "proposed_id": item.proposed_id,
                    "kind": item.kind.value,
                    "title": item.title,
                    "description": item.description,
                    "story_points": item.story_points,
                    "parent_id": item.parent_id,
                    "metadata": item.metadata,
                }
                for item in plan.items
            ],
            "metadata": plan.metadata,
        }
        raw_json = canonical_json(data)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    def save_plan(self, plan: BacklogPlan, content_hash: Optional[str] = None) -> None:
        """Persists a BacklogPlan and all its items atomically inside a transaction."""
        now_iso = _utc_now_iso()
        c_hash = content_hash or self.compute_content_hash(plan)
        meta_json = canonical_json(plan.metadata)

        with self.connection() as conn:
            with conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status, content_hash FROM backlog_plans WHERE plan_id = ?", (plan.plan_id,))
                existing_row = cursor.fetchone()
                target_status = plan.status.value
                target_approved_by = plan.approved_by
                if existing_row:
                    prev_status, prev_hash = existing_row
                    if prev_status == BacklogPlanStatus.APPROVED.value and prev_hash != c_hash:
                        logger.warning(
                            f"Plan '{plan.plan_id}' content was mutated after approval. Invalidating approval and reverting to DRAFT."
                        )
                        target_status = BacklogPlanStatus.DRAFT.value
                        target_approved_by = None

                # Upsert plan header
                conn.execute(
                    """
                    INSERT INTO backlog_plans (
                        plan_id, project_id, status, created_by, approved_by,
                        content_hash, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        status = excluded.status,
                        approved_by = excluded.approved_by,
                        content_hash = excluded.content_hash,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        plan.plan_id,
                        plan.project_id,
                        target_status,
                        plan.created_by,
                        target_approved_by,
                        c_hash,
                        meta_json,
                        now_iso,
                        now_iso,
                    ),
                )

                # Delete old items if updating draft to ensure synchronization
                conn.execute("DELETE FROM backlog_plan_items WHERE plan_id = ?", (plan.plan_id,))

                # Insert items in sequence
                for seq, item in enumerate(plan.items):
                    item_id = f"{plan.plan_id}:{seq:04d}"
                    item_meta_json = canonical_json(item.metadata)
                    canonical_id = item.metadata.get("canonical_id", item.proposed_id)
                    conn.execute(
                        """
                        INSERT INTO backlog_plan_items (
                            item_id, plan_id, proposed_id, canonical_id, kind,
                            title, description, story_points, parent_id,
                            sequence_order, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            plan.plan_id,
                            item.proposed_id,
                            canonical_id,
                            item.kind.value,
                            item.title,
                            item.description,
                            item.story_points,
                            item.parent_id,
                            seq,
                            item_meta_json,
                            now_iso,
                        ),
                    )

    def get_plan(self, plan_id: str) -> Optional[BacklogPlan]:
        """Loads a BacklogPlan and all its items by plan_id."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM backlog_plans WHERE plan_id = ?", (plan_id,))
            plan_row = cursor.fetchone()
            if not plan_row:
                return None

            col_names = [d[0] for d in cursor.description]
            plan_dict = dict(zip(col_names, plan_row))

            cursor.execute(
                "SELECT * FROM backlog_plan_items WHERE plan_id = ? ORDER BY sequence_order ASC",
                (plan_id,),
            )
            item_rows = cursor.fetchall()
            item_cols = [d[0] for d in cursor.description]

            items: List[BacklogPlanItem] = []
            for r in item_rows:
                idict = dict(zip(item_cols, r))
                meta = json.loads(idict["metadata_json"]) if idict.get("metadata_json") else {}
                if idict.get("canonical_id"):
                    meta["canonical_id"] = idict["canonical_id"]

                items.append(
                    BacklogPlanItem(
                        proposed_id=idict["proposed_id"],
                        kind=WorkItemKind(idict["kind"]),
                        title=idict["title"],
                        description=idict["description"],
                        story_points=idict["story_points"],
                        parent_id=idict["parent_id"],
                        metadata=meta,
                    )
                )

            return BacklogPlan(
                plan_id=plan_dict["plan_id"],
                project_id=plan_dict["project_id"],
                status=BacklogPlanStatus(plan_dict["status"]),
                items=items,
                created_by=plan_dict["created_by"],
                approved_by=plan_dict["approved_by"],
                metadata=json.loads(plan_dict["metadata_json"]) if plan_dict.get("metadata_json") else {},
            )

    def list_plans(
        self,
        project_id: str,
        status: Optional[BacklogPlanStatus] = None,
    ) -> List[BacklogPlan]:
        """Lists plans for a project, optionally filtered by status."""
        query = "SELECT plan_id FROM backlog_plans WHERE project_id = ?"
        params: List[Any] = [project_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC"

        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        result: List[BacklogPlan] = []
        for (pid,) in rows:
            plan = self.get_plan(pid)
            if plan:
                result.append(plan)
        return result

    def update_plan_status(
        self,
        plan_id: str,
        new_status: BacklogPlanStatus,
        approved_by: Optional[str] = None,
    ) -> None:
        """Updates plan status and approval metadata."""
        now_iso = _utc_now_iso()
        with self.connection() as conn:
            with conn:
                if approved_by is not None:
                    conn.execute(
                        """
                        UPDATE backlog_plans
                        SET status = ?, approved_by = ?, updated_at = ?
                        WHERE plan_id = ?
                        """,
                        (new_status.value, approved_by, now_iso, plan_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE backlog_plans
                        SET status = ?, updated_at = ?
                        WHERE plan_id = ?
                        """,
                        (new_status.value, now_iso, plan_id),
                    )

    def record_qbc_decision(self, decision: QbcDecisionRecord) -> None:
        """Records an evaluated QBC decision."""
        with self.connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO backlog_qbc_decisions (
                        decision_id, plan_id, proposed_id, kind, title,
                        matched_id, matched_source, similarity_score,
                        decision_status, resolution_notes, resolved_by, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(decision_id) DO UPDATE SET
                        matched_id = excluded.matched_id,
                        matched_source = excluded.matched_source,
                        similarity_score = excluded.similarity_score,
                        decision_status = excluded.decision_status,
                        resolution_notes = excluded.resolution_notes,
                        resolved_by = excluded.resolved_by,
                        evaluated_at = excluded.evaluated_at
                    """,
                    (
                        decision.decision_id,
                        decision.plan_id,
                        decision.proposed_id,
                        decision.kind,
                        decision.title,
                        decision.matched_id,
                        decision.matched_source,
                        decision.similarity_score,
                        decision.decision_status,
                        decision.resolution_notes,
                        decision.resolved_by,
                        decision.evaluated_at,
                    ),
                )

    def get_qbc_decisions(self, plan_id: str) -> List[QbcDecisionRecord]:
        """Fetches all QBC decisions for a plan."""
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM backlog_qbc_decisions WHERE plan_id = ? ORDER BY evaluated_at ASC",
                (plan_id,),
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]

            decisions: List[QbcDecisionRecord] = []
            for r in rows:
                d = dict(zip(cols, r))
                decisions.append(
                    QbcDecisionRecord(
                        decision_id=d["decision_id"],
                        plan_id=d["plan_id"],
                        proposed_id=d["proposed_id"],
                        kind=d["kind"],
                        title=d["title"],
                        matched_id=d["matched_id"],
                        matched_source=d["matched_source"],
                        similarity_score=float(d["similarity_score"]),
                        decision_status=d["decision_status"],
                        evaluated_at=d["evaluated_at"],
                        resolution_notes=d["resolution_notes"],
                        resolved_by=d["resolved_by"],
                    )
                )
            return decisions

    def resolve_qbc_decision(
        self,
        decision_id: str,
        resolved_by: str,
        notes: str,
        new_status: str = "OVERRIDDEN",
    ) -> None:
        """Resolves an AMBIGUITY_DETECTED decision to OVERRIDDEN with audit trail."""
        now_iso = _utc_now_iso()
        with self.connection() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE backlog_qbc_decisions
                    SET decision_status = ?, resolution_notes = ?, resolved_by = ?, evaluated_at = ?
                    WHERE decision_id = ?
                    """,
                    (new_status, notes, resolved_by, now_iso, decision_id),
                )
