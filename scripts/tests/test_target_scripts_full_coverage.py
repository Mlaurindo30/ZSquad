"""Branch-complete tests for the four distribution/prompt/backlog scripts."""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import build_clean_distribution as clean
import build_file_manifest as manifest
import materialize_xquads_wave_backlog as backlog
import render_agent_prompt as prompt
import sync_mcp_servers as sync_mcp


def put(root: Path, relative: str, data: bytes = b"x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def clean_record(path: str, data: bytes = b"x", destination: str = "distribution") -> dict:
    return {
        "path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        "destination": destination, "classification": "module", "treatment": "reused",
        "lifecycle": "runtime", "origin": "test", "source_revision": "UNVERIFIED",
        "executable": False, "license": "UNVERIFIED", "sensitivity": "internal",
        "owner": "test", "reason": "test",
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in records) + "\n", encoding="utf-8")


@pytest.mark.parametrize("bad", ["", "a\\b", "/a", "C:a", "a/../b"])
def test_clean_path_and_manifest_validation(tmp_path: Path, bad: str):
    with pytest.raises(clean.PackageError, match="relative POSIX"):
        clean.validate_record_path(bad)


def test_clean_load_manifest_every_outcome(tmp_path: Path):
    valid = clean_record("agents/a.md")
    path = tmp_path / "m.jsonl"
    path.write_text("\n" + json.dumps(valid) + "\n", encoding="utf-8")
    assert clean.load_manifest(path) == [valid]

    cases = [
        ("{", "invalid manifest JSON"),
        (json.dumps({"path": "x"}), "fields mismatch"),
        ("\n".join((json.dumps(valid), json.dumps(valid))), "duplicate"),
        (json.dumps({**valid, "destination": "bad"}), "invalid destination"),
        (json.dumps({**valid, "sha256": "bad"}), "invalid sha256"),
    ]
    for text, error in cases:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(clean.PackageError, match=error):
            clean.load_manifest(path)


def test_clean_source_resolution_and_plan_defenses(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    put(root, "agents/a.md")
    assert clean._source(root, "agents/a.md").is_file()
    with pytest.raises(clean.PackageError, match="source missing"):
        clean._source(root, "agents/missing.md")

    outside = put(tmp_path, "outside", b"z")
    link = root / "agents/link"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink unavailable")
    with pytest.raises(clean.PackageError, match="symlink source"):
        clean._source(root, "agents/link")

    fake = SimpleNamespace(is_symlink=lambda: False, is_file=lambda: True,
                           resolve=lambda strict=True: outside.resolve())
    monkeypatch.setattr(clean, "Path", lambda *parts: Path(*parts))
    monkeypatch.setattr(clean, "_source", clean._source)
    # Exercise the escape guard with a path-like stand-in through direct resolve patching.
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: outside if self.name == "escape" else real_resolve(self, strict=strict))
    put(root, "escape")
    with pytest.raises(clean.PackageError, match="escapes root"):
        clean._source(root, "escape")
    monkeypatch.setattr(Path, "resolve", real_resolve)

    archived = put(root, "archive/materialized/work/old", b"old")
    rec = clean_record("work/old", b"old", "archive")
    assert clean.source_for_record(root, rec) == archived
    with pytest.raises(clean.PackageError, match="retained archive hash mismatch"):
        clean.source_for_record(root, {**rec, "size": 99})

    duplicate = [clean_record("agents/a.md"), clean_record("agents/a.md")]
    with pytest.raises(clean.PackageError, match="duplicate"):
        clean.plan(root, duplicate)
    with pytest.raises(clean.PackageError, match="invalid destination"):
        clean.plan(root, [{**clean_record("agents/a.md"), "destination": "bad"}])

    class BadBuckets(dict):
        def values(self):
            return [()]
    monkeypatch.setattr(clean, "dict", lambda *a, **k: BadBuckets(*a, **k))
    with pytest.raises(clean.PackageError, match="incomplete manifest reconciliation"):
        clean.plan(root, [])


