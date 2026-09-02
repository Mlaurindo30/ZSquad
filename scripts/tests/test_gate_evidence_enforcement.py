import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from gate_validators import validate_G4_code_security, validate_G5_quality
from tdd_evidence import evidence_digest


def _verification(item, verifier):
    path = item / "evaluation" / f"{verifier}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = item / "reviews" / "review.md" if (item / "reviews" / "review.md").is_file() else item / "validation" / "report.md"
    relative = source.relative_to(ROOT).as_posix() if ROOT in source.parents else source.relative_to(item).as_posix()
    path.write_text(json.dumps({
        "schema_version": 1,
        "work_item": item.name,
        "verifier": verifier,
        "persona": "test-engineer",
        "command": [sys.executable, "-m", "pytest"],
        "passed": True,
        "exit_code": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_hashes": {relative: hashlib.sha256(source.read_bytes()).hexdigest()},
        "results": (
            {
                "status": "PASS",
                "minimum_percent": 80,
                "total_percent": 90,
                "branch_percent": 85,
            }
            if verifier == "coverage"
            else {"status": "PASS"}
        ),
    }), encoding="utf-8")


def _tdd(item):
    directory = item / "evaluation" / "tdd"
    directory.mkdir(parents=True, exist_ok=True)
    red = {"passed": False, "exit_code": 1, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-1"}}
    green = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-1", "previous_digest": evidence_digest(red)}}
    refactor = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-1", "previous_digest": evidence_digest(green)}}
    for stage, value in (("red", red), ("green", green), ("refactor", refactor)):
        (directory / f"{stage}.json").write_text(json.dumps(value), encoding="utf-8")


def test_g4_rejects_text_only_claims(tmp_path):
    item = tmp_path / "TASK-GATE-001"
    (item / "reviews").mkdir(parents=True)
    (item / "findings").mkdir()
    (item / "reviews" / "claim.md").write_text("clean code tests pass security approved", encoding="utf-8")
    assert validate_G4_code_security(item)["approved"] is False


def test_g4_accepts_executable_evidence_and_tdd(tmp_path):
    item = tmp_path / "TASK-GATE-002"
    (item / "reviews").mkdir(parents=True)
    (item / "findings").mkdir()
    (item / "reviews" / "review.md").write_text("independent review", encoding="utf-8")
    for verifier in ("clean-code", "unit-tests", "security"):
        _verification(item, verifier)
    _tdd(item)
    assert validate_G4_code_security(item)["approved"] is True


def test_g5_requires_bdd_regression_and_coverage_evidence(tmp_path):
    item = tmp_path / "TASK-GATE-003"
    (item / "validation").mkdir(parents=True)
    (item / "validation" / "report.md").write_text("failure paths verified", encoding="utf-8")
    assert validate_G5_quality(item)["approved"] is False
    for verifier in ("bdd", "regression", "coverage"):
        _verification(item, verifier)
    assert validate_G5_quality(item)["approved"] is True


def test_g5_rejects_passing_command_below_branch_threshold(tmp_path):
    item = tmp_path / "TASK-GATE-004"
    (item / "validation").mkdir(parents=True)
    (item / "validation" / "report.md").write_text("failure paths verified", encoding="utf-8")
    for verifier in ("bdd", "regression", "coverage"):
        _verification(item, verifier)
    evidence_path = item / "evaluation" / "coverage.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["results"]["branch_percent"] = 79.99
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    assert validate_G5_quality(item)["approved"] is False
