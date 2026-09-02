import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_file_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_file_manifest", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(root: Path, relative: str, content: str = "x") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_build_is_deterministic_and_reconciles_every_file(tmp_path: Path):
    root = tmp_path / "root"
    write(root, ".gitattributes", "AGENTS.md text eol=lf")
    write(root, "AGENTS.md", "rules")
    write(root, "THIRD_PARTY_NOTICES.md", "notices")
    write(root, "agents/00/PROMPT.md", "persona")
    write(root, "work/OLD/status.yaml", "state: done")
    write(root, "work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/evidence.md", "evidence")
    write(root, "work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/traceability/verification-log.md", "live")
    write(root, ".coverage", "coverage data")
    write(root, ".pytest_cache/nodeids", "[]")
    write(root, "scripts/__pycache__/x.pyc", "bytecode")
    write(root, "distribution/files.manifest.jsonl", "old self output")

    first = MODULE.build(root)
    second = MODULE.build(root)
    assert first == second
    assert len(first.records) == 10
    assert sum(first.counts.values()) == 10
    by_path = {record["path"]: record for record in first.records}
    assert by_path[".gitattributes"]["destination"] == "distribution"
    assert by_path["AGENTS.md"]["destination"] == "distribution"
    assert by_path["THIRD_PARTY_NOTICES.md"]["destination"] == "distribution"
    assert by_path["work/OLD/status.yaml"]["destination"] == "archive"
    assert by_path["work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/evidence.md"]["lifecycle"] == "release-evidence"
    assert by_path["work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/traceability/verification-log.md"]["destination"] == "excluded"
    assert by_path[".coverage"]["destination"] == "excluded"
    assert by_path[".pytest_cache/nodeids"]["destination"] == "excluded"
    assert by_path["scripts/__pycache__/x.pyc"]["destination"] == "excluded"
    assert "distribution/files.manifest.jsonl" not in by_path


def test_fail_closed_for_unclassified_top_level(tmp_path: Path):
    root = tmp_path / "root"
    write(root, "mystery/file.bin")
    with pytest.raises(MODULE.ManifestError, match="unclassified"):
        MODULE.build(root)


def test_fail_closed_for_absolute_or_traversal_record_path():
    for path in ("C:/escape.txt", "/escape.txt", "../escape.txt", "ok/../../escape"):
        with pytest.raises(MODULE.ManifestError, match="relative POSIX"):
            MODULE.validate_relative_path(path)


def test_fail_closed_for_symlink(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "README.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(MODULE.ManifestError, match="symlink"):
        MODULE.build(root)


def test_fail_closed_for_hash_mismatch_and_undeclared_executable(tmp_path: Path):
    root = tmp_path / "root"
    target = write(root, "scripts/tool.py", "print('x')")
    result = MODULE.build(root)
    target.write_text("print('changed')", encoding="utf-8")
    with pytest.raises(MODULE.ManifestError, match="hash mismatch"):
        MODULE.verify_record(root, result.records[0])

    command = write(root, "scripts/tool.sh", "echo x")
    policy = MODULE.classify("scripts/tool.sh")
    policy["executable"] = False
    with pytest.raises(MODULE.ManifestError, match="undeclared executable"):
        MODULE.record_for(root, command, "scripts/tool.sh", policy)


def test_serialized_outputs_are_byte_identical(tmp_path: Path):
    root = tmp_path / "root"
    write(root, "README.md", "hello")
    one = MODULE.build(root)
    snapshot_a, manifest_a = MODULE.serialize(one)
    two = MODULE.build(root)
    snapshot_b, manifest_b = MODULE.serialize(two)
    assert snapshot_a == snapshot_b
    assert manifest_a == manifest_b
    assert hashlib.sha256(manifest_a).hexdigest() == hashlib.sha256(manifest_b).hexdigest()
    assert json.loads(snapshot_a)["reconciliation"]["complete"] is True


def test_retains_verified_archive_record_when_original_is_absent(tmp_path: Path):
    root = tmp_path / "root"
    payload = b"historical"
    archived = root / "archive/materialized/work/OLD/status.yaml"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(payload)
    record = {
        "path": "work/OLD/status.yaml",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        **MODULE.classify("work/OLD/status.yaml"),
    }
    manifest = root / "archive/archive-manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = MODULE.build(root)

    assert {item["path"] for item in result.records} == {"work/OLD/status.yaml"}
    assert result.records[0] == record


def test_current_source_overrides_retained_archive_snapshot(tmp_path: Path):
    root = tmp_path / "root"
    put = write(root, "work/OLD/status.yaml", "changed")
    archived_payload = b"historical"
    archived = root / "archive/materialized/work/OLD/status.yaml"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(archived_payload)
    record = {
        "path": "work/OLD/status.yaml",
        "sha256": hashlib.sha256(archived_payload).hexdigest(),
        "size": len(archived_payload),
        **MODULE.classify("work/OLD/status.yaml"),
    }
    manifest = root / "archive/archive-manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert put.is_file()
    result = MODULE.build(root)
    current = next(item for item in result.records if item["path"] == "work/OLD/status.yaml")
    assert current["sha256"] == hashlib.sha256(b"changed").hexdigest()
    assert current["sha256"] != record["sha256"]


def test_clean_architecture_roots_and_canonical_provenance_are_distribution():
    for relative in (
        "domain/README.md",
        "application/README.md",
        "ports/README.md",
        "adapters/README.md",
        "bootstrap/README.md",
        "legacy-runtime/README.md",
        "PROVENANCE.yaml",
    ):
        assert MODULE.classify(relative)["destination"] == "distribution"
    with pytest.raises(MODULE.ManifestError, match="unclassified"):
        MODULE.classify("PROVENANCE-2.0.yaml")
