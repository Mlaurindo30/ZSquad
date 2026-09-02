import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_clean_distribution.py"
SPEC = importlib.util.spec_from_file_location("build_clean_distribution", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def put(root: Path, relative: str, content: bytes = b"x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def record(path: str, payload: bytes, destination: str) -> dict:
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "destination": destination,
        "classification": "archive" if destination == "archive" else "module",
        "treatment": "reused",
        "lifecycle": "historical" if destination == "archive" else "runtime",
        "origin": "test",
        "source_revision": "UNVERIFIED",
        "executable": False,
        "license": "UNVERIFIED",
        "sensitivity": "internal",
        "owner": "test",
        "reason": "test fixture",
    }


def write_manifest(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in records), encoding="utf-8")


def test_plan_blocks_runtime_forbidden_paths_and_reconciles(tmp_path: Path):
    root = tmp_path / "root"
    live = put(root, "agents/a.md", b"agent")
    old = put(root, "work/OLD/status.yaml", b"old")
    cache = put(root, ".pytest_cache/nodeids", b"cache")
    records = [record("agents/a.md", live.read_bytes(), "distribution"), record("work/OLD/status.yaml", old.read_bytes(), "archive"), record(".pytest_cache/nodeids", cache.read_bytes(), "excluded")]
    plan = MODULE.plan(root, records)
    assert plan.counts == {"distribution": 1, "archive": 1, "excluded": 1}
    assert [x["path"] for x in plan.distribution] == ["agents/a.md"]
    assert [x["path"] for x in plan.archive] == ["work/OLD/status.yaml"]

    # Excluded transient evidence may drift because it is provenance-only and
    # is never copied into either materialized output.
    cache.write_bytes(b"changed cache")
    MODULE.plan(root, records)

    bad = record("work/EPIC/file.md", b"bad", "distribution")
    put(root, bad["path"], b"bad")
    with pytest.raises(MODULE.PackageError, match="forbidden runtime path"):
        MODULE.plan(root, [bad])


def test_rejects_stale_hash_escape_and_nested_git(tmp_path: Path):
    root = tmp_path / "root"
    put(root, "agents/a.md", b"changed")
    stale = record("agents/a.md", b"old", "distribution")
    with pytest.raises(MODULE.PackageError, match="source hash mismatch"):
        MODULE.plan(root, [stale])

    for path in ("../escape", "/escape", "C:/escape"):
        bad = record(path, b"x", "distribution")
        with pytest.raises(MODULE.PackageError, match="relative POSIX"):
            MODULE.validate_record_path(bad["path"])

    nested = record("skills/vendor/.git/config", b"x", "distribution")
    put(root, nested["path"], b"x")
    with pytest.raises(MODULE.PackageError, match="forbidden runtime path"):
        MODULE.plan(root, [nested])


def test_materialize_preserves_source_recalculates_hashes_and_is_deterministic(tmp_path: Path):
    root = tmp_path / "root"
    payload = b"agent"
    history = b"history"
    put(root, "agents/a.md", payload)
    put(root, "work/OLD/status.yaml", history)
    records = [record("agents/a.md", payload, "distribution"), record("work/OLD/status.yaml", history, "archive")]
    before = {p: (root / p).read_bytes() for p in ("agents/a.md", "work/OLD/status.yaml")}
    plan = MODULE.plan(root, records)
    output = root / "distribution/materialized"
    archive = root / "archive/materialized"
    result1 = MODULE.materialize(root, plan, output, archive)
    assert (output / "agents/a.md").read_bytes() == payload
    assert (archive / "work/OLD/status.yaml").read_bytes() == history
    assert before == {p: (root / p).read_bytes() for p in before}
    archive_bytes_1 = MODULE.serialize_archive_manifest(plan.archive)
    MODULE.verify_materialized(plan, output, archive)
    archive_bytes_2 = MODULE.serialize_archive_manifest(plan.archive)
    assert archive_bytes_1 == archive_bytes_2
    assert result1["distribution"] == 1 and result1["archive"] == 1


def test_replaces_existing_outputs_and_rejects_symlink(tmp_path: Path):
    root = tmp_path / "root"
    put(root, "agents/a.md", b"x")
    plan = MODULE.plan(root, [record("agents/a.md", b"x", "distribution")])
    output = root / "distribution/materialized"
    output.mkdir(parents=True)
    (output / "stale.txt").write_text("stale", encoding="utf-8")
    archive = root / "archive/materialized"
    residual = root / "archive/.materialized.old.previous"
    residual.mkdir(parents=True)
    (residual / "stale.txt").write_text("stale", encoding="utf-8")
    result = MODULE.materialize(root, plan, output, archive)
    assert result["source_preserved"] is True
    assert not (output / "stale.txt").exists()
    MODULE.verify_materialized(plan, output, archive)
    assert not residual.exists()

    outside = tmp_path / "outside"
    outside.write_bytes(b"x")
    link = root / "agents/link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(MODULE.PackageError, match="symlink"):
        MODULE.plan(root, [record("agents/link.md", b"x", "distribution")])


def test_replacement_cleanup_removes_read_only_nested_archive_file(tmp_path: Path):
    root = tmp_path / "root"
    put(root, "agents/a.md", b"x")
    plan = MODULE.plan(root, [record("agents/a.md", b"x", "distribution")])
    output = root / "distribution/materialized"
    archive = root / "archive/materialized"
    stale = put(archive, ".git/objects/pack/stale.idx", b"stale")
    stale.chmod(stat.S_IREAD)

    result = MODULE.materialize(root, plan, output, archive)

    assert result["source_preserved"] is True
    assert not (archive / ".git/objects/pack/stale.idx").exists()
    assert not list(root.glob("**/.materialized.*"))
    MODULE.verify_materialized(plan, output, archive)


def test_plan_uses_verified_materialized_archive_when_original_is_absent(tmp_path: Path):
    root = tmp_path / "root"
    history = b"history"
    put(root, "archive/materialized/work/OLD/status.yaml", history)
    archive_record = record("work/OLD/status.yaml", history, "archive")

    package_plan = MODULE.plan(root, [archive_record])

    assert package_plan.archive == (archive_record,)
    assert MODULE.source_for_record(root, archive_record).read_bytes() == history


def test_current_archive_source_overrides_old_materialized_snapshot(tmp_path: Path):
    root = tmp_path / "root"
    put(root, "work/OLD/status.yaml", b"changed")
    put(root, "archive/materialized/work/OLD/status.yaml", b"history")
    archive_record = record("work/OLD/status.yaml", b"history", "archive")

    current_record = record("work/OLD/status.yaml", b"changed", "archive")
    package_plan = MODULE.plan(root, [current_record])

    assert package_plan.archive == (current_record,)
    assert MODULE.source_for_record(root, current_record).read_bytes() == b"changed"
