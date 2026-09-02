import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from local_agent_db import LocalAgentDB


class LocalAgentDBTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_squad.db"
        self.db = LocalAgentDB(self.db_path, project_id="project-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ast_indexing_and_blast_radius(self):
        """Index a sample module and verify symbols and blast radius computation."""
        code_a = '''"""
O que é: Módulo de utilitários centrais.
Responsabilidade: Formatação e hashing.
Pra que serve: Apoiar componentes de infraestrutura.
Comportamento em falha: Retorna strings vazias.
Conexões: Consumido por auth e billing.
"""

def hash_string(value: str) -> str:
    """Calcula hash sha256."""
    return "hashed_" + value
'''
        code_b = '''
import utils

def login_user(username: str) -> bool:
    """Faz login usando hash de utils."""
    h = utils.hash_string(username)
    return bool(h)
'''
        file_a = Path(self.temp_dir.name) / "utils.py"
        file_b = Path(self.temp_dir.name) / "auth.py"
        file_a.write_text(code_a, encoding="utf-8")
        file_b.write_text(code_b, encoding="utf-8")

        self.db.index_python_file(file_a)
        self.db.index_python_file(file_b)

        blast = self.db.get_blast_radius(str(file_a))
        self.assertIn("auth.py", [Path(f).name for f in blast["dependent_files"]])
        self.assertTrue(any(s["name"] == "hash_string" for s in blast["symbols_at_risk"]))

    def test_token_tracking_summary(self):
        """Record tokens and verify aggregation by step and agent."""
        self.db.record_token_metrics("TASK-001", "delivery-orchestrator", "discovery", 500, 200, 0.005)
        self.db.record_token_metrics("TASK-001", "software-engineer", "implementation", 1500, 800, 0.020)

        summary = self.db.get_token_summary("TASK-001")
        self.assertEqual(summary["total_steps"], 2)
        self.assertEqual(summary["total_prompt_tokens"], 2000)
        self.assertEqual(summary["total_completion_tokens"], 1000)
        self.assertAlmostEqual(summary["total_cost_usd"], 0.025, places=3)
        self.assertEqual(len(summary["breakdown_by_agent"]), 2)

    def test_quorum_evaluation(self):
        """Verify weighted Byzantine voting threshold."""
        # 1. Cast votes for G4-code-security
        self.db.record_quorum_vote("TASK-001", "G4-code-security", "code-reviewer", "pass", weight=1.0)
        self.db.record_quorum_vote("TASK-001", "G4-code-security", "security-reviewer", "pass", weight=1.5)
        self.db.record_quorum_vote("TASK-001", "G4-code-security", "offensive-cyber-operator", "fail", weight=1.0)

        # Total weight: 3.5, Pass weight: 2.5 (ratio = 2.5 / 3.5 = 0.714)
        # Threshold: 0.67 -> Approved!
        res_approved = self.db.evaluate_quorum("TASK-001", "G4-code-security", threshold=0.67)
        self.assertTrue(res_approved["approved"])
        self.assertEqual(res_approved["status"], "approved")

        # Threshold: 0.80 -> Rejected!
        res_rejected = self.db.evaluate_quorum("TASK-001", "G4-code-security", threshold=0.80)
        self.assertFalse(res_rejected["approved"])
        self.assertEqual(res_rejected["status"], "rejected")

    def test_project_id_is_required_and_validated(self):
        with self.assertRaises(ValueError):
            LocalAgentDB(self.db_path)
        with self.assertRaises(ValueError):
            LocalAgentDB(self.db_path, project_id="../unsafe")

    def test_projects_are_isolated_in_one_database(self):
        other = LocalAgentDB(self.db_path, project_id="project-b")
        self.db.record_token_metrics("TASK-001", "agent-a", "plan", 10, 5)
        other.record_token_metrics("TASK-001", "agent-b", "build", 100, 50)
        self.db.record_quorum_vote("TASK-001", "G4", "reviewer", "pass")
        other.record_quorum_vote("TASK-001", "G4", "reviewer", "fail")
        self.db.log_trajectory("smoke", "agent-a", "TASK-001", "pass", 1, 1, 0.1)
        other.log_trajectory("smoke", "agent-b", "TASK-001", "fail", 2, 2, 0.2)
        shared_path = Path(self.temp_dir.name) / "shared.py"
        self.db.index_python_file(shared_path, "def only_in_a():\n    return 1\n")
        other.index_python_file(shared_path, "def only_in_b():\n    return 2\n")

        self.assertEqual(self.db.get_token_summary("TASK-001")["total_tokens"], 15)
        self.assertEqual(other.get_token_summary("TASK-001")["total_tokens"], 150)
        self.assertTrue(self.db.evaluate_quorum("TASK-001", "G4")["approved"])
        self.assertFalse(other.evaluate_quorum("TASK-001", "G4")["approved"])
        self.assertEqual(self.db.get_trajectory_history("TASK-001")[0]["agent_id"], "agent-a")
        self.assertEqual(other.get_trajectory_history("TASK-001")[0]["agent_id"], "agent-b")
        symbols_a = self.db.get_blast_radius(str(shared_path))["symbols_at_risk"]
        symbols_b = other.get_blast_radius(str(shared_path))["symbols_at_risk"]
        self.assertEqual([symbol["name"] for symbol in symbols_a], ["only_in_a"])
        self.assertEqual([symbol["name"] for symbol in symbols_b], ["only_in_b"])

    def test_legacy_schema_migrates_idempotently(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.execute("""
                CREATE TABLE token_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_item_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    cost_usd REAL DEFAULT 0.0,
                    recorded_at REAL NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO token_metrics VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                ("TASK-OLD", "old-agent", "old-step", 7, 3, 0.1, 1.0),
            )
            conn.commit()

        legacy = LocalAgentDB(legacy_path, allow_legacy=True)
        self.assertEqual(legacy.get_token_summary("TASK-OLD")["total_tokens"], 10)
        LocalAgentDB(legacy_path, allow_legacy=True)
        with closing(sqlite3.connect(legacy_path)) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(token_metrics)")]
            projects = conn.execute("SELECT DISTINCT project_id FROM token_metrics").fetchall()
        self.assertIn("project_id", columns)
        self.assertEqual(projects, [("legacy",)])


if __name__ == "__main__":
    unittest.main()
