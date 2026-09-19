"""Activation Repository — SQLite persistence for Activation Packets.

Ensures idempotent storage, full auditability, and deterministic retrieval.
Strictly stdlib-only.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union

from scripts.domain.delegation import ActivationPacket, AncestorSnapshot, WorkContext
from scripts.domain.work_items import AcceptanceCriterion, WorkItemKind
from .errors import ActivationPersistenceError


DDL_ACTIVATION_PACKETS = """
CREATE TABLE IF NOT EXISTS activation_packets (
    activation_id       TEXT    PRIMARY KEY,
    assignment_id       TEXT    NOT NULL,
    agent_id            TEXT    NOT NULL,
    role_name           TEXT    NOT NULL,
    work_item_id        TEXT    NOT NULL,
    project_id          TEXT    NOT NULL,
    stage               TEXT    NOT NULL,
    context_fingerprint TEXT    NOT NULL,
    work_context_json   TEXT    NOT NULL,
    skill_manifest_json TEXT    NOT NULL,
    compiled_instruction TEXT   NOT NULL,
    instruction_hash    TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    UNIQUE(assignment_id, context_fingerprint, instruction_hash)
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_activation_work_item ON activation_packets(work_item_id);
CREATE INDEX IF NOT EXISTS idx_activation_assignment ON activation_packets(assignment_id);
"""


class ActivationRepository:
    """SQLite repository for persisting and retrieving specialist ActivationPackets."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(DDL_ACTIVATION_PACKETS)
            self._conn.executescript(CREATE_INDEXES)

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def _deserialize_work_context(self, raw_json: str) -> WorkContext:
        data = json.loads(raw_json)
        ancestors = []
        for anc in data.get("ancestors", []):
            kind_val = anc.get("kind")
            kind = WorkItemKind(kind_val) if kind_val else WorkItemKind.TASK
            ancestors.append(
                AncestorSnapshot(
                    work_item_id=anc["work_item_id"],
                    kind=kind,
                    title=anc["title"],
                    stage=anc["stage"],
                    spec_summary=anc.get("spec_summary", ""),
                )
            )

        criteria = []
        for ac in data.get("acceptance_criteria", []):
            criteria.append(
                AcceptanceCriterion(
                    id=ac["id"],
                    scenario=ac["scenario"],
                    given=ac["given"],
                    when=ac["when"],
                    then=ac["then"],
                    is_verified=bool(ac.get("is_verified", False)),
                )
            )

        return WorkContext(
            work_item_id=data["work_item_id"],
            project_id=data["project_id"],
            current_stage=data["current_stage"],
            title=data["title"],
            description=data.get("description", ""),
            definition_of_done=data.get("definition_of_done", []),
            acceptance_criteria=criteria,
            ancestors=ancestors,
            ancestor_artifacts=data.get("ancestor_artifacts", {}),
            active_receipts=data.get("active_receipts", []),
            filesystem_scope=data.get("filesystem_scope", []),
        )

    def _row_to_packet(self, row: sqlite3.Row) -> ActivationPacket:
        work_ctx = self._deserialize_work_context(row["work_context_json"])
        skill_manifest = json.loads(row["skill_manifest_json"])
        created_at = datetime.fromisoformat(row["created_at"])

        return ActivationPacket(
            session_id=row["activation_id"],
            agent_id=row["agent_id"],
            role_name=row["role_name"],
            work_item_id=row["work_item_id"],
            work_context=work_ctx,
            skill_manifest=skill_manifest,
            compiled_instruction=row["compiled_instruction"],
            instruction_hash=row["instruction_hash"],
            created_at=created_at,
        )

    def save(self, packet: ActivationPacket, assignment_id: str, context_fingerprint: str) -> None:
        """Saves an ActivationPacket idempotently."""
        sql = """
        INSERT INTO activation_packets (
            activation_id, assignment_id, agent_id, role_name,
            work_item_id, project_id, stage, context_fingerprint,
            work_context_json, skill_manifest_json, compiled_instruction,
            instruction_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(assignment_id, context_fingerprint, instruction_hash) DO UPDATE SET
            activation_id = excluded.activation_id,
            compiled_instruction = excluded.compiled_instruction,
            created_at = excluded.created_at
        """
        try:
            with self._conn:
                self._conn.execute(
                    sql,
                    (
                        packet.session_id,
                        assignment_id,
                        packet.agent_id,
                        packet.role_name,
                        packet.work_item_id,
                        packet.work_context.project_id,
                        packet.work_context.current_stage,
                        context_fingerprint,
                        packet.work_context.to_json(),
                        json.dumps(packet.skill_manifest, sort_keys=True),
                        packet.compiled_instruction,
                        packet.instruction_hash,
                        packet.created_at.isoformat(),
                    ),
                )
        except sqlite3.Error as err:
            raise ActivationPersistenceError(f"Failed to persist activation packet: {err}") from err

    def get_by_id(self, activation_id: str) -> Optional[ActivationPacket]:
        """Retrieves an activation packet by its ID."""
        sql = "SELECT * FROM activation_packets WHERE activation_id = ?"
        cur = self._conn.execute(sql, (activation_id,))
        row = cur.fetchone()
        return self._row_to_packet(row) if row else None

    def find_existing(
        self,
        assignment_id: str,
        context_fingerprint: str,
        instruction_hash: str,
    ) -> Optional[ActivationPacket]:
        """Finds existing packet matching the exact assignment and context fingerprint."""
        sql = """
        SELECT * FROM activation_packets
        WHERE assignment_id = ? AND context_fingerprint = ? AND instruction_hash = ?
        """
        cur = self._conn.execute(sql, (assignment_id, context_fingerprint, instruction_hash))
        row = cur.fetchone()
        return self._row_to_packet(row) if row else None

    def list_for_work_item(self, work_item_id: str) -> List[ActivationPacket]:
        """Lists all activation packets recorded for a work item."""
        sql = "SELECT * FROM activation_packets WHERE work_item_id = ? ORDER BY created_at DESC"
        cur = self._conn.execute(sql, (work_item_id,))
        return [self._row_to_packet(row) for row in cur.fetchall()]
