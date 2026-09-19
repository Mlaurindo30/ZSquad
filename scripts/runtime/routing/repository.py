"""Routing Repository — SQLite persistence for routing decisions.

Idempotent storage. Audit trail. Decision replay.

Strictly stdlib-only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from scripts.domain.delegation import RoutingDecision, RoutingStatus


DDL_ROUTING_DECISIONS = """
CREATE TABLE IF NOT EXISTS routing_decisions (
    decision_id     TEXT    PRIMARY KEY,
    work_item_id    TEXT    NOT NULL,
    stage           TEXT    NOT NULL,
    required_role   TEXT    NOT NULL DEFAULT '',
    required_capability TEXT NOT NULL DEFAULT '',
    candidates      TEXT    NOT NULL,  -- JSON array
    selected_agent_id TEXT,
    status          TEXT    NOT NULL,
    reason          TEXT    NOT NULL,
    timestamp       TEXT    NOT NULL,
    UNIQUE(work_item_id, stage, required_role, required_capability)
);
"""

DDL_ROUTING_HISTORY = """
CREATE TABLE IF NOT EXISTS routing_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     TEXT    NOT NULL,
    work_item_id    TEXT    NOT NULL,
    stage           TEXT    NOT NULL,
    selected_agent_id TEXT,
    status          TEXT    NOT NULL,
    reason          TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL
);
"""


class RoutingRepository:
    """SQLite repository for persisting and querying routing decisions.

    Idempotent: recording the same (work_item_id, stage, role, capability)
    returns the existing decision without duplication.
    """

    DDL_DECISIONS = DDL_ROUTING_DECISIONS
    DDL_HISTORY = DDL_ROUTING_HISTORY

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._bootstrap_schema()

    def _bootstrap_schema(self) -> None:
        self._conn.executescript(self.DDL_DECISIONS + self.DDL_HISTORY)
        self._conn.commit()

    def record_decision(self, decision: RoutingDecision) -> RoutingDecision:
        """Persists a routing decision. Idempotent on (work_item_id, stage, role, capability).

        Returns:
            The persisted decision (may be existing if idempotent match).
        """
        candidates_json = json.dumps(decision.candidates)
        timestamp_str = decision.timestamp.isoformat()
        role_key = decision.required_role or ""
        cap_key = decision.required_capability or ""

        try:
            self._conn.execute(
                """INSERT INTO routing_decisions
                   (decision_id, work_item_id, stage, required_role, required_capability,
                    candidates, selected_agent_id, status, reason, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.decision_id,
                    decision.work_item_id,
                    decision.stage,
                    role_key,
                    cap_key,
                    candidates_json,
                    decision.selected_agent_id,
                    decision.status.value,
                    decision.reason,
                    timestamp_str,
                ),
            )
        except sqlite3.IntegrityError:
            # Idempotent: return existing decision
            existing = self._get_by_unique_key(
                decision.work_item_id,
                decision.stage,
                decision.required_role,
                decision.required_capability,
            )
            if existing is not None:
                return existing

        # Record in history for audit trail
        self._conn.execute(
            """INSERT INTO routing_history
               (decision_id, work_item_id, stage, selected_agent_id, status, reason, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.decision_id,
                decision.work_item_id,
                decision.stage,
                decision.selected_agent_id,
                decision.status.value,
                decision.reason,
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()
        return decision

    def get_decision(self, decision_id: str) -> Optional[RoutingDecision]:
        """Retrieves a routing decision by ID."""
        row = self._conn.execute(
            "SELECT * FROM routing_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        return self._row_to_decision(row) if row else None

    def get_decisions_for_work_item(self, work_item_id: str) -> List[RoutingDecision]:
        """Returns all routing decisions for a work item."""
        rows = self._conn.execute(
            "SELECT * FROM routing_decisions WHERE work_item_id = ? ORDER BY timestamp",
            (work_item_id,),
        ).fetchall()
        return [self._row_to_decision(r) for r in rows if r]

    def _get_by_unique_key(
        self,
        work_item_id: str,
        stage: str,
        required_role: Optional[str],
        required_capability: Optional[str],
    ) -> Optional[RoutingDecision]:
        """Looks up by the unique constraint columns."""
        role_key = required_role or ""
        cap_key = required_capability or ""
        row = self._conn.execute(
            """SELECT * FROM routing_decisions
               WHERE work_item_id = ? AND stage = ?
               AND required_role = ? AND required_capability = ?""",
            (work_item_id, stage, role_key, cap_key),
        ).fetchone()
        return self._row_to_decision(row) if row else None

    @staticmethod
    def _row_to_decision(row: tuple) -> RoutingDecision:
        """Converts a SQLite row to a RoutingDecision."""
        return RoutingDecision(
            decision_id=row[0],
            work_item_id=row[1],
            stage=row[2],
            required_role=row[3],
            required_capability=row[4],
            candidates=json.loads(row[5]),
            selected_agent_id=row[6],
            status=RoutingStatus(row[7]),
            reason=row[8],
            timestamp=datetime.fromisoformat(row[9]),
        )
