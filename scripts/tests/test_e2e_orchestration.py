import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml
from agent_squad import AgentSquad, SquadError
from toon import dumps as toon_dumps, loads as toon_loads


class RealOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.squad = AgentSquad(ROOT)
        self.temp = tempfile.TemporaryDirectory()
        self.work_root = Path(self.temp.name) / "work"
        self.work_root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_full_orchestration_with_real_engines(self):
        item = self.squad.init_work_item("TASK-ORCH-001", "medium", base=self.work_root)

        (item / "implementation").mkdir(exist_ok=True)
        (item / "implementation" / "demo.py").write_text("pass\n", encoding="utf-8")

        memory = self.squad.record_memory(
            item, "software-engineer",
            "Repo compaction and AST chunking completed before implementation.",
            "implementation/demo.py", kind="fact"
        )
        memory_path = f"memory/deltas/{memory['id']}.yaml"

        engine_result = self.squad.run_integration_engine("gitingest", work_item=item.name)
        self.assertEqual(engine_result.get("status"), "ok")

        toon_payload = toon_dumps({"work_item": item.name, "engine": "gitingest"})
        self.assertIsInstance(toon_payload, str)
        parsed = toon_loads(toon_payload)
        self.assertEqual(parsed["work_item"], item.name)

        handoff = self.squad.create_handoff(
            item, "software-engineer", "code-reviewer",
            "Implementation compacted and chunked, ready for review.",
            ["implementation/demo.py"], ["implementation/demo.py"],
            memory_path, next_gate="G4-code-security"
        )
        self.squad.ack_handoff(item, handoff["id"], "code-reviewer")
        with self.assertRaisesRegex(SquadError, "verificação executável"):
            self.squad.decide_gate(
                item, "G4-code-security", "code-reviewer",
                [(c, "pass") for c in self.squad.get_gate("G4-code-security")["criteria"]],
                ["implementation/demo.py"]
            )
        errors = self.squad.validate_work_item(item)
        self.assertEqual(errors, [])

    def test_multi_agent_concurrent_work_items(self):
        items = []
        for i in range(5):
            item = self.squad.init_work_item(f"TASK-CONC-{i:03d}", "low", base=self.work_root)
            items.append(item)
        self.assertEqual(len(items), 5)
        for item in items:
            memory = self.squad.record_memory(
                item, "software-engineer",
                "Concurrent execution fact.", "status.yaml", kind="fact"
            )
            handoff = self.squad.create_handoff(
                item, "software-engineer", "qa-engineer",
                "Ready for concurrent validation.", ["status.yaml"], ["status.yaml"],
                f"memory/deltas/{memory['id']}.yaml", next_gate="G5-quality"
            )
            self.squad.ack_handoff(item, handoff["id"], "qa-engineer")
            with self.assertRaisesRegex(SquadError, "verificação executável"):
                self.squad.decide_gate(
                    item, "G5-quality", "qa-engineer",
                    [(c, "pass") for c in self.squad.get_gate("G5-quality")["criteria"]],
                    ["status.yaml"],
                    human_approved_by="business-stakeholder",
                    human_evidence="status.yaml"
                )
            errors = self.squad.validate_work_item(item)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
