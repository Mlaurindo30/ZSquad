import json
import sys
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
FEATURE = ROOT / "work" / "agent_squad" / "TASK-CODE-REVIEW-20260820" / "specs" / "features" / "quality-gates.feature"

from gate_validators import validate_G4_code_security
from tdd_evidence import evidence_digest, validate_tdd_cycle

scenarios(str(FEATURE))


@given("a work item without executable evidence", target_fixture="context")
def work_item_without_evidence(tmp_path):
    item = tmp_path / "TASK-BDD-001"
    (item / "reviews").mkdir(parents=True)
    (item / "findings").mkdir()
    (item / "reviews" / "claim.md").write_text("tests pass clean code security approved", encoding="utf-8")
    return {"item": item}


@given("linked Red Green and Refactor evidence", target_fixture="context")
def linked_tdd_evidence(tmp_path):
    red = {"passed": False, "exit_code": 1, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-QUALITY-2"}}
    green = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-QUALITY-2", "previous_digest": evidence_digest(red)}}
    refactor = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-QUALITY-2", "previous_digest": evidence_digest(green)}}
    for stage, value in (("red", red), ("green", green), ("refactor", refactor)):
        (tmp_path / f"{stage}.json").write_text(json.dumps(value), encoding="utf-8")
    return {"directory": tmp_path}


@when("gate G4 is evaluated")
def evaluate_g4(context):
    context["result"] = validate_G4_code_security(context["item"])


@when("the TDD evidence is validated")
def evaluate_tdd(context):
    context["result"] = validate_tdd_cycle(context["directory"])


@then("the gate is rejected")
def gate_rejected(context):
    assert context["result"]["approved"] is False


@then("the TDD cycle is approved")
def tdd_approved(context):
    assert context["result"]["approved"] is True