def test_clean_output_copy_verify_and_helpers(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    expected = root / "distribution/materialized"
    assert clean._ensure_governed_output(root, expected, "distribution/materialized") == expected
    expected.mkdir(parents=True)
    with pytest.raises(clean.PackageError, match="already exists"):
        clean._ensure_governed_output(root, expected, "distribution/materialized")
    with pytest.raises(clean.PackageError, match="must be"):
        clean._ensure_governed_output(root, root / "wrong", "distribution/materialized")
    with pytest.raises(clean.PackageError, match="escapes root"):
        clean._ensure_governed_output(root, tmp_path / "outside", "distribution/materialized")

    source = put(root, "agents/a", b"a")
    rec = clean_record("agents/a", b"a")
    stage = root / "stage"; stage.mkdir()
    real_copy = clean.shutil.copy2
    monkeypatch.setattr(clean.shutil, "copy2", lambda src, dst, follow_symlinks=False: Path(dst).write_bytes(b"bad"))
    with pytest.raises(clean.PackageError, match="destination hash mismatch"):
        clean._copy_bucket(root, (rec,), stage)
    monkeypatch.setattr(clean.shutil, "copy2", real_copy)

    archive = root / "archive"; put(archive, "nested/file", b"x")
    clean._make_archive_read_only(archive)
    assert not ((archive / "nested/file").stat().st_mode & stat.S_IWUSR)
    called = []
    clean._writable_and_retry(lambda p: called.append(p), archive / "nested/file", None)
    assert called

    tree = root / "tree"; tree.mkdir()
    real_rmtree = clean.shutil.rmtree
    calls = []
    def legacy(path, **kwargs):
        calls.append(kwargs)
        if "onexc" in kwargs:
            raise TypeError
        real_rmtree(path)
    monkeypatch.setattr(clean.shutil, "rmtree", legacy)
    clean._remove_tree(tree)
    assert len(calls) == 2

    out = root / "out"; arc = root / "arc"; out.mkdir(); arc.mkdir()
    plan = clean.PackagePlan((rec,), (), (), {"distribution": 1, "archive": 0, "excluded": 0})
    with pytest.raises(clean.PackageError, match="reconciliation mismatch"):
        clean.verify_materialized(plan, out, arc)
    put(out, "agents/a", b"bad")
    with pytest.raises(clean.PackageError, match="hash mismatch"):
        clean.verify_materialized(plan, out, arc)


def test_clean_remaining_branches(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir(); put(root, "agents/a", b"a")
    outside = put(tmp_path, "outside-clean", b"x")
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: outside if self.name == "escape" else real_resolve(self, strict=strict))
    put(root, "escape")
    with pytest.raises(clean.PackageError, match="escapes root"):
        clean._source(root, "escape")
    monkeypatch.setattr(Path, "resolve", real_resolve)

    with pytest.raises(clean.PackageError, match="duplicate"):
        clean.plan(root, [clean_record("agents/a", b"a"), clean_record("agents/a", b"a")])
    with pytest.raises(clean.PackageError, match="invalid destination"):
        clean.plan(root, [{**clean_record("agents/a", b"a"), "destination": "bad"}])
    real_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "a" or real_symlink(self))
    with pytest.raises(clean.PackageError, match="symlink source"):
        clean._source(root, "agents/a")
    monkeypatch.setattr(Path, "is_symlink", real_symlink)

    archived = put(root, "archive/materialized/work/a", b"x")
    rec = clean_record("work/a", b"y", "archive")
    with pytest.raises(clean.PackageError, match="retained archive hash mismatch"):
        clean.source_for_record(root, rec)
    archived.unlink()
    with pytest.raises(clean.PackageError, match="source missing"):
        clean.source_for_record(root, rec)

    parent = root / "distribution"; parent.mkdir()
    (parent / ".materialized.keep").mkdir()
    clean._cleanup_residuals(parent)
    assert (parent / ".materialized.keep").exists()

    # Cleanup both staging trees when copying fails before commit.
    package_plan = clean.plan(root, [clean_record("agents/a", b"a")])
    monkeypatch.setattr(clean, "_copy_bucket", lambda *a: (_ for _ in ()).throw(OSError("copy")))
    with pytest.raises(OSError, match="copy"):
        clean.materialize(root, package_plan, root / "distribution/materialized", root / "archive/materialized")
    assert not list((root / "distribution").glob("*.staging"))
    assert not list((root / "archive").glob("*.staging"))


