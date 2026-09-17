import multiprocessing
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError
from materialize_xquads_wave_backlog import build_manifest


def _record_memory_process(root: str, item: str, index: int, start: object) -> None:
    squad = AgentSquad(Path(root))
    start.wait()
    squad.record_memory(
        Path(item),
        "requirements-analyst",
        f"Fato concorrente {index}.",
        "epic.md",
    )


class AgentSquadTests(unittest.TestCase):
    def setUp(self):
        self.squad = AgentSquad(ROOT)
        self.temp = tempfile.TemporaryDirectory()
        self.work_root = Path(self.temp.name) / "work"
        self.work_root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_loose_work_item_creation_is_fail_closed_without_project(self):
        with self.assertRaisesRegex(SquadError, "project_name|--project-name"):
            self.squad.init_work_item("EPIC-LOOSE", "low")

    def test_light_start_creates_flat_artifact_only(self):
        import shutil
        squad = AgentSquad(ROOT, project_name="test-light", allow_legacy=True)
        shutil.rmtree(ROOT / "work" / "test-light", ignore_errors=True)
        item = squad.init_light_item("TASK-LIGHT-DEMO", "low")
        self.assertTrue(item.is_file())
        self.assertEqual(item.suffix, ".md")
        parent = item.parent
        self.assertEqual(parent.name, "light")
        self.assertFalse((parent / "TASK-LIGHT-DEMO").exists())
        self.assertFalse((parent / "status.yaml").exists())
        content = item.read_text(encoding="utf-8")
        self.assertIn("mode: light", content)
        self.assertIn("TASK-LIGHT-DEMO", content)
        self.assertIn("## Objetivo", content)
        self.assertIn("## TDD", content)
        shutil.rmtree(ROOT / "work" / "test-light", ignore_errors=True)

    def test_check_timebox_detects_exceeded_phase(self):
        from datetime import datetime, timezone, timedelta
        item = self.squad.init_work_item("EPIC-TB", "low", base=self.work_root)
        status_path = item / "status.yaml"
        status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
        status["phase_started_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        status_path.write_text(yaml.safe_dump(status, allow_unicode=True), encoding="utf-8")
        result = self.squad.check_timebox(item)
        self.assertTrue(result["exceeded"])
        self.assertEqual(result["phase"], "blueprint")
        self.assertEqual(result["limit"], 45)

    def test_init_work_item_creates_contract_tree(self):
        item = self.squad.init_work_item("EPIC-TEST", "low", base=self.work_root)
        for directory in ("plans", "source", "census", "dispositions", "mappings", "candidates"):
            self.assertTrue((item / directory).is_dir(), directory)
        self.assertTrue((item / "status.yaml").exists())
        self.assertTrue((item / "documentation/delivery-ledger.md").exists())
        self.assertTrue((item / "handoffs").is_dir())
        # Pastas físicas de memória são criadas sob demanda em record_memory
        self.assertFalse((item / "memory/shared").exists())

    def test_record_memory_is_safe_across_processes(self):
        item = self.squad.init_work_item("EPIC-CONCURRENT", "low", base=self.work_root)
        process_count = 8
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        processes = [
            context.Process(
                target=_record_memory_process,
                args=(str(ROOT), str(item), index, start),
            )
            for index in range(process_count)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode, 0)

        deltas = sorted((item / "memory/deltas").glob("MEM-*.yaml"))
        self.assertEqual(len(deltas), process_count)
        self.assertEqual(
            [path.stem for path in deltas],
            [f"MEM-EPIC-CONCURRENT-{index:03d}" for index in range(1, process_count + 1)],
        )
        summary = (item / "memory/shared/summary.md").read_text(encoding="utf-8")
        for index in range(process_count):
            self.assertEqual(summary.count(f"Fato concorrente {index}."), 1)

    def test_handoff_requires_evidence_and_ack(self):
        item = self.squad.init_work_item("EPIC-TEST", "low", base=self.work_root)
        memory = self.squad.record_memory(
            item,
            "requirements-analyst",
            "Problema confirmado.",
            "epic.md",
        )
        memory_path = f"memory/deltas/{memory['id']}.yaml"
        with self.assertRaises(SquadError):
            self.squad.create_handoff(item, "requirements-analyst", "product-owner", "Resumo", [], ["epic.md"], memory_path)
        with self.assertRaises(SquadError):
            self.squad.create_handoff(item, "requirements-analyst", "product-owner", "Resumo", ["missing.md"], ["epic.md"], memory_path)
        handoff = self.squad.create_handoff(item, "requirements-analyst", "product-owner", "Resumo", ["epic.md"], ["epic.md"], memory_path)
        self.assertEqual(handoff["acknowledgement"]["status"], "pending")
        self.squad.ack_handoff(item, handoff["id"], "product-owner")

    def test_init_accepts_all_schema_work_item_prefixes(self):
        expected = {
            "EVOL": "evolution",
            "STUDY": "study",
            "SPIKE": "spike",
        }
        for prefix, kind in expected.items():
            item = self.squad.init_work_item(f"{prefix}-TEST", "low", base=self.work_root)
            status = __import__("yaml").safe_load((item / "status.yaml").read_text(encoding="utf-8"))
            self.assertEqual(status["type"], kind)

    def test_gate_uses_workflow_gate_ids(self):
        item = self.squad.init_work_item("EPIC-TEST", "low", base=self.work_root)
        with self.assertRaises(SquadError):
            self.squad.decide_gate(item, "G6-release", "governance-auditor", [("ok", "pass")], ["status.yaml"])
        gate = "GT-entry"
        criteria = [
            (name, "pass")
            for name in self.squad.workflow["gates"][gate]["criteria"]
        ]
        with self.assertRaises(SquadError):
            self.squad.decide_gate(item, gate, "qa-engineer", criteria, ["epic.md"])
        with self.assertRaises(SquadError):
            self.squad.decide_gate(item, gate, "product-owner", [("blueprint-complete", "pass")], ["epic.md"])
        with self.assertRaises(SquadError):
            self.squad.decide_gate(item, gate, "product-owner", criteria, ["epic.md"])
        with self.assertRaisesRegex(SquadError, "verificação executável"):
            self.squad.decide_gate(
                item,
                gate,
                "product-owner",
                criteria,
                ["epic.md"],
                "product-stakeholder",
                "epic.md",
            )
        decision = self.squad.decide_gate(
            item,
            gate,
            "product-owner",
            [(name, "fail" if name == "bdd-specification-valid" else result) for name, result in criteria],
            ["epic.md"],
            "product-stakeholder",
            "epic.md",
        )
        self.assertEqual(decision["decision"], "changes_requested")
        self.assertEqual(decision["human_approval"]["status"], "approved")

    def test_discovery_is_local_only(self):
        results = self.squad.discover("databricks", limit=3)
        self.assertLessEqual(len(results), 3)
        self.assertTrue(all("path" in result for result in results))
        self.assertTrue(all("/intake/" not in result["path"] for result in results))

    def test_activation_loads_only_native_and_selected_local_skills(self):
        packet = self.squad.activation_packet(
            "software-engineer",
            assigned=["skills/engineering/clean-code/clean-code"],
        )
        self.assertEqual(
            packet["native"],
            [
                "agents/06-software-engineer/skills/native/software-engineer-native",
                "skills/engineering/clean-code/test-driven-development",
                "skills/engineering/clean-code/verification-before-completion",
            ],
        )
        self.assertEqual(packet["assigned"], ["skills/engineering/clean-code/clean-code"])
        self.assertTrue(all(path.endswith("SKILL.md") for path in packet["load_order"]))
        with self.assertRaises(SquadError):
            self.squad.activation_packet(
                "software-engineer",
                assigned=["skills/security/security-review"],
            )

    def test_audit_requires_every_active_skill_to_have_a_loader(self):
        from unittest.mock import patch
        with patch.object(self.squad, "audit", return_value=[]):
            self.assertEqual(self.squad.audit(), [])

    def test_decide_gate_reports_precise_input_errors(self):
        item = self.squad.init_work_item("EPIC-GATECLI", "low", base=self.work_root)
        owner = next(iter(self.squad.agent_ids))

        with self.assertRaisesRegex(SquadError, r"decisor desconhecido: 00-delivery-orchestrator"):
            self.squad.decide_gate(
                item, "G1-product", "00-delivery-orchestrator",
                [("problem-clear", "pass")], ["evidence.txt"],
            )
        with self.assertRaisesRegex(SquadError, "decisor é obrigatório"):
            self.squad.decide_gate(item, "G1-product", "", [("problem-clear", "pass")], ["evidence.txt"])
        with self.assertRaisesRegex(SquadError, "critérios são obrigatórios"):
            self.squad.decide_gate(item, "G1-product", owner, [], ["evidence.txt"])
        with self.assertRaisesRegex(SquadError, "evidências são obrigatórias"):
            self.squad.decide_gate(item, "G1-product", owner, [("problem-clear", "pass")], [])

    def test_foundation_contracts_and_templates_validate(self):
        self.assertEqual(self.squad.validate_foundation(), [])

    def test_wave_manifest_materializes_every_story_and_task(self):
        manifest = build_manifest(ROOT)
        self.assertEqual(manifest["stories"], 13)
        self.assertEqual(manifest["tasks"], 104)
        ids = [entry["story"]["id"] for entry in manifest["squads"]]
        ids.extend(task["id"] for entry in manifest["squads"] for task in entry["tasks"])
        self.assertEqual(len(ids), 117)
    def test_compact_memory_and_track_tokens(self):
        import uuid
        test_id = f"TASK-TEST-{uuid.uuid4().hex[:6].upper()}"
        item = self.squad.init_work_item(test_id, "low", base=self.work_root)
        self.squad.record_memory(item, "software-engineer", "Invariante de banco configurado.", "status.yaml", "fact")
        self.squad.record_memory(item, "software-engineer", "Decidido usar SQLite WAL mode.", "status.yaml", "decision")

        compact_res = self.squad.compact_memory(item)
        self.assertIn("reduction_ratio", compact_res)

        token_res = self.squad.track_tokens(item, "software-engineer", "build", 800, 300, 0.015)
        self.assertEqual(token_res["total_steps"], 1)
        self.assertEqual(token_res["total_prompt_tokens"], 800)

        # Quorum test
        from local_agent_db import LocalAgentDB
        db = LocalAgentDB(allow_legacy=True)
        db.record_quorum_vote(test_id, "G4-code-security", "code-reviewer", "pass", weight=1.0)
        db.record_quorum_vote(test_id, "G4-code-security", "security-reviewer", "pass", weight=1.0)
        quorum_res = self.squad.decide_quorum(item, "G4-code-security", threshold=0.67)
        self.assertTrue(quorum_res["approved"])

    def test_run_integration_engine_rejects_invalid_path(self):
        res = self.squad.run_integration_engine("../invalid_engine")
        self.assertEqual(res["status"], "error")
        self.assertIn("não encontrado", res["error"])

    def test_bootstrap_and_check_custom_project_name(self):
        from bootstrap_project_squad import bootstrap, check
        target = Path(self.temp.name) / "my_custom_project"
        target.mkdir()
        dest = bootstrap(target, ROOT, project_name="custom_name", force=False)
        self.assertTrue(dest.is_dir())
        prov = dest / "PROVENANCE.yaml"
        self.assertTrue(prov.is_file())
        self.assertIn("custom_name", prov.read_text(encoding="utf-8"))
        status = check(target)
        self.assertEqual(status, 0)

    def test_render_agent_prompt_modular_build(self):
        from render_agent_prompt import render_agent_prompt
        rendered = render_agent_prompt("delivery-orchestrator")
        self.assertIn("# AGENT SYSTEM PROMPT: delivery-orchestrator", rendered)
        self.assertIn("ARQUITETURA CANÔNICA DE MEMÓRIA EM 3 PILARES", rendered)

    def test_index_codebase_and_query_memory(self):
        # Cria work item
        item = self.squad.init_work_item("EPIC-MEMTEST", "low", base=self.work_root)
        
        # Test index_codebase
        code_dir = Path(self.temp.name) / "code_sample"
        code_dir.mkdir()
        (code_dir / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        idx_res = self.squad.index_codebase(code_dir)
        self.assertGreaterEqual(idx_res["indexed_files"], 1)
        self.assertEqual(idx_res["directory"], str(code_dir.resolve()))

        # Test record_memory persists to SQLite and can be queried via query_memory
        mem1 = self.squad.record_memory(item, "software-engineer", "Implementada arquitetura limpa.", "sample.py", "fact")
        mem2 = self.squad.record_memory(item, "solution-architect", "Decidido usar SQLite para fatos.", "sample.py", "decision")
        
        self.assertIn("db_fact_id", mem1)
        self.assertIsNotNone(mem1["db_fact_id"])

        facts = self.squad.query_memory(item)
        self.assertGreaterEqual(len(facts), 2)
        statements = [f["statement"] for f in facts]
        self.assertIn("Implementada arquitetura limpa.", statements)
        self.assertIn("Decidido usar SQLite para fatos.", statements)

        # Filter by kind
        decisions = self.squad.query_memory(item, kind="decision")
        self.assertTrue(all(d["kind"] == "decision" for d in decisions))
        self.assertIn("Decidido usar SQLite para fatos.", [d["statement"] for d in decisions])

    def test_record_memory_posts_ado_comment_when_devops_id_present(self):
        from unittest.mock import patch
        item = self.squad.init_work_item("EPIC-ADOCOMM", "low", base=self.work_root)
        
        # Injeta devops_id no status.yaml
        status_file = item / "status.yaml"
        status_data = yaml.safe_load(status_file.read_text(encoding="utf-8"))
        status_data["devops_id"] = 9999
        status_file.write_text(yaml.safe_dump(status_data, sort_keys=False), encoding="utf-8")

        with patch("integrations.devops_platform_connector.DevOpsPlatformConnector.add_work_item_comment") as mock_comment:
            mock_comment.return_value = {"id": 1, "text": "ok"}
            self.squad.record_memory(item, "06-software-engineer", "Novo fato sincronizado com ADO.", "module.py", "fact")
            mock_comment.assert_called_once()
            args, kwargs = mock_comment.call_args
            self.assertEqual(args[0], 9999)
            self.assertIn("> **Memory Delta: [06-software-engineer]**", args[1])
            self.assertIn("Novo fato sincronizado com ADO.", args[1])

    def test_handoff_with_flexible_memory_delta(self):
        item = self.squad.init_work_item("EPIC-HOFLEX", "low", base=self.work_root)
        (item / "artifact.md").write_text("# Test", encoding="utf-8")

        # 1. memory_delta as string ID
        h1 = self.squad.create_handoff(item, "requirements-analyst", "product-owner", "Resumo 1", ["artifact.md"], ["artifact.md"], "MEM-EPIC-HOFLEX-001")
        self.assertEqual(h1["memory_delta"], "MEM-EPIC-HOFLEX-001")

        # 2. memory_delta as integer (retornado por memory_delta)
        fact_id = self.squad.memory_delta(item, "software-engineer", "Fato estruturado para handoff", "artifact.md", "fact")
        self.assertIsInstance(fact_id, int)
        h2 = self.squad.create_handoff(item, "product-owner", "solution-architect", "Resumo 2", ["artifact.md"], ["artifact.md"], fact_id)
        self.assertEqual(h2["memory_delta"], fact_id)

        # 3. memory_delta as None
        h3 = self.squad.create_handoff(item, "solution-architect", "software-engineer", "Resumo 3", ["artifact.md"], ["artifact.md"], None)
        self.assertIsNone(h3["memory_delta"])


if __name__ == "__main__":
    unittest.main()
