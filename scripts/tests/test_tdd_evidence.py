import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from tdd_evidence import evidence_digest, validate_tdd_cycle


def _evidence(stage, passed, exit_code, hashes=None):
    return {
        "passed": passed,
        "exit_code": exit_code,
        "file_hashes": hashes or {"source.py": "a" * 64},
        "results": {"test_id": "test_feature", "criterion_id": "AC-1", "stage": stage},
    }


def _write_cycle(path):
    red = _evidence("red", False, 1)
    green = _evidence("green", True, 0)
    green["results"]["previous_digest"] = evidence_digest(red)
    refactor = _evidence("refactor", True, 0)
    refactor["results"]["previous_digest"] = evidence_digest(green)
    for stage, evidence in (("red", red), ("green", green), ("refactor", refactor)):
        (path / f"{stage}.json").write_text(json.dumps(evidence), encoding="utf-8")
    return red, green, refactor


def test_valid_tdd_cycle_is_approved(tmp_path):
    _write_cycle(tmp_path)
    assert validate_tdd_cycle(tmp_path, {"source.py": "a" * 64})["approved"] is True


def test_red_must_fail(tmp_path):
    red, green, refactor = _write_cycle(tmp_path)
    red["passed"], red["exit_code"] = True, 0
    (tmp_path / "red.json").write_text(json.dumps(red), encoding="utf-8")
    result = validate_tdd_cycle(tmp_path)
    assert result["approved"] is False
    assert "RED deve falhar" in result["errors"]


def test_chain_tampering_is_rejected(tmp_path):
    _write_cycle(tmp_path)
    green = json.loads((tmp_path / "green.json").read_text(encoding="utf-8"))
    green["results"]["previous_digest"] = "0" * 64
    (tmp_path / "green.json").write_text(json.dumps(green), encoding="utf-8")
    assert validate_tdd_cycle(tmp_path)["approved"] is False


def test_stale_refactor_is_rejected(tmp_path):
    _write_cycle(tmp_path)
    result = validate_tdd_cycle(tmp_path, {"source.py": "b" * 64})
    assert result["approved"] is False
    assert "evidência REFACTOR está desatualizada" in result["errors"]


def test_each_stage_contract_violation_is_reported(tmp_path):
    _write_cycle(tmp_path)
    green_path = tmp_path / "green.json"
    green = json.loads(green_path.read_text(encoding="utf-8"))
    green.update({"passed": False, "exit_code": 1})
    green["results"]["test_id"] = "test_other"
    green_path.write_text(json.dumps(green), encoding="utf-8")

    refactor_path = tmp_path / "refactor.json"
    refactor = json.loads(refactor_path.read_text(encoding="utf-8"))
    refactor.update({"passed": False, "exit_code": 1})
    refactor["results"]["criterion_id"] = "AC-OTHER"
    refactor_path.write_text(json.dumps(refactor), encoding="utf-8")

    result = validate_tdd_cycle(tmp_path)

    assert result["approved"] is False
    assert "GREEN deve passar" in result["errors"]
    assert "REFACTOR deve passar" in result["errors"]
    assert "test_id deve ser único e igual nos três estágios" in result["errors"]
    assert "criterion_id deve ser único e igual nos três estágios" in result["errors"]
