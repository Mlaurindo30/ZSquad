"""RED contract tests for TASK-P0-CONTRACT-INTEGRITY-20260901.

These tests intentionally describe the approved target contract. Production code
must make them green one story at a time; the test engineer does not implement the
runtime changes in this slice.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_squad import AgentSquad, SquadError  # noqa: E402


WORKFLOW_PATH = ROOT / "config" / "workflow.yaml"
REGISTRY_PATH = ROOT / "config" / "agent-registry.yaml"
HANDOFF_SCHEMA_PATH = ROOT / "contracts" / "handoff.schema.json"
VERIFY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "verify.yml"
FINAL_GATES = ("G4-code-security", "G5-quality", "G6-governance-release")
ALLOWED_STORY_POINTS = {1, 2, 3, 5, 8}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _handoff_v2() -> dict:
    return {
        "schema_version": 2,
        "id": "HANDOFF-TASK-P0-TEST-001",
        "work_item_id": "TASK-P0-TEST",
        "from": "software-engineer",
        "to": "test-engineer",
        "created_at": "2026-09-01T00:00:00Z",
        "status": "ready",
        "summary": "Implementation is ready for independent quality validation.",
        "artifacts": ["scripts/agent_squad.py"],
        "decisions": [],
        "open_questions": [],
        "risks": [],
        "evidence": ["python -m pytest scripts/tests/test_contract_integrity_p0.py"],
        "memory_delta": "memory/deltas/MEM-TASK-P0-TEST-001.yaml",
        "next_gate": "G5-quality",
        "sod_snapshot": {
            "risk": "high",
            "author": "software-engineer",
            "reviewer": "code-reviewer",
            "approver": "test-engineer",
            "executor": None,
        },
        "acceptance": {
            "criteria_checked": ["handoff-v2"],
            "recipient_ack_required": True,
        },
        "acknowledgement": {
            "status": "pending",
            "by": None,
            "at": None,
            "notes": None,
        },
    }


def test_us_001_story_point_contract_accepts_only_fibonacci_up_to_eight():
    schema = json.loads((ROOT / "contracts" / "work-item.schema.json").read_text(encoding="utf-8"))
    story_points = schema["properties"]["story_points"]

    assert set(story_points["enum"]) == ALLOWED_STORY_POINTS
    assert 13 not in story_points["enum"]


def test_us_001_work_item_creation_rejects_thirteen_before_writing(tmp_path):
    squad = AgentSquad(ROOT, project_name="p0-red")
    signature = inspect.signature(squad.init_work_item)

    assert "story_points" in signature.parameters
    with pytest.raises(SquadError, match="story_points"):
        squad.init_work_item("TASK-P0-SP-13", "low", base=tmp_path, story_points=13)
    assert not (tmp_path / "TASK-P0-SP-13").exists()


def test_us_002_workflow_declares_namespaced_root_and_relative_ledger():
    work_item = _yaml(WORKFLOW_PATH)["work_item"]

    assert work_item["root"] == "work/<project>/<WORK-ID>"
    assert work_item["ledger"] == "documentation/delivery-ledger.md"


@pytest.mark.parametrize("unsafe", ["../delivery-ledger.md", "C:/temp/delivery-ledger.md"])
def test_us_002_ledger_resolver_rejects_unsafe_paths(tmp_path, unsafe):
    squad = AgentSquad(ROOT, project_name="p0-red")
    item = tmp_path / "TASK-P0-LEDGER"
    item.mkdir()
    squad.workflow["work_item"]["ledger"] = unsafe

    with pytest.raises(SquadError, match="ledger"):
        squad._ledger_path(item)


def test_us_002_same_work_item_id_resolves_to_distinct_project_ledgers(tmp_path):
    first = AgentSquad(ROOT, project_name="alpha")
    second = AgentSquad(ROOT, project_name="beta")
    first_item = first.init_work_item("TASK-P0-SAME", "low", base=tmp_path / "alpha")
    second_item = second.init_work_item("TASK-P0-SAME", "low", base=tmp_path / "beta")

    first_ledger = first._ledger_path(first_item)
    second_ledger = second._ledger_path(second_item)
    assert first_ledger != second_ledger
    assert first_item.resolve() in first_ledger.resolve().parents
    assert second_item.resolve() in second_ledger.resolve().parents


def test_us_003_handoff_schema_requires_v2_and_sod_snapshot():
    schema = json.loads(HANDOFF_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    handoff = _handoff_v2()

    assert not list(validator.iter_errors(handoff))
    assert "schema_version" in schema["required"]
    assert schema["properties"]["schema_version"] == {"const": 2}
    assert "sod_snapshot" in schema["required"]


def test_us_003_unversioned_handoff_is_rejected():
    schema = json.loads(HANDOFF_SCHEMA_PATH.read_text(encoding="utf-8"))
    handoff = _handoff_v2()
    handoff.pop("schema_version")

    errors = list(Draft202012Validator(schema).iter_errors(handoff))
    assert any(error.validator == "required" and "schema_version" in error.message for error in errors)


def test_us_003_migration_command_is_exposed_for_safe_v1_upgrade():
    source = (ROOT / "scripts" / "agent_squad.py").read_text(encoding="utf-8")

    assert "migrate-handoffs" in source
    assert "--dry-run" in source


def test_us_004_final_gates_are_native_distinct_and_unaliased():
    workflow = _yaml(WORKFLOW_PATH)
    aliases = workflow.get("gate_aliases", {})

    assert all(gate in workflow["gates"] for gate in FINAL_GATES)
    assert all(gate not in aliases for gate in FINAL_GATES)
    assert len({workflow["gates"][gate]["owner"] for gate in FINAL_GATES}) == 3
    assert len({workflow["gate_state_mapping"][gate] for gate in FINAL_GATES}) == 3


def test_us_004_done_is_reachable_only_after_native_g6():
    workflow = _yaml(WORKFLOW_PATH)

    assert workflow["gate_state_mapping"]["G4-code-security"] == "quality-validation"
    assert workflow["gate_state_mapping"]["G5-quality"] == "governance-release"
    assert workflow["gate_state_mapping"]["G6-governance-release"] == "done"


@pytest.mark.parametrize("risk", ["medium", "high", "critical"])
def test_us_005_sod_rejects_author_as_reviewer_for_governed_risk(risk):
    squad = AgentSquad(ROOT, project_name="p0-red")

    with pytest.raises(SquadError, match="segregation|SoD|author|reviewer"):
        squad.validate_sod_snapshot(
            {
                "risk": risk,
                "author": "software-engineer",
                "reviewer": "software-engineer",
                "approver": "test-engineer",
                "executor": None,
            }
        )


def test_us_005_high_risk_requires_all_populated_roles_to_be_distinct():
    squad = AgentSquad(ROOT, project_name="p0-red")

    with pytest.raises(SquadError, match="segregation|SoD|approver|executor"):
        squad.validate_sod_snapshot(
            {
                "risk": "high",
                "author": "software-engineer",
                "reviewer": "code-reviewer",
                "approver": "governance-auditor",
                "executor": "governance-auditor",
            }
        )


def test_us_006_registry_has_exactly_one_non_dispatchable_provider_primary_host():
    agents = _yaml(REGISTRY_PATH)["agents"]
    hosts = [agent for agent in agents if agent.get("host_role") == "provider-primary"]

    assert len(hosts) == 1
    assert hosts[0]["id"] == "delivery-orchestrator"
    assert hosts[0]["mode"] == "host"
    assert hosts[0]["singleton"] is True
    assert hosts[0]["dispatchable"] is False


def test_us_006_runtime_refuses_to_compile_the_host_as_subagent(tmp_path):
    from render_agent_prompt import render_agent_prompt

    output = tmp_path / "host.txt"
    with pytest.raises(SquadError, match="host|dispatch"):
        render_agent_prompt("delivery-orchestrator", output_path=str(output))
    assert not output.exists()


def test_us_007_ci_runs_p0_affected_and_complete_governed_suites():
    text = VERIFY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "scripts/tests/test_contract_integrity_p0.py" in text
    assert "scripts/tests/test_work_cycles.py" in text
    assert "python -m pytest scripts/tests/" in text
    assert "--ignore=scripts/tests/test_work_cycles.py" not in text
