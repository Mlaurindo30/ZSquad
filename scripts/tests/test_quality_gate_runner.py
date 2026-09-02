import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from quality_gate_runner import _coverage_results, hash_files, main, run_verifier, validate_evidence


def test_runner_persists_valid_passing_evidence(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    output = tmp_path / "evidence.json"
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-001",
        "unit-tests",
        "test-engineer",
        [sys.executable, "-c", "print('pass')"],
        ["source.py"],
        output,
        timeout=5,
    )
    assert evidence["passed"] is True
    assert evidence["exit_code"] == 0
    assert output.exists()
    validate_evidence(ROOT, json.loads(output.read_text(encoding="utf-8")))


def test_runner_fails_closed_for_missing_tool(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-002",
        "missing-tool",
        "test-engineer",
        ["tool-that-does-not-exist-xyz"],
        ["source.py"],
        tmp_path / "evidence.json",
        timeout=1,
    )
    assert evidence["passed"] is False
    assert evidence["exit_code"] == 127


def test_runner_enforces_total_and_branch_coverage(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "percent_covered": 95,
        "covered_branches": 79,
        "num_branches": 100,
    }}), encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-004",
        "coverage",
        "test-engineer",
        [sys.executable, "-c", "print('pass')"],
        ["source.py", "coverage.json"],
        tmp_path / "evidence.json",
        timeout=5,
        coverage_report="coverage.json",
        coverage_minimum=80,
    )
    assert evidence["passed"] is False
    assert evidence["results"]["total_percent"] == 95
    assert evidence["results"]["branch_percent"] == 79


def test_coverage_report_must_be_inside_root(tmp_path):
    with pytest.raises(ValueError, match="fora da raiz"):
        _coverage_results(tmp_path, "../coverage.json", 80)


def test_coverage_report_requires_measurable_branches(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "percent_covered": 100,
        "covered_branches": 0,
        "num_branches": 0,
    }}), encoding="utf-8")

    with pytest.raises(ValueError, match="sem branches mensuráveis"):
        _coverage_results(tmp_path, "coverage.json", 80)


def test_runner_accepts_coverage_at_threshold(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {
        "percent_covered": 80,
        "covered_branches": 8,
        "num_branches": 10,
    }}), encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-005",
        "coverage",
        "test-engineer",
        [sys.executable, "-c", "print('pass')"],
        ["source.py", "coverage.json"],
        tmp_path / "evidence.json",
        coverage_report="coverage.json",
    )
    assert evidence["passed"] is True
    assert evidence["results"]["status"] == "PASS"


def test_runner_fails_closed_on_invalid_coverage_report(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-006",
        "coverage",
        "test-engineer",
        [sys.executable, "-c", "print('pass')"],
        ["source.py"],
        tmp_path / "evidence.json",
        coverage_report="missing.json",
    )
    assert evidence["passed"] is False
    assert evidence["results"]["status"] == "FAIL"


def test_runner_fails_closed_on_timeout(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-003",
        "timeout-test",
        "test-engineer",
        [sys.executable, "-c", "import time; time.sleep(2)"],
        ["source.py"],
        tmp_path / "evidence.json",
        timeout=1,
    )
    assert evidence["passed"] is False
    assert evidence["exit_code"] == 124


def test_hash_files_rejects_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        hash_files(tmp_path, ["../outside.txt"])


def test_hash_files_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        hash_files(tmp_path, ["missing.py"])


def test_validate_evidence_rejects_invalid_payload(tmp_path):
    with pytest.raises(ValueError, match="evidência inválida"):
        validate_evidence(ROOT, {"schema_version": 1})


def test_runner_records_nonzero_exit_and_stderr(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    evidence = run_verifier(
        tmp_path,
        "TASK-QUALITY-004",
        "unit-tests",
        "test-engineer",
        [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); raise SystemExit(3)"],
        ["source.py"],
        tmp_path / "evidence.json",
        timeout=5,
    )
    assert evidence["passed"] is False
    assert evidence["exit_code"] == 3
    assert "boom" in evidence["stderr"]


def test_cli_executes_a_passing_verifier(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    exit_code = main([
        "--root", str(tmp_path),
        "--work-item", "TASK-QUALITY-CLI",
        "--verifier", "unit-tests",
        "--persona", "test-engineer",
        "--output", str(tmp_path / "evidence.json"),
        "--file", "source.py",
        sys.executable, "-c", "print('pass')",
    ])

    assert exit_code == 0
    assert (tmp_path / "evidence.json").exists()


def test_cli_requires_command(tmp_path):
    with pytest.raises(SystemExit) as error:
        main([
            "--root", str(tmp_path),
            "--work-item", "TASK-QUALITY-005",
            "--verifier", "unit-tests",
            "--persona", "test-engineer",
            "--output", str(tmp_path / "evidence.json"),
        ])
    assert error.value.code == 2