def test_clean_exact_defensive_gaps(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    put(root, "agents/a", b"a")

    retained = put(root, "archive/materialized/work/a", b"x")
    retained_record = clean_record("work/a", b"x", "archive")
    assert clean.source_for_record(root, retained_record) == retained

    with pytest.raises(clean.PackageError, match="source hash mismatch"):
        clean.plan(root, [clean_record("agents/a", b"wrong")])
    with pytest.raises(clean.PackageError, match="forbidden runtime path"):
        clean.plan(root, [clean_record("work/a", b"x", "distribution")])

    put(root, "agents/b", b"b")
    dist = clean_record("agents/a", b"a")
    arc = clean_record("agents/b", b"b", "archive")
    package_plan = clean.plan(root, [dist, arc])
    out = root / "out"; archive = root / "arc"
    put(out, "agents/a", b"a"); put(archive, "agents/b", b"b")
    clean.verify_materialized(package_plan, out, archive)

    put(out, "extra", b"x")
    with pytest.raises(clean.PackageError, match="reconciliation mismatch"):
        clean.verify_materialized(package_plan, out, archive)


def test_clean_reconciliation_and_empty_rollback_branches(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir(); put(root, "agents/a", b"a")
    excluded = clean_record("volatile", destination="excluded")
    assert clean.plan(root, [excluded]).excluded == (excluded,)

    monkeypatch.setattr(clean, "sum", lambda values: 1, raising=False)
    with pytest.raises(clean.PackageError, match="incomplete manifest reconciliation"):
        clean.plan(root, [])
    monkeypatch.delattr(clean, "sum")

    package_plan = clean.plan(root, [clean_record("agents/a", b"a")])
    output = root / "distribution/materialized"
    archive = root / "archive/materialized"
    output.parent.mkdir(); archive.parent.mkdir()
    real_replace = clean.os.replace

    def fail_first_commit(src, dst):
        if Path(src).name.endswith(".staging"):
            raise OSError("first commit failed")
        return real_replace(src, dst)

    monkeypatch.setattr(clean.os, "replace", fail_first_commit)
    with pytest.raises(OSError, match="first commit failed"):
        clean.materialize(root, package_plan, output, archive)
    assert not output.exists() and not archive.exists()


def test_clean_success_with_each_previous_tree_independently(tmp_path: Path):
    for previous in ("distribution", "archive"):
        root = tmp_path / previous; root.mkdir(); put(root, "agents/a", b"a")
        package_plan = clean.plan(root, [clean_record("agents/a", b"a")])
        output = root / "distribution/materialized"
        archive = root / "archive/materialized"
        prior = output if previous == "distribution" else archive
        put(prior, "old", b"old")
        clean.materialize(root, package_plan, output, archive)
        assert (output / "agents/a").is_file()


def test_clean_materialize_failures_and_cli(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "root"; root.mkdir(); put(root, "agents/a", b"a")
    rec = clean_record("agents/a", b"a")
    package_plan = clean.plan(root, [rec])
    output = root / "distribution/materialized"; archive = root / "archive/materialized"

    monkeypatch.setattr(clean.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    (root / "distribution/.materialized.collision.staging").mkdir(parents=True)
    with pytest.raises(clean.PackageError, match="collision"):
        clean.materialize(root, package_plan, output, archive)
    clean._remove_tree(root / "distribution/.materialized.collision.staging")

    real_replace = clean.os.replace
    counter = {"n": 0}
    def fail_second(src, dst):
        counter["n"] += 1
        if counter["n"] == 2:
            real_replace(src, dst)
            raise OSError("commit failed")
        return real_replace(src, dst)
    output.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(clean.os, "replace", fail_second)
    with pytest.raises(OSError, match="commit failed"):
        clean.materialize(root, package_plan, output, archive)
    monkeypatch.setattr(clean.os, "replace", real_replace)
    if output.exists(): clean._remove_tree(output)
    if archive.exists(): clean._remove_tree(archive)

    # With both old trees present, a failed archive commit removes partial trees
    # and restores both previous versions.
    monkeypatch.setattr(clean.uuid, "uuid4", lambda: SimpleNamespace(hex="rollback"))
    output.mkdir(parents=True, exist_ok=True); archive.mkdir(parents=True, exist_ok=True)
    put(output, "old", b"o"); put(archive, "old", b"o")
    counter["n"] = 0
    def fail_archive_commit(src, dst):
        counter["n"] += 1
        if counter["n"] == 4:
            real_replace(src, dst)
            raise OSError("archive commit failed")
        return real_replace(src, dst)
    monkeypatch.setattr(clean.os, "replace", fail_archive_commit)
    with pytest.raises(OSError, match="archive commit failed"):
        clean.materialize(root, package_plan, output, archive)
    assert (output / "old").is_file() and (archive / "old").is_file()
    monkeypatch.setattr(clean.os, "replace", real_replace)

    manifest_path = root / "input.jsonl"; write_jsonl(manifest_path, [rec])
    common = ["--root", str(root), "--manifest", str(manifest_path), "--output", str(output),
              "--archive-output", str(archive), "--archive-manifest", str(root / "archive/archive-manifest.jsonl")]
    assert clean.main(common + ["--dry-run"]) == 0
    clean.materialize(root, package_plan, output, archive)
    assert clean.main(common + ["--verify"]) == 0
    assert clean.main(common) == 0
    with pytest.raises(clean.PackageError, match="archive manifest must"):
        clean.main(common[:-1] + [str(root / "wrong.jsonl")])
    assert "PACKAGE_OK" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["build-clean", *common, "--verify"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(clean.__file__)), run_name="__main__")
    assert exc.value.code == 0


@pytest.mark.parametrize("relative,classification", [
    ("audits/a", "archive"), ("agents/a", "module"), ("config/a", "core"),
    ("contracts/a", "core"), ("domain/a", "core"), ("application/a", "core"),
    ("ports/a", "core"), ("scripts/a", "tooling"), (".github/a", "tooling"),
    ("distribution/a", "tooling"), ("archive/a", "tooling"), ("bootstrap/a", "tooling"),
    ("banco/a", "tooling"), ("integrations/a", "tooling"), ("adapters/a", "adapter"),
    ("legacy-runtime/a", "adapter"), ("skills/a", "module"), ("templates/a", "adapter"),
    ("tasks/a", "adapter"), ("workflows/a", "adapter"), ("docs/a", "doc"),
    ("rendered_prompts/a", "doc"),
    ("scripts/tests/a.py", "test"), ("scripts/agent_squad.py", "tooling"),
])
def test_manifest_classification_matrix(relative: str, classification: str):
    assert manifest.classify(relative)["classification"] == classification


def test_manifest_policy_paths_and_record_validation(tmp_path: Path, monkeypatch):
    assert manifest.classify(".temp/a")["destination"] == "excluded"
    assert manifest.classify("x.pyc")["destination"] == "excluded"
    assert manifest.classify(manifest.VOLATILE_EVIDENCE_PREFIXES[0] + "a")["destination"] == "excluded"
    assert manifest.classify(manifest.CURRENT_EPIC + "a.sh")["executable"] is True
    assert manifest.classify("work/OLD/a.sh")["destination"] == "archive"
    assert manifest.classify("PROVENANCE.yaml")["treatment"] == "new"
    assert manifest.classify("scripts/build_file_manifest.py")["treatment"] == "new"

    base = {"path": "README.md", "sha256": "a" * 64, "size": 1,
            **manifest.classify("README.md")}
    variants = [
        ({k: v for k, v in base.items() if k != "owner"}, "fields mismatch"),
        ({**base, "sha256": "x"}, "invalid sha256"), ({**base, "size": -1}, "invalid size"),
        ({**base, "classification": "bad"}, "invalid classification"),
        ({**base, "treatment": "bad"}, "invalid treatment"),
        ({**base, "destination": "bad"}, "invalid destination"),
        ({**base, "owner": ""}, "missing owner"),
    ]
    for value, error in variants:
        with pytest.raises(manifest.ManifestError, match=error):
            manifest.validate_record(value)

    root = tmp_path / "root"; root.mkdir(); target = put(root, "README.md", b"x")
    record = manifest.record_for(root, target, "README.md", manifest.classify("README.md"))
    manifest.verify_record(root, record)
    target.unlink()
    with pytest.raises(manifest.ManifestError, match="file missing"):
        manifest.verify_record(root, record)

    outside = put(tmp_path, "outside", b"x")
    with pytest.raises(manifest.ManifestError, match="escapes root"):
        manifest.record_for(root, outside, "README.md", manifest.classify("README.md"))


def test_manifest_exact_validation_and_integrity_gaps(tmp_path: Path, monkeypatch):
    for bad in ("", "a\\b", "/a", "C:a", "a/../b"):
        with pytest.raises(manifest.ManifestError, match="relative POSIX"):
            manifest.validate_relative_path(bad)

    with pytest.raises(manifest.ManifestError, match="unclassified"):
        manifest.classify("unknown/a")

    root = tmp_path / "root"; root.mkdir()
    executable = put(root, "agents/tool.txt", b"x")
    real_mode_executable = manifest._is_mode_executable
    monkeypatch.setattr(manifest, "_is_mode_executable", lambda path: True)
    with pytest.raises(manifest.ManifestError, match="undeclared executable"):
        manifest.record_for(root, executable, "agents/tool.txt", manifest.classify("agents/tool.txt"))
    monkeypatch.setattr(manifest, "_is_mode_executable", real_mode_executable)

    base = {"path": "README.md", "sha256": "a" * 64, "size": 1,
            **manifest.classify("README.md")}
    for key in ("origin", "source_revision", "license", "reason"):
        with pytest.raises(manifest.ManifestError, match=f"missing {key}"):
            manifest.validate_record({**base, key: ""})

    target = put(root, "README.md", b"x")
    record = manifest.record_for(root, target, "README.md", manifest.classify("README.md"))
    target.write_bytes(b"y")
    with pytest.raises(manifest.ManifestError, match="hash mismatch"):
        manifest.verify_record(root, record)


def test_manifest_unreachable_policy_and_reconciliation_guards(tmp_path: Path, monkeypatch):
    class PermissiveTop:
        def __contains__(self, value):
            return True

    monkeypatch.setattr(manifest, "DISTRIBUTION_TOP", PermissiveTop())
    with pytest.raises(manifest.ManifestError, match="unclassified"):
        manifest.classify("unknown/a")

    root = tmp_path / "root"; root.mkdir()
    archived = clean_record("work/a", b"x", "archive")
    monkeypatch.setattr(manifest, "load_retained_archive", lambda unused: {"work/a": archived})
    monkeypatch.setattr(manifest.os, "walk", lambda *args, **kwargs: [])

    real_len = len
    monkeypatch.setattr(manifest, "len", lambda value: 0 if isinstance(value, list) else real_len(value), raising=False)
    with pytest.raises(manifest.ManifestError, match="incomplete reconciliation"):
        manifest.build(root)
    monkeypatch.delattr(manifest, "len")

    monkeypatch.setattr(manifest, "load_retained_archive", lambda unused: {})
    monkeypatch.setattr(manifest, "sum", lambda values: 1, raising=False)
    with pytest.raises(manifest.ManifestError, match="incomplete reconciliation"):
        manifest.build(root)


def test_manifest_build_merges_retained_archive_paths(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    real_walk = manifest.os.walk
    retained_only = clean_record("work/old", b"x", "archive")
    retained_second = clean_record("work/older", b"y", "archive")
    monkeypatch.setattr(manifest, "load_retained_archive", lambda unused: {
        "work/old": retained_only, "work/older": retained_second,
    })
    monkeypatch.setattr(manifest.os, "walk", lambda *args, **kwargs: [])
    result = manifest.build(root)
    assert result.records == (retained_only, retained_second)

    live = put(root, "work/old", b"x")
    monkeypatch.setattr(manifest.os, "walk", real_walk)
    policy = manifest.classify("work/old")
    retained_live = manifest.record_for(root, live, "work/old", policy)
    monkeypatch.setattr(manifest, "load_retained_archive", lambda unused: {
        "work/older": retained_second, "work/old": retained_live,
    })
    result = manifest.build(root)
    assert [record["path"] for record in result.records].count("work/old") == 1
    assert any(record["path"] == "work/older" for record in result.records)


def test_manifest_retained_archive_errors(tmp_path: Path):
    root = tmp_path / "root"; root.mkdir()
    assert manifest.load_retained_archive(root) == {}
    path = root / "archive/archive-manifest.jsonl"; path.parent.mkdir()
    valid = {"path": "work/OLD/a", "sha256": hashlib.sha256(b"x").hexdigest(), "size": 1,
             **manifest.classify("work/OLD/a")}
    cases = [
        ("\n{", "invalid archive manifest"),
        (json.dumps({**valid, "destination": "distribution"}), "non-archive"),
        (json.dumps(valid), "retained archive missing"),
    ]
    for text, error in cases:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(manifest.ManifestError, match=error):
            manifest.load_retained_archive(root)
    archived = put(root, "archive/materialized/work/OLD/a", b"bad")
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(manifest.ManifestError, match="hash mismatch"):
        manifest.load_retained_archive(root)
    archived.write_bytes(b"x")
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert manifest.load_retained_archive(root) == {valid["path"]: valid}
    path.write_text(json.dumps(valid) + "\n" + json.dumps(valid), encoding="utf-8")
    with pytest.raises(manifest.ManifestError, match="duplicate"):
        manifest.load_retained_archive(root)


def test_manifest_symlink_record_and_verify(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"; root.mkdir(); target = put(root, "README.md", b"x")
    policy = manifest.classify("README.md")
    real_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "README.md" or real_symlink(self))
    with pytest.raises(manifest.ManifestError, match="symlink"):
        manifest.record_for(root, target, "README.md", policy)
    record = {"path": "README.md", "sha256": hashlib.sha256(b"x").hexdigest(), "size": 1, **policy}
    with pytest.raises(manifest.ManifestError, match="symlink"):
        manifest.verify_record(root, record)


def test_manifest_build_defenses_output_and_cli(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "root"; root.mkdir(); put(root, "README.md", b"x")
    with pytest.raises(manifest.ManifestError, match="not a directory"):
        manifest.build(root / "README.md")
    result = manifest.build(root)
    outside = tmp_path / "out"
    with pytest.raises(manifest.ManifestError, match="escapes root"):
        manifest._output_under_root(root, outside)
    assert manifest._output_under_root(root, root / "distribution/a") == root / "distribution/a"

    args = ["--root", str(root), "--snapshot", str(root / "distribution/source-snapshot.json"),
            "--manifest", str(root / "distribution/files.manifest.jsonl")]
    assert manifest.main(args) == 0
    assert "MANIFEST_OK" in capsys.readouterr().out
    with pytest.raises(manifest.ManifestError, match="governed"):
        manifest.main(["--root", str(root), "--snapshot", str(root / "bad"), "--manifest", args[-1]])
    monkeypatch.setattr(sys, "argv", ["build-manifest", *args])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(manifest.__file__)), run_name="__main__")
    assert exc.value.code == 0

    # Synthetic walk outcomes cover directory symlinks and duplicate names.
    real_walk = manifest.os.walk
    real_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link" or real_symlink(self))
    monkeypatch.setattr(manifest.os, "walk", lambda *a, **k: [(str(root), ["link"], [])])
    with pytest.raises(manifest.ManifestError, match="symlink"):
        manifest.build(root)
    monkeypatch.setattr(Path, "is_symlink", real_symlink)
    monkeypatch.setattr(manifest.os, "walk", lambda *a, **k: [(str(root), [], ["README.md", "README.md"])])
    with pytest.raises(manifest.ManifestError, match="duplicate path"):
        manifest.build(root)
    monkeypatch.setattr(manifest.os, "walk", real_walk)

    # A scanned path disappearing after record creation triggers the build guard.
    monkeypatch.setattr(Path, "is_file", lambda self: False if self.name == "README.md" else Path.exists(self))
    monkeypatch.setattr(manifest, "record_for", lambda *a: {"path": "README.md", "sha256": "a" * 64, "size": 1, **manifest.classify("README.md")})
    with pytest.raises(manifest.ManifestError, match="file missing"):
        manifest.build(root)


def fake_squad(root: Path, packet: dict | None = None, *, activation_errors: list[Exception] | None = None):
    class FakeSquad:
        def __init__(self, root=None, project_name=None):
            self.root = Path(root)
            self.project_name = project_name
            self.skills_catalog = {"catalog": []}
            self.agents = {"agent": {"manifest": "agent.yaml"}}
            self.dispatchable_agent_ids = ["agent", "software-engineer", "code-reviewer"]
            self.calls = 0
        def _work_base(self):
            return self.root / "work"
        def activation_packet(self, *args, **kwargs):
            self.calls += 1
            if activation_errors and self.calls <= len(activation_errors):
                error = activation_errors[self.calls - 1]
                if error:
                    raise error
            return packet or {"prompt": "PROMPT.md", "load_order": []}
    return FakeSquad


def test_prompt_extract_sections_and_engines(tmp_path: Path, monkeypatch):
    assert prompt._extract_work_item_text(tmp_path / "absent") == ""
    item = tmp_path / "item"; item.mkdir()
    (item / "status.yaml").write_text("title: T\ndescription: D", encoding="utf-8")
    (item / "epic.md").write_text("E" * 3000, encoding="utf-8")
    assert prompt._extract_work_item_text(item).startswith("T D ")
    (item / "status.yaml").write_text("- not-a-mapping", encoding="utf-8")
    assert prompt._extract_work_item_text(item).startswith("E")
    (item / "status.yaml").write_text("title: ''\ndescription: ''", encoding="utf-8")
    assert prompt._extract_work_item_text(item).startswith("E")
    real_read_text = Path.read_text
    real_safe_load = prompt.yaml.safe_load
    monkeypatch.setattr(prompt.yaml, "safe_load", lambda x: (_ for _ in ()).throw(yaml.YAMLError()))
    monkeypatch.setattr(Path, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError()))
    assert prompt._extract_work_item_text(item) == ""
    monkeypatch.setattr(Path, "read_text", real_read_text)
    monkeypatch.setattr(prompt.yaml, "safe_load", real_safe_load)

    squad = SimpleNamespace(root=tmp_path, _work_base=lambda: tmp_path / "work", skills_catalog={"catalog": []}, agents={})
    with pytest.raises(prompt.SquadError, match="prompt ausente"):
        prompt._build_prompt_section("a", {"prompt": "missing"}, squad)
    assert prompt._build_work_item_context({}, squad) is None
    assert prompt._build_work_item_context({"work_item": "x"}, squad) is None
    assert prompt._build_engines_section("a", squad) is None
    bare_packet = {"load_order": ["skills/a/SKILL.md"]}
    put(tmp_path, "skills/a/SKILL.md", b"skill")
    skills = prompt._build_skills_section(bare_packet, squad)
    assert "SKILL" in skills and "METADADOS" not in skills
    put(tmp_path, "agents/a/manifest.yaml", b"assigned:\n  - path: eng/a\n  - eng/b\n")
    squad.agents = {"a": {"manifest": "agents/a/manifest.yaml"}}
    squad.skills_catalog = {"catalog": [{"domain": "other"}, {"domain": "integration-engines", "path": "eng/a", "description": "line1\nline2"}, {"domain": "integration-engines", "path": "eng/b"}]}
    engines = prompt._build_engines_section("a", squad)
    assert "line1" in engines and "`b` | b" in engines


def test_prompt_render_all_paths(tmp_path: Path, monkeypatch):
    root = tmp_path
    put(root, "PROMPT.md", b"persona")
    put(root, "skills/a/SKILL.md", b"skill")
    put(root, "skills/a/agents/openai.yaml", b"meta: yes")
    put(root, "work/W/status.yaml", b"title: task")
    packet = {"prompt": "PROMPT.md", "load_order": ["skills/a/SKILL.md"], "work_item": "W"}
    Fake = fake_squad(root, packet)
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    monkeypatch.setattr(prompt, "__file__", str(root / "scripts/render_agent_prompt.py"))
    output = root / "out/prompt.txt"
    monkeypatch.setattr(prompt, "_build_engines_section", lambda agent, squad: "\n---\nENGINES")
    rendered = prompt.render_agent_prompt("agent", work_item="W", discovered=[], output_path=str(output), auto_select_skills=True)
    assert "environment_details" in rendered and "METADADOS" in rendered and "STATUS" in rendered
    assert "ENGINES" in rendered
    assert output.read_text(encoding="utf-8") == rendered

    assert prompt._resolve_project_name(None, None) is None
    assert prompt._resolve_project_name("work/p/W", None) == "p"
    with pytest.raises(prompt.SquadError, match="conflitante"):
        prompt._resolve_project_name("work/p/W", "other")


def test_prompt_auto_selection_and_retry_branches(tmp_path: Path, monkeypatch):
    root = tmp_path; put(root, "PROMPT.md", b"p"); put(root, "work/W/status.yaml", b"title: task")
    put(root, "agent.yaml", yaml.safe_dump({"native": ["a"]}).encode())
    packet = {"prompt": "PROMPT.md", "load_order": []}
    monkeypatch.setattr(prompt, "__file__", str(root / "scripts/render_agent_prompt.py"))

    selector = SimpleNamespace(suggest_and_load=lambda **kw: [SimpleNamespace(loaded=True, ranked=SimpleNamespace(skill=SimpleNamespace(path="s1"))), SimpleNamespace(loaded=False)])
    monkeypatch.setitem(sys.modules, "scripts.skill_selector", selector)
    Fake = fake_squad(root, packet, activation_errors=[prompt.SquadError("budget"), None])
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    assert "AGENT SYSTEM" in prompt.render_agent_prompt("agent", work_item=str(root / "work/W"), auto_select_skills=True)

    selector.suggest_and_load = lambda **kw: (_ for _ in ()).throw(ValueError("bad"))
    Fake = fake_squad(root, packet)
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    assert "AGENT SYSTEM" in prompt.render_agent_prompt("agent", work_item="work/W", auto_select_skills=True)

    Fake = fake_squad(root, packet, activation_errors=[prompt.SquadError("first"), None])
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    monkeypatch.setattr(prompt, "read_yaml", lambda p: {"native": []})
    assert "AGENT SYSTEM" in prompt.render_agent_prompt("agent", discovered=list("abcdefgh"), auto_select_skills=True)

    Fake = fake_squad(root, packet, activation_errors=[prompt.SquadError("first"), None])
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    assert "AGENT SYSTEM" in prompt.render_agent_prompt("agent", discovered=["a"], auto_select_skills=True)

    Fake = fake_squad(root, packet, activation_errors=[prompt.SquadError("first"), None])
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    monkeypatch.setattr(prompt, "read_yaml", lambda p: (_ for _ in ()).throw(KeyError("bad")))
    assert "AGENT SYSTEM" in prompt.render_agent_prompt("agent", discovered=["a"], auto_select_skills=True)

    Fake = fake_squad(root, packet, activation_errors=[prompt.SquadError("fatal")])
    monkeypatch.setattr(prompt, "AgentSquad", Fake)
    with pytest.raises(prompt.SquadError):
        prompt.render_agent_prompt("agent")


def test_prompt_main_success_and_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["render", "--agent", "a"])
    monkeypatch.setattr(prompt, "render_agent_prompt", lambda **kw: "TEXT")
    prompt.main(); assert "TEXT" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["render", "--agent", "a", "--output", "x"])
    prompt.main(); assert "PROMPT_RENDERED_OK" in capsys.readouterr().out
    monkeypatch.setattr(prompt, "render_agent_prompt", lambda **kw: (_ for _ in ()).throw(prompt.SquadError("bad")))
    with pytest.raises(SystemExit) as exc:
        prompt.main()
    assert exc.value.code == 1

    monkeypatch.setattr(sys, "argv", ["render", "--agent", "missing-agent"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(prompt.__file__)), run_name="__main__")
    assert exc.value.code == 1


