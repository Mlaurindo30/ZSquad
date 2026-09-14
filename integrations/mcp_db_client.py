import sqlite3
import time
import os
from typing import List, Dict, Any


class DBClient:
    def __init__(self, db_path: str):
        # Resolve to absolute path relative to project root or use provided
        if not os.path.isabs(db_path):
            self.db_path = os.path.join(os.path.dirname(__file__), "..", db_path)
        else:
            self.db_path = db_path

        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_facts (
                id TEXT PRIMARY KEY, project_id TEXT, work_item_id TEXT, author TEXT, kind TEXT, statement TEXT, source TEXT, confidence REAL, sensitivity TEXT, invalidates_when TEXT, recorded_at REAL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS quorum_votes (
                id TEXT PRIMARY KEY, project_id TEXT, work_item_id TEXT, gate_id TEXT, voter_agent TEXT, vote TEXT, weight REAL, rationale TEXT, voted_at REAL
            )""")
            conn.commit()

    def _execute(self, query: str, params: tuple = ()) -> List[tuple]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.fetchall()

    def get_context(
        self, project_id: str, work_item: str, topics: List[str]
    ) -> List[Dict[str, Any]]:
        if not topics:
            return []
        placeholders = ",".join("?" * len(topics))
        query = f"SELECT id, kind, statement, source FROM memory_facts WHERE project_id=? AND work_item_id=? AND kind IN ({placeholders})"
        rows = self._execute(query, (project_id, work_item, *topics))
        return [
            {"id": r[0], "kind": r[1], "statement": r[2], "source": r[3]} for r in rows
        ]

    def record_fact(
        self,
        project_id: str,
        work_item: str,
        author: str,
        kind: str,
        statement: str,
        source: str,
    ) -> str:
        query = "INSERT INTO memory_facts (project_id, work_item_id, author, kind, statement, source, confidence, sensitivity, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    project_id,
                    work_item,
                    author,
                    kind,
                    statement,
                    source,
                    1.0,
                    "normal",
                    time.time(),
                ),
            )
            conn.commit()
            fact_id = str(cursor.lastrowid)
        return fact_id

    def get_quorum_votes(
        self, project_id: str, work_item: str, gate_id: str
    ) -> List[Dict[str, Any]]:
        query = "SELECT id, voter_agent, vote, weight, rationale FROM quorum_votes WHERE project_id=? AND work_item_id=? AND gate_id=?"
        rows = self._execute(query, (project_id, work_item, gate_id))
        return [
            {
                "id": r[0],
                "voter_agent": r[1],
                "vote": r[2],
                "weight": r[3],
                "rationale": r[4],
            }
            for r in rows
        ]

    def get_impact_analysis(self, paths: List[str]) -> List[str]:
        return [f"Impact for {p}" for p in paths]
