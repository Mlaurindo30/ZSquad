import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import SquadError, _build_parser
from bootstrap_project_squad import bootstrap, check
from local_agent_db import LocalAgentDB
from render_agent_prompt import _resolve_project_name


class ReviewRegressionTests(unittest.TestCase):
    def test_project_resolution_distinguishes_legacy_and_multi_project_paths(self):
        self.assertIsNone(_resolve_project_name("work/TASK-ONE", None))
        self.assertEqual(_resolve_project_name("work/acme/TASK-ONE", None), "acme")
        self.assertEqual(_resolve_project_name("TASK-ONE", "explicit"), "explicit")
        with self.assertRaises(SquadError):
            _resolve_project_name("work/acme/TASK-ONE", "other")

    def test_parser_build_is_side_effect_free_and_preserves_commands(self):
        parser = _build_parser()
        args = parser.parse_args(["discover", "--query", "security"])
        self.assertEqual(args.command, "discover")
        self.assertEqual(args.query, "security")

    def test_bootstrap_rejects_unsafe_project_name(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            target.mkdir()
            with self.assertRaises(ValueError):
                bootstrap(target, ROOT, project_name="../outside")

    def test_check_rejects_invalid_provenance_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "project"
            destination = target / ".agents_squad"
            destination.mkdir(parents=True)
            (destination / "PROVENANCE.yaml").write_text("invalid: [", encoding="utf-8")
            self.assertEqual(check(target), 1)

    def test_trajectory_history_filters_and_limits_recent_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            db = LocalAgentDB(Path(temp) / "squad.db", project_id="test-project")
            for index, agent in enumerate(("alpha", "beta", "alpha")):
                db.log_trajectory("bench", agent, "TASK-ONE", "pass", index, 0, 0.1)
                time.sleep(0.002)
            all_rows = db.get_trajectory_history("TASK-ONE")
            self.assertEqual(len(all_rows), 3)
            alpha_rows = db.get_trajectory_history("TASK-ONE", agent_id="alpha")
            self.assertEqual(len(alpha_rows), 2)
            recent = db.get_trajectory_history("TASK-ONE", limit=2)
            self.assertEqual([row["agent_id"] for row in recent], ["beta", "alpha"])
            for invalid in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    db.get_trajectory_history("TASK-ONE", limit=invalid)


if __name__ == "__main__":
    unittest.main()