def test_sync_mcp_build_save_and_validation(tmp_path: Path):
    manager = sync_mcp.MCPSyncManager(tmp_path)
    config = sync_mcp.build_default_mcp_config(Path(r"C:\squad"))
    # squad-local-db foi removido: local_agent_db.py é biblioteca, não servidor MCP.
    assert set(config["mcpServers"]) == {"azure-devops", "codebase-memory", "sinapse-hivemind"}
    assert config["mcpServers"]["sinapse-hivemind"]["args"] == [
        "D:/Hive-Mind/scripts/services/sinapse-mcp.py"
    ]
    assert config["mcpServers"]["codebase-memory"]["command"] == (
        "C:/squad/integrations/vendor/codebase-memory-mcp/build/c/codebase-memory-mcp.exe"
    )
    assert config["mcpServers"]["codebase-memory"]["args"] == []
    assert "env" not in config["mcpServers"]["codebase-memory"]

    default_output = manager.generate_and_save()
    assert default_output == tmp_path / "config/mcp_config.json"
    assert manager.validate_config(default_output) is True
    custom_output = manager.generate_and_save(tmp_path / "custom.json")
    assert custom_output.is_file()

    assert sync_mcp.MCPSyncManager().root == sync_mcp.ROOT
    assert manager.validate_config(tmp_path / "missing.json") is False
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    assert manager.validate_config(invalid) is False
    invalid.write_text(json.dumps([]), encoding="utf-8")
    assert manager.validate_config(invalid) is False
    invalid.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    assert manager.validate_config(invalid) is False
    invalid.write_text(json.dumps({"mcpServers": {"bad": {}}}), encoding="utf-8")
    assert manager.validate_config(invalid) is False


