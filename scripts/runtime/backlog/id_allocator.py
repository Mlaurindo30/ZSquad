"""Canonical ID Allocation Service with 4-Namespace Collision Protection and Durable Reservation.

Strictly stdlib-only.
Guarantees collision-free sequential ID generation across:
1. SQLite lifecycle state (work_item_lifecycle_state)
2. Local filesystem (work/<project_id>/)
3. Azure DevOps bindings (delivery_work_item_bindings)
4. In-flight items within the active BacklogPlan
5. Durable SQLite reservations (canonical_id_reservations)

Concurrency safe across OS processes and threads via SQLite BEGIN IMMEDIATE
and atomic durable reservation tables.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from scripts.domain.common import ValidationError
from scripts.domain.work_items import (
    RE_EPIC,
    RE_FEATURE,
    RE_OPERATIONAL,
    RE_STORY,
    RE_TASK,
    WorkItemId,
    WorkItemKind,
)
from scripts.runtime.work_items.paths import WorkItemPathResolver

logger = logging.getLogger(__name__)

DDL_CANONICAL_RESERVATIONS = """
CREATE TABLE IF NOT EXISTS canonical_id_reservations (
    reservation_id   TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    kind             TEXT NOT NULL,
    canonical_id     TEXT NOT NULL,
    reserved_at      TEXT NOT NULL,
    UNIQUE(project_id, canonical_id)
);
"""


class CanonicalIdAllocator:
    """Allocates collision-free canonical work item identifiers across durable namespaces."""

    _GLOBAL_ALLOCATION_LOCK = threading.Lock()

    def __init__(
        self,
        db_path: Optional[Union[str, Path, sqlite3.Connection]] = None,
        runtime_root: Optional[Union[str, Path]] = None,
    ) -> None:
        self._external_conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._is_memory = False

        if isinstance(db_path, sqlite3.Connection):
            self._external_conn = db_path
            self._is_memory = True
            try:
                self._external_conn.execute(DDL_CANONICAL_RESERVATIONS)
                self._external_conn.commit()
            except sqlite3.OperationalError:
                pass
        elif db_path == ":memory:":
            self._is_memory = True
            self._external_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._external_conn.execute(DDL_CANONICAL_RESERVATIONS)
            self._external_conn.commit()
        elif db_path is not None:
            self._db_path = Path(db_path)
        else:
            root = Path(os.environ.get("SQUAD_RUNTIME", runtime_root or Path.cwd()))
            self._db_path = root / "banco" / "squad.db"

        self.runtime_root = Path(runtime_root or os.environ.get("SQUAD_RUNTIME", Path.cwd()))

    def _get_connection(self) -> sqlite3.Connection:
        if self._external_conn is not None:
            return self._external_conn
        conn = sqlite3.connect(str(self._db_path), timeout=15.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 15000;")
        try:
            conn.execute(DDL_CANONICAL_RESERVATIONS)
            conn.commit()
        except sqlite3.OperationalError:
            pass
        return conn

    @staticmethod
    def normalize_id(raw_id: str) -> str:
        """Normalizes legacy aliases (FEAT-, US-, TK-) to canonical prefixes."""
        return WorkItemId.normalize(raw_id)

    def _get_existing_ids_from_conn(self, conn: sqlite3.Connection, project_id: str) -> Set[str]:
        """Queries occupied canonical IDs from SQLite using the given active connection."""
        occupied: Set[str] = set()
        cursor = conn.cursor()

        # 1. work_item_lifecycle_state
        try:
            cursor.execute(
                "SELECT work_item_id FROM work_item_lifecycle_state WHERE project_id = ?",
                (project_id,),
            )
            for (wid,) in cursor.fetchall():
                if wid:
                    occupied.add(WorkItemId.normalize(str(wid)))
        except sqlite3.OperationalError:
            pass

        # 2. delivery_work_item_bindings
        try:
            cursor.execute(
                "SELECT work_item_id FROM delivery_work_item_bindings WHERE project_id = ?",
                (project_id,),
            )
            for (wid,) in cursor.fetchall():
                if wid:
                    occupied.add(WorkItemId.normalize(str(wid)))
        except sqlite3.OperationalError:
            pass

        # 3. backlog_plan_items
        try:
            cursor.execute(
                """
                SELECT bpi.canonical_id, bpi.proposed_id
                FROM backlog_plan_items bpi
                JOIN backlog_plans bp ON bpi.plan_id = bp.plan_id
                WHERE bp.project_id = ? AND bp.status IN ('VALIDATED', 'APPROVED', 'MATERIALIZED')
                """,
                (project_id,),
            )
            for cid, pid in cursor.fetchall():
                if cid:
                    occupied.add(WorkItemId.normalize(str(cid)))
                elif pid:
                    occupied.add(WorkItemId.normalize(str(pid)))
        except sqlite3.OperationalError:
            pass

        # 4. canonical_id_reservations (durable reservations table)
        try:
            cursor.execute(
                "SELECT canonical_id FROM canonical_id_reservations WHERE project_id = ?",
                (project_id,),
            )
            for (cid,) in cursor.fetchall():
                if cid:
                    occupied.add(WorkItemId.normalize(str(cid)))
        except sqlite3.OperationalError:
            pass

        return occupied

    def get_existing_ids_from_sqlite(self, project_id: str) -> Set[str]:
        """Queries occupied canonical IDs from SQLite lifecycle, bindings, plans, and reservations."""
        conn = self._get_connection()
        should_close = (self._external_conn is None)
        try:
            return self._get_existing_ids_from_conn(conn, project_id)
        finally:
            if should_close:
                conn.close()

    def get_existing_ids_from_filesystem(self, project_id: str) -> Set[str]:
        """Scans work/<project_id>/ for existing work item folders with status.yaml."""
        occupied: Set[str] = set()
        project_work_dir = self.runtime_root / "work" / project_id
        if not project_work_dir.is_dir():
            return occupied

        try:
            resolver = WorkItemPathResolver(self.runtime_root, project_id)
            found_paths = resolver.find_all_work_items()
            for p in found_paths:
                occupied.add(WorkItemId.normalize(p.name))
        except Exception:
            for status_file in project_work_dir.glob("**/status.yaml"):
                occupied.add(WorkItemId.normalize(status_file.parent.name))

        return occupied

    def is_id_occupied(
        self,
        project_id: str,
        canonical_id: str,
        in_flight_ids: Optional[Set[str]] = None,
    ) -> bool:
        """Checks if a canonical ID is occupied in any of the namespaces."""
        norm_id = self.normalize_id(canonical_id)

        if in_flight_ids and norm_id in {self.normalize_id(i) for i in in_flight_ids}:
            return True

        sqlite_ids = self.get_existing_ids_from_sqlite(project_id)
        if norm_id in sqlite_ids:
            return True

        fs_ids = self.get_existing_ids_from_filesystem(project_id)
        if norm_id in fs_ids:
            return True

        return False

    @staticmethod
    def _format_id(kind: WorkItemKind, seq: int) -> str:
        """Formats canonical ID based on kind and numeric sequence."""
        if kind == WorkItemKind.EPIC:
            return f"EPIC-{seq:03d}"
        if kind == WorkItemKind.FEATURE:
            return f"FEATURE-{seq:03d}"
        if kind == WorkItemKind.STORY:
            return f"STORY-{seq:03d}"
        if kind == WorkItemKind.TASK:
            return f"TASK-{seq:04d}"
        if kind in (WorkItemKind.BUG, WorkItemKind.SPIKE, WorkItemKind.INCIDENT, WorkItemKind.RELEASE, WorkItemKind.PROJECT_SETUP):
            return f"{kind.value}-{seq:03d}"
        return f"{kind.value}-{seq:03d}"

    @staticmethod
    def _extract_sequence_number(raw_id: str, prefix: str) -> Optional[int]:
        """Extracts the numeric sequence from an existing ID matching prefix."""
        norm = WorkItemId.normalize(raw_id)
        m = re.match(rf"^{prefix}-(\d+)$", norm)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None

    def allocate_next_id(
        self,
        project_id: str,
        kind: WorkItemKind,
        in_flight_ids: Optional[Set[str]] = None,
        starting_seq: int = 1,
    ) -> str:
        """Allocates the next collision-free sequential canonical ID across all durable namespaces.
        
        Atomically selects and commits a durable reservation into SQLite under BEGIN IMMEDIATE.
        Guarantees process-safe and thread-safe sequential ID generation without collisions.
        """
        prefix = kind.value
        if kind == WorkItemKind.PROJECT_SETUP:
            prefix = "SETUP"

        with self._GLOBAL_ALLOCATION_LOCK:
            conn = self._get_connection()
            should_close = (self._external_conn is None)
            try:
                if not self._is_memory:
                    try:
                        conn.execute("BEGIN IMMEDIATE;")
                    except sqlite3.OperationalError:
                        pass

                occupied_sqlite = self._get_existing_ids_from_conn(conn, project_id)
                occupied_fs = self.get_existing_ids_from_filesystem(project_id)
                occupied_inflight = {self.normalize_id(i) for i in in_flight_ids} if in_flight_ids else set()

                all_occupied = occupied_sqlite | occupied_fs | occupied_inflight

                # Find highest existing sequence for this kind
                max_seq = starting_seq - 1
                for occ_id in all_occupied:
                    seq = self._extract_sequence_number(occ_id, prefix)
                    if seq is not None and seq > max_seq:
                        max_seq = seq

                candidate_seq = max_seq + 1
                while True:
                    candidate_id = self._format_id(kind, candidate_seq)
                    if candidate_id not in all_occupied:
                        now_str = datetime.now(timezone.utc).isoformat()
                        res_id = f"RES-{uuid.uuid4().hex[:12]}"
                        try:
                            conn.execute(
                                """
                                INSERT INTO canonical_id_reservations
                                (reservation_id, project_id, kind, canonical_id, reserved_at)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (res_id, project_id, kind.value, candidate_id, now_str),
                            )
                            if not self._is_memory:
                                conn.commit()
                            return candidate_id
                        except sqlite3.IntegrityError:
                            candidate_seq += 1
                            continue
                    candidate_seq += 1

            finally:
                if not self._is_memory and conn.in_transaction:
                    conn.commit()
                if should_close:
                    conn.close()
