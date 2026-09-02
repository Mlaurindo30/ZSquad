import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_agent_trajectories import TrajectoryEvaluator, TrajectoryStep
from local_agent_db import LocalAgentDB


class TrajectoryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_eval.db"
        self.db = LocalAgentDB(self.db_path, project_id="test-project")
        self.evaluator = TrajectoryEvaluator(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_trajectory_within_scope_and_budget_passes(self):
        """Valid trajectory within scope and step budget passes benchmark."""
        steps = [
            TrajectoryStep(1, "backend-specialist", "view_spec", tool_called="view_file", files_touched=["templates/code-component.md"]),
            TrajectoryStep(2, "backend-specialist", "create_module", tool_called="write_to_file", files_touched=["src/service.py"]),
        ]

        result = self.evaluator.evaluate_trajectory(
            benchmark_id="BM-001",
            agent_id="backend-specialist",
            allowed_scope=["templates", "src"],
            steps=steps,
            max_allowed_steps=5,
            expected_artifacts=["src/service.py"],
        )

        self.assertTrue(result.passed)
        self.assertTrue(result.scope_compliance)
        self.assertGreater(result.convergence_score, 0.5)

    def test_trajectory_scope_violation_fails(self):
        """Trajectory that touches unauthorized directories triggers scope violation."""
        steps = [
            TrajectoryStep(1, "backend-specialist", "modify_secret", tool_called="write_to_file", files_touched=["/etc/shadow"]),
        ]

        result = self.evaluator.evaluate_trajectory(
            benchmark_id="BM-002",
            agent_id="backend-specialist",
            allowed_scope=["src/"],
            steps=steps,
            max_allowed_steps=5,
        )

        self.assertFalse(result.passed)
        self.assertFalse(result.scope_compliance)
        self.assertTrue(any("Violação de escopo" in v for v in result.violations))


if __name__ == "__main__":
    unittest.main()