def test_sync_mcp_main_and_script_entry(tmp_path: Path, monkeypatch, capsys):
    output = tmp_path / "mcp.json"
    assert sync_mcp.main(["--output", str(output)]) == 0
    assert "MCP_SYNC_SUCCESS" in capsys.readouterr().out

    monkeypatch.setattr(sync_mcp.MCPSyncManager, "validate_config", lambda self, path: False)
    assert sync_mcp.main(["--output", str(output)]) == 1
    assert "MCP_SYNC_ERROR" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["sync-mcp", "--output", str(output)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(sync_mcp.__file__)), run_name="__main__")
    assert exc.value.code == 0


def backlog_plan(root: Path, count: int = 13) -> None:
    squads = [{"id": "cybersecurity" if i == 0 else f"s_{i}", "personas": 1, "tasks": 2, "workflows": 3} for i in range(count)]
    path = root / "work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/plans/agents-squad-2.0-build-backlog.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"epics": [{"id": "OTHER"}, {"id": "EPIC-SQUAD-2-WAVES", "waves": [{"wave": 1, "squads": squads}]}]}), encoding="utf-8")


def test_backlog_build_validation_and_main(tmp_path: Path, monkeypatch, capsys):
    backlog_plan(tmp_path)
    result = backlog.build_manifest(tmp_path)
    assert result["stories"] == 13 and result["tasks"] == 104
    assert result["squads"][0]["tasks"][0]["risk"] == "high"
    review = next(item for item in result["squads"][0]["tasks"] if item["id"].endswith("-REVIEW"))
    assert review["owner"] == "governance-auditor"
    backlog_plan(tmp_path, 12)
    with pytest.raises(ValueError, match="counts"):
        backlog.build_manifest(tmp_path)
    backlog_plan(tmp_path)

    calls = []
    monkeypatch.setattr(backlog.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    output = tmp_path / "custom/out.json"
    monkeypatch.setattr(sys, "argv", ["backlog", "--root", str(tmp_path), "--output", str(output), "--apply"])
    assert backlog.main() == 0 and len(calls) == 117 and output.is_file()
    assert all(call[1]["timeout"] == backlog.WORK_ITEM_TIMEOUT_SECONDS for call in calls)
    assert "apply=True" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["backlog", "--root", str(tmp_path)])
    assert backlog.main() == 0
    assert "apply=False" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["backlog", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(backlog.__file__)), run_name="__main__")
    assert exc.value.code == 0
