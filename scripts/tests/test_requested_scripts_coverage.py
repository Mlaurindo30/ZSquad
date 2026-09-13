from __future__ import annotations

import argparse
import errno
import json
import os
import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import scripts.agent_squad as agent
import scripts.gate_validators as gates
import scripts.governed_io as gio
import scripts.local_agent_db as dbmod
import scripts.project_context as context
import scripts.quality_gate_runner as quality


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    for name in ("scripts", "agents", "contracts"):
        (runtime / name).mkdir(parents=True)
    return runtime


def _marker(project: Path, runtime: Path, **updates: object) -> Path:
    marker = project / ".agents_squad/config/project.yaml"
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"project_id": "sample", "runtime": str(runtime), "project_root": str(project)}
    payload.update(updates)
    marker.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return marker


def test_project_context_all_validation_paths(tmp_path: Path):
    runtime = _runtime(tmp_path)
    project = tmp_path / "project"
    child = project / "a/b"
    child.mkdir(parents=True)
    marker = _marker(project, runtime)

    with pytest.raises(context.ProjectContextError):
        context.validate_project_id("!")
    assert context.find_project_root(marker) == project.resolve()
    assert context.find_project_root(child) == project.resolve()
    assert context.find_project_root(tmp_path / "none") is None
    loaded = context.resolve_project_context(child)
    assert loaded.work_dir == runtime.resolve() / "work/sample"
    assert loaded.db_path == runtime.resolve() / "banco/squad.db"
    assert context.resolve_project_context(tmp_path, project).project_id == "sample"

    marker.write_text("[]", encoding="utf-8")
    with pytest.raises(context.ProjectContextError, match="marcador"):
        context.load_project_context(project)
    marker.write_text("[", encoding="utf-8")
    with pytest.raises(context.ProjectContextError, match="marcador"):
        context.load_project_context(project)
    marker.write_text(yaml.safe_dump({"project_id": "sample"}), encoding="utf-8")
    with pytest.raises(context.ProjectContextError, match="runtime ausente"):
        context.load_project_context(project)
    _marker(project, runtime, project_root=str(tmp_path / "other"))
    with pytest.raises(context.ProjectContextError, match="project_root"):
        context.load_project_context(project)
    _marker(project, tmp_path / "missing")
    with pytest.raises(context.ProjectContextError, match="runtime compartilhado"):
        context.load_project_context(project)
    with pytest.raises(context.ProjectContextError, match="não encontrado"):
        context.resolve_project_context(tmp_path / "unlinked")


class _Handle:
    def __init__(self, size: int = 0):
        self.size = size
        self.data = bytearray()
        self.closed = False
    def seek(self, _offset): pass
    def write(self, value): self.data.extend(value)
    def flush(self): pass
    def fileno(self): return 7
    def close(self): self.closed = True


def test_governed_io_success_cleanup_platforms_and_lock_paths(tmp_path: Path, monkeypatch):
    path = tmp_path / "value.txt"
    gio.atomic_write_text(path, "ok")
    assert path.read_text() == "ok"

    temporary = tmp_path / "orphan.tmp"
    monkeypatch.setattr(gio.tempfile, "mkstemp", lambda **kw: (os.open(temporary, os.O_CREAT | os.O_WRONLY), str(temporary)))
    monkeypatch.setattr(gio.os, "replace", Mock(side_effect=OSError("replace")))
    with pytest.raises(OSError, match="replace"):
        gio.atomic_write_text(path, "bad")
    assert not temporary.exists()

    monkeypatch.setattr(gio.os, "name", "posix")
    monkeypatch.setattr(gio.os, "open", Mock(return_value=8))
    monkeypatch.setattr(gio.os, "fsync", Mock())
    monkeypatch.setattr(gio.os, "close", Mock())
    gio._fsync_directory(tmp_path)
    gio.os.close.assert_called_once_with(8)

    fake_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4, flock=Mock())
    monkeypatch.setitem(__import__("sys").modules, "fcntl", fake_fcntl)
    handle = _Handle()
    gio._try_lock(handle); gio._unlock(handle)
    assert fake_fcntl.flock.call_count == 2

    monkeypatch.setattr(gio.os, "name", "nt")
    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=Mock())
    monkeypatch.setitem(__import__("sys").modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(gio.os, "fstat", lambda fd: SimpleNamespace(st_size=0))
    gio._try_lock(handle); gio._unlock(handle)
    assert handle.data == b"\0"

    with pytest.raises(ValueError):
        with gio.file_lock(tmp_path / "x", timeout=-1): pass


def test_governed_io_busy_timeout_retry_and_nonbusy(tmp_path: Path, monkeypatch):
    attempts = iter([BlockingIOError(errno.EACCES, "busy"), None])
    def retry(_handle):
        result = next(attempts)
        if result: raise result
    monkeypatch.setattr(gio, "_try_lock", retry)
    monkeypatch.setattr(gio, "_unlock", Mock())
    monkeypatch.setattr(gio.time, "sleep", Mock())
    with gio.file_lock(tmp_path / "retry", timeout=1): pass
    gio._unlock.assert_called_once()

    monkeypatch.setattr(gio, "_try_lock", Mock(side_effect=BlockingIOError(errno.EACCES, "busy")))
    monkeypatch.setattr(gio.time, "monotonic", Mock(side_effect=[0, 1]))
    with pytest.raises(gio.LockTimeoutError):
        with gio.file_lock(tmp_path / "timeout", timeout=0): pass

    monkeypatch.setattr(gio.time, "monotonic", Mock(return_value=0))
    monkeypatch.setattr(gio, "_try_lock", Mock(side_effect=OSError(errno.EINVAL, "bad")))
    with pytest.raises(OSError, match="bad"):
        with gio.file_lock(tmp_path / "bad"): pass
    assert gio._lock_is_busy(BlockingIOError())
    assert gio._lock_is_busy(OSError(errno.EAGAIN, "x"))
    assert not gio._lock_is_busy(OSError(errno.EINVAL, "x"))
    assert gio._lock_is_busy(OSError(errno.EACCES, "x"))
    real_os = gio.os
    monkeypatch.setattr(gio, "os", SimpleNamespace(name="posix"))
    busy = OSError("busy"); busy.errno = errno.EAGAIN
    assert gio._lock_is_busy(busy)
    monkeypatch.setattr(gio, "os", real_os)

    missing_temporary = tmp_path / "already-removed.tmp"
    monkeypatch.setattr(gio.tempfile, "mkstemp", lambda **kw: (os.open(missing_temporary, os.O_CREAT | os.O_WRONLY), str(missing_temporary)))
    original_unlink = Path.unlink
    def disappear(path, *args, **kwargs):
        if path == missing_temporary:
            original_unlink(path)
            raise FileNotFoundError(path)
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", disappear)
    monkeypatch.setattr(gio.os, "replace", Mock(side_effect=OSError("replace")))
    with pytest.raises(OSError, match="replace"):
        gio.atomic_write_text(tmp_path / "target", "x")


def test_local_db_remaining_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SQUAD_PROJECT_ID", "env-project")
    monkeypatch.setenv("SQUAD_DB_PATH", str(tmp_path / "env.db"))
    env_db = dbmod.LocalAgentDB()
    assert env_db.project_id == "env-project" and env_db.db_path == tmp_path / "env.db"
    monkeypatch.delenv("SQUAD_PROJECT_ID")
    assert dbmod.LocalAgentDB(tmp_path / "legacy.db", allow_legacy=True).project_id == "legacy"
    with pytest.raises(ValueError, match="obrigatório"): dbmod.LocalAgentDB(tmp_path / "x.db")
    with pytest.raises(ValueError, match="inválido"): dbmod.LocalAgentDB(tmp_path / "x.db", "bad id")

    db = dbmod.LocalAgentDB(tmp_path / "main.db", "p")
    source = tmp_path / "features.py"
    source.write_text(
        '"""module"""\nimport os.path as op\nfrom pkg import thing as alias\n'
        'def outer(x):\n    """doc"""\n    if x:\n        for y in x:\n            while False:\n                pass\n'
        '    try:\n        return x\n    except Exception:\n        return []\n'
        'class C:\n    """class doc"""\n    async def method(self):\n        return 1\n', encoding="utf-8")
    symbols, deps = db._extract_ast_symbols_and_deps(source, source.read_text())
    assert {s.kind for s in symbols} == {"function", "class"}
    assert {"os.path", "pkg"} <= {d[1] for d in deps}
    db.index_python_file(source)
    source.write_text("def broken(:\n", encoding="utf-8")
    assert db.index_python_file(source) == []
    missing = tmp_path / "missing.py"
    assert db.index_python_file(missing) == []
    assert db.index_python_file(tmp_path / "x.txt", "x = 1") == []

    (tmp_path / "sub").mkdir(); (tmp_path / "sub/a.py").write_text("x=1")
    (tmp_path / "sub/b.txt").write_text("x")
    assert db.index_directory(tmp_path / "sub") == 1
    (tmp_path / "sub/.git").mkdir(); (tmp_path / "sub/.git/ignored.py").write_text("x=1")
    assert db.index_directory(tmp_path / "sub") == 1

    empty = tmp_path / "empty.py"; empty.write_text("# comment\n\n")
    db.index_python_file(empty)
    health = db.get_code_health(str(empty))
    assert health.total_lines == 2
    assert db.get_code_health("nope.py") is None

    db.record_token_metrics("w", "a", "s", 1, 2, 0.5)
    assert db.get_token_summary("w")["total_tokens"] == 3
    assert db.get_token_summary("missing")["total_tokens"] == 0
    assert db.evaluate_quorum("w", "g")["status"] == "pending_votes"
    db.record_quorum_vote("w", "g", "a", "approve", 1.0)
    assert db.evaluate_quorum("w", "g")["status"] == "rejected"
    with db._connection() as conn:
        conn.execute("UPDATE quorum_votes SET vote='pass' WHERE work_item_id='w'")
    assert db.evaluate_quorum("w", "g")["status"] == "approved"
    db.log_trajectory("bench", "a", "w", "ok", 1, 2, 0.1, {"v": 1})
    assert json.loads(db.get_trajectory_history(task_id="w", agent_id="a", limit=1)[0]["details_json"]) == {"v": 1}
    with pytest.raises(ValueError): db.get_trajectory_history("w", limit=0)


def test_gate_helpers_and_all_gate_dispatch(tmp_path: Path, monkeypatch):
    assert gates._read_yaml(tmp_path / "missing") == {}
    assert gates._read_md(tmp_path / "missing") == ""
    work = tmp_path / "work"; work.mkdir()
    assert gates._project_root(work) == ROOT
    monkeypatch.setattr(gates, "_project_root", lambda _: None)
    assert not gates._verification_passed(work, "lint")

    monkeypatch.setattr(gates, "_project_root", lambda _: tmp_path)
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/verification-evidence.schema.json").write_text(json.dumps({"type": "object"}))
    evidence_dir = work / "evaluation"; evidence_dir.mkdir()
    evidence = evidence_dir / "lint.json"
    evidence.write_text("{}")
    assert not gates._verification_passed(work, "lint")

    schema = {"type": "object"}
    (tmp_path / "contracts/verification-evidence.schema.json").write_text(json.dumps(schema))
    artifact = tmp_path / "a.txt"; artifact.write_text("hello")
    digest = __import__("hashlib").sha256(artifact.read_bytes()).hexdigest()
    base = {"timestamp": datetime.now(timezone.utc).isoformat(), "passed": True, "exit_code": 0,
            "work_item": work.name, "verifier": "lint", "file_hashes": {"a.txt": digest}}
    evidence.write_text(json.dumps(base)); assert gates._verification_passed(work, "lint")
    for timestamp in [(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), datetime.now().isoformat()]:
        evidence.write_text(json.dumps({**base, "timestamp": timestamp})); assert not gates._verification_passed(work, "lint")
    evidence.write_text(json.dumps({**base, "file_hashes": {"a.txt": "bad"}})); assert not gates._verification_passed(work, "lint")

    assert not gates._coverage_passed(work)
    monkeypatch.setattr(gates, "_verification_passed", lambda *_: True)
    (work / "evaluation/coverage.json").write_text(json.dumps({"results": {"status": "PASS", "minimum_percent": 80, "total_percent": 100, "branch_percent": 100}}))
    assert gates._coverage_passed(work)
    (work / "evaluation/coverage.json").write_text("bad")
    assert not gates._coverage_passed(work)
    monkeypatch.setattr(gates, "validate_tdd_cycle", Mock(side_effect=ValueError))
    assert not gates._tdd_passed(work)

    for gate_id in list(gates._GATE_VALIDATORS):
        monkeypatch.setitem(gates._GATE_VALIDATORS, gate_id, lambda p, g=gate_id: {"approved": True, "gate": g})
    assert gates.validate_gate("G1-product", work)["approved"]
    assert len(gates.validate_all_gates(work)["gates"]) == 3
    assert not gates.validate_gate("G0", work)["approved"]


def test_quality_gate_runner_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "root"; root.mkdir()
    tracked = root / "tracked.txt"; tracked.write_text("payload", encoding="utf-8")
    output = tmp_path / "evidence.json"
    assert list(quality.hash_files(root, ["tracked.txt"])) == ["tracked.txt"]
    with pytest.raises(ValueError): quality.hash_files(root, ["../outside"])
    with pytest.raises(FileNotFoundError): quality.hash_files(root, ["missing"])

    quality.validate_evidence(ROOT, {
        "schema_version": 1, "work_item": "TASK-COV", "verifier": "unit",
        "persona": "qa-engineer", "command": ["ok"], "exit_code": 0,
        "passed": True, "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_hashes": {}, "results": {},
    })
    bad_root = tmp_path / "schema-root"; (bad_root / "contracts").mkdir(parents=True)
    (bad_root / "contracts/verification-evidence.schema.json").write_text(
        json.dumps({"type": "object", "required": ["never"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="evidência inválida"): quality.validate_evidence(bad_root, {})

    report = root / "coverage.json"
    report.write_text(json.dumps({"totals": {"covered_branches": 9, "num_branches": 10,
                                               "percent_covered": 90}}), encoding="utf-8")
    assert quality._coverage_results(root, "coverage.json", 90)["status"] == "PASS"
    assert quality._coverage_results(root, "coverage.json", 91)["status"] == "FAIL"
    with pytest.raises(ValueError, match="fora da raiz"): quality._coverage_results(root, "../x", 80)
    report.write_text(json.dumps({"totals": {"covered_branches": 0, "num_branches": 0,
                                               "percent_covered": 100}}), encoding="utf-8")
    with pytest.raises(ValueError, match="sem branches"): quality._coverage_results(root, "coverage.json", 80)

    monkeypatch.setattr(quality.subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=0, stdout="success", stderr="")))
    passed = quality.run_verifier(ROOT, "TASK-COV", "unit", "qa-engineer", ["ok"],
                                  ["scripts/quality_gate_runner.py"], output)
    assert passed["passed"] and json.loads(output.read_text(encoding="utf-8"))["passed"]
    monkeypatch.setattr(quality.subprocess, "run", Mock(side_effect=quality.subprocess.TimeoutExpired(
        ["slow"], 1, output="partial")))
    timed = quality.run_verifier(ROOT, "TASK-COV", "unit", "qa-engineer", ["slow"], [], output, 1)
    assert timed["exit_code"] == 124 and timed["stdout"] == "partial"
    monkeypatch.setattr(quality.subprocess, "run", Mock(side_effect=OSError("missing tool")))
    assert quality.run_verifier(ROOT, "TASK-COV", "unit", "qa-engineer", ["x"], [], output)["exit_code"] == 127
    monkeypatch.setattr(quality.subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=0, stdout="", stderr="warning")))
    covered = quality.run_verifier(ROOT, "TASK-COV", "coverage", "qa-engineer", ["ok"], [], output,
                                   coverage_report="missing.json")
    assert covered["exit_code"] == 1 and "cobertura abaixo" in covered["stderr"]

    monkeypatch.setattr(quality, "run_verifier", Mock(return_value={"passed": True}))
    cli = ["--root", str(ROOT), "--work-item", "TASK-COV", "--verifier", "unit",
           "--persona", "qa-engineer", "--output", str(output)]
    assert quality.main(cli + ["python", "-V"]) == 0
    quality.run_verifier.return_value = {"passed": False}
    assert quality.main(cli + ["false"]) == 1
    with pytest.raises(SystemExit): quality.main(cli)
    with pytest.raises(SystemExit): runpy.run_path(str(ROOT / "scripts/quality_gate_runner.py"), run_name="__main__")
    original_argv = sys.argv
    sys.argv = [str(ROOT / "scripts/agent_squad.py"), "--root", str(ROOT), "audit"]
    try:
        with pytest.raises(SystemExit): runpy.run_path(str(ROOT / "scripts/agent_squad.py"), run_name="__main__")
    finally:
        sys.argv = original_argv


def test_agent_helpers_hive_integration_and_cli_dispatch(tmp_path: Path, monkeypatch, capsys):
    real_execute_command = agent._execute_command
    real_build_parser = agent._build_parser
    path = tmp_path / "bad.yaml"; path.write_text("[")
    with pytest.raises(agent.SquadError): agent.read_yaml(path)
    control = tmp_path / "control"; control.write_bytes(b"x\x00")
    clean = tmp_path / "clean"; clean.write_bytes(b"x\n")
    assert agent.contains_control(control) and not agent.contains_control(clean)
    with pytest.raises(agent.SquadError): agent._parse_criteria(["bad"])
    assert agent._parse_criteria([" a = pass "]) == [("a", "pass")]

    squad = agent.AgentSquad(ROOT, "coverage-project")
    with pytest.raises(agent.SquadError): squad._validate({}, "handoff.schema.json")
    with pytest.raises(agent.SquadError): squad.init_work_item("bad", "low", base=tmp_path)
    item = squad.init_work_item("EPIC-COV", "low", base=tmp_path)
    with pytest.raises(agent.SquadError): squad._item(tmp_path / "missing")
    with pytest.raises(agent.SquadError): squad._item_reference(item, "../x", "ref")
    with pytest.raises(agent.SquadError): squad._item_reference(item, "missing.md", "ref")
    with pytest.raises(agent.SquadError): squad.record_memory(item, "missing-agent", "x", "epic.md")
    with pytest.raises(agent.SquadError, match="catálogo aprovado"):
        squad.activation_packet("requirements-analyst", item=item, discovered=["unknown"])
    assert squad.discover("", 2) == []
    monkeypatch.setattr(agent, "LocalAgentDB", Mock(side_effect=[ValueError("db"), Mock()]))
    assert squad.track_tokens(item, "requirements-analyst", "x", -1, 0, 0)["total_prompt_tokens"] == -1

    assert squad.run_integration_engine("fake_engine", work_item=item)["status"] == "error"
    assert squad.run_integration_engine("../bad", work_item=item)["status"] == "error"

    fake = Mock()
    fake.discover.return_value = []
    fake.run_integration_engine.return_value = {"status": "ok"}
    assert agent._execute_command(fake, argparse.Namespace(command="discover", query="q", limit=1)) == 0
    assert agent._execute_command(fake, argparse.Namespace(command="run-engine", engine="x", work_item="w")) == 0
    assert "status" in capsys.readouterr().out

    monkeypatch.setattr(agent, "AgentSquad", Mock(return_value=fake))
    monkeypatch.setattr(agent, "_execute_command", Mock(side_effect=agent.SquadError("boom")))
    monkeypatch.setattr(agent, "_build_parser", lambda: SimpleNamespace(parse_args=lambda *a: argparse.Namespace(root=ROOT, project_root=None, project_name=None)))
    assert agent.main() == 2
    monkeypatch.setattr(agent, "_execute_command", real_execute_command)
    monkeypatch.setattr(agent, "_build_parser", real_build_parser)
    parser = agent._build_parser()
    fake = Mock()
    fake.activation_packet.return_value = {}
    assert agent._execute_command(fake, parser.parse_args([
        "activate-agent", "--agent", "a", "--work-item", "w",
    ])) == 0
    assert agent._execute_command(fake, parser.parse_args([
        "activate-agent", "--agent", "a",
    ])) == 0
    monkeypatch.setattr(agent, "_build_parser", lambda: SimpleNamespace(parse_args=lambda *a: argparse.Namespace(root=ROOT, project_root=None, project_name=None)))
    monkeypatch.setattr(agent, "find_project_root", Mock(return_value=tmp_path))
    monkeypatch.setattr(agent, "resolve_project_context", Mock(side_effect=agent.ProjectContextError("marker")))
    assert agent.main() == 2
    monkeypatch.setattr(agent, "resolve_project_context", Mock(return_value=SimpleNamespace(
        runtime_root=ROOT, project_id="coverage-project")))
    monkeypatch.setattr(agent, "AgentSquad", Mock(return_value=fake))
    monkeypatch.setattr(agent, "_execute_command", Mock(return_value=0))
    assert agent.main() == 0


def test_agent_defensive_branches_and_custom_iterables(tmp_path: Path, monkeypatch):
    assert agent.read_yaml.__module__ == "scripts.agent_squad"
    scalar = tmp_path / "scalar.yaml"; scalar.write_text("- x", encoding="utf-8")
    with pytest.raises(agent.SquadError, match="Esperado objeto"): agent.read_yaml(scalar)

    squad = agent.AgentSquad(ROOT, "coverage-project")
    item = squad.init_work_item("TASK-DEFENSIVE", "low", base=tmp_path)
    assert squad._work_base() == ROOT / "work/coverage-project"
    rooted = tmp_path / "runtime/work/coverage-project/TASK-ROOTED"
    (rooted).mkdir(parents=True)
    (rooted / "status.yaml").write_text("id: TASK-ROOTED", encoding="utf-8")
    squad.root = tmp_path / "runtime"
    assert squad._item("work/coverage-project/TASK-ROOTED") == rooted.resolve()
    generic = tmp_path / "runtime/work/TASK-GENERIC"; generic.mkdir()
    (generic / "status.yaml").write_text("id: TASK-GENERIC", encoding="utf-8")
    assert squad._item("work/TASK-GENERIC") == generic.resolve()
    squad.root = ROOT
    with pytest.raises(agent.SquadError, match="caminho relativo"):
        squad._item_reference(item, str((tmp_path / "absolute").resolve()), "ref")
    with pytest.raises(agent.SquadError, match="Risco inválido"):
        squad._init_work_item_unlocked("TASK-RISK", "impossible", tmp_path)
    real_file_lock = agent.file_lock
    monkeypatch.setattr(agent, "file_lock", Mock(side_effect=agent.LockTimeoutError("busy")))
    with pytest.raises(agent.SquadError, match="busy"):
        with squad._artifact_lock(item): pass
    monkeypatch.setattr(agent, "file_lock", real_file_lock)

    (item / "evidence.txt").write_text("ok", encoding="utf-8")
    with pytest.raises(agent.SquadError, match="from/to"):
        squad.create_handoff(item, "missing", "missing", "x", ["evidence.txt"], ["evidence.txt"], "evidence.txt")
    with pytest.raises(agent.SquadError, match="gate desconhecido"):
        squad.create_handoff(item, next(iter(squad.agent_ids)), next(iter(squad.agent_ids)), "x",
                             ["evidence.txt"], ["evidence.txt"], "evidence.txt", "GX")

    original_workflow = squad.workflow
    owner = next(iter(squad.agent_ids))
    squad.workflow = {"gates": {"GX": {"owners": [owner], "criteria": []}}}
    squad.gate_ids = {"GX"}
    with pytest.raises(agent.SquadError, match="obrigatórios"):
        squad.decide_gate(item, "GX", owner, [], ["evidence.txt"])
    squad.workflow = original_workflow

    engine = ROOT / "integrations/fake.py"
    monkeypatch.setattr(Path, "is_file", lambda p: True if p == engine else Path.exists(p))
    monkeypatch.setattr(agent.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stdout="o", stderr="e")))
    assert squad.run_integration_engine("fake", work_item=item)["status"] == "error"
    monkeypatch.setattr(agent.subprocess, "run", Mock(side_effect=OSError("offline")))
    assert squad.run_integration_engine("fake", work_item=item)["status"] == "unavailable"


def test_agent_remaining_validation_activation_memory_and_audit(tmp_path: Path, monkeypatch):
    squad = agent.AgentSquad(ROOT, "coverage-project")
    item = squad.init_work_item("TASK-REMAINING", "medium", base=tmp_path)
    assert squad._item(item) == item

    legacy = agent.AgentSquad(ROOT, allow_legacy=True)
    legacy.root = tmp_path / "legacy-runtime"
    legacy_item = legacy.init_work_item("TASK-RELATIVE", "low")
    assert legacy._item("TASK-RELATIVE") == legacy_item
    monkeypatch.setattr(agent, "ID_RE", SimpleNamespace(fullmatch=lambda _: True))
    with pytest.raises(agent.SquadError, match="fora da raiz"):
        squad._init_work_item_unlocked("../escape", "low", tmp_path)

    sender, recipient = list(squad.agent_ids)[:2]
    (item / "e.txt").write_text("e", encoding="utf-8")
    delta_ref = "memory/deltas/MEM-TASK-REMAINING-000.yaml"
    (item / delta_ref).parent.mkdir(parents=True, exist_ok=True)
    (item / delta_ref).write_text("kind: fact\nstatement: seed", encoding="utf-8")
    handoff = squad.create_handoff(item, sender, recipient, "summary", ["e.txt"], ["e.txt"], delta_ref)
    with pytest.raises(agent.SquadError, match="destinatário"):
        squad.ack_handoff(item, handoff["id"], sender)

    original_validate = gates.validate_gate
    monkeypatch.setattr(gates, "validate_gate", lambda *_: {"findings": []})
    gate_id = next(iter(squad.gate_ids)); gate = squad.workflow["gates"][gate_id]
    owner = next(iter(set(gate.get("owners", [gate.get("owner")])) - {None}))
    expected = list(gate.get("criteria", []))
    if expected:
        criteria = [(name, "pass") for name in expected]
        criteria.append(criteria[0])
        with pytest.raises(agent.SquadError, match="duplicado"):
            squad.decide_gate(item, gate_id, owner, criteria, ["e.txt"])
        executable = {"bdd-specification-valid", "tdd-cycle-valid", "clean-code-executed", "tests-executed",
                      "security-executed", "acceptance-bdd-executed", "regression-executed", "coverage-executed"}
        if set(expected) & executable:
            with pytest.raises(agent.SquadError):
                squad.decide_gate(item, gate_id, owner, [(n, "pass") for n in expected], ["e.txt"])
    monkeypatch.setattr(gates, "validate_gate", original_validate)

    missing_summary = squad.init_work_item("TASK-NOSUMMARY", "low", base=tmp_path)
    (missing_summary / "memory/shared/summary.md").unlink(missing_ok=True)
    with pytest.raises(agent.SquadError, match="summary.md ausente"): squad.compact_memory(missing_summary)
    (item / "memory/deltas/MEM-BAD.yaml").write_text("- bad", encoding="utf-8")
    for index, kind in enumerate(("fact", "decision", "risk")):
        (item / f"memory/deltas/MEM-{index}.yaml").write_text(yaml.safe_dump({"kind": kind, "statement": kind}))
    (item / "memory/shared/summary.md").parent.mkdir(parents=True, exist_ok=True)
    (item / "memory/shared/summary.md").write_text("# Summary\n- Seed line 1\n- Seed line 2\n", encoding="utf-8")
    compacted = squad.compact_memory(item)
    assert compacted["compacted_lines"] > 0

    chosen = next(iter(squad.agents))
    manifest_path = ROOT / squad.agents[chosen]["manifest"]
    real_read_yaml = agent.read_yaml
    def manifest_with_selected(path):
        if path == manifest_path:
            return {"native": [{"path": "missing-skill"}], "assigned": [],
                    "discovery": {"maximum_loaded": 10}, "handoff": {"schema": "x"}}
        return real_read_yaml(path)
    monkeypatch.setattr(agent, "read_yaml", manifest_with_selected)
    with pytest.raises(agent.SquadError, match="skill inválida"):
        squad.activation_packet(chosen)
    skill_dir = ROOT / "missing-skill"
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda p: p == skill_dir or real_is_dir(p))
    with pytest.raises(agent.SquadError, match="SKILL.md ausente"):
        squad.activation_packet(chosen)
    monkeypatch.setattr(Path, "is_dir", real_is_dir)
    monkeypatch.setattr(agent, "read_yaml", real_read_yaml)

    many = [f"s/{index}" for index in range(8)]
    monkeypatch.setattr(agent, "read_yaml", lambda p: {
        "native": [{"path": value} for value in many], "assigned": [],
        "discovery": {"maximum_loaded": 10}, "handoff": {"schema": "x"},
    } if p == manifest_path else real_read_yaml(p))
    with pytest.raises(agent.SquadError, match="limite de skills"):
        squad.activation_packet(chosen)
    monkeypatch.setattr(agent, "read_yaml", real_read_yaml)

    monkeypatch.setattr(squad, "_item", Mock(return_value=item))
    monkeypatch.setattr(squad, "_work_base", Mock(return_value=tmp_path))
    packet = squad.activation_packet(chosen, item=item)
    assert packet["work_item"] == "TASK-REMAINING"

    (item / "bad.txt").write_bytes(b"x\0")
    (item / "handoffs" / f"{handoff['id']}.yaml").write_text(yaml.safe_dump(handoff), encoding="utf-8")
    (item / "gate-decisions/x.yaml").write_text("decision: pass", encoding="utf-8")
    monkeypatch.setattr(squad, "_validate", Mock())
    assert any("controle" in error for error in squad.validate_work_item(item))
    assert squad._validate.called

    squad.workflow = {"gates": {
        "GX": {"owners": [owner], "criteria": ["tests-executed"]},
        "GM": {"owners": [owner], "criteria": ["plain"], "human_required_when": "risk-medium-high-critical"},
        "GB": {"owners": [owner], "criteria": ["plain"], "human_required_when": "business-acceptance"},
    }}
    squad.gate_ids = set(squad.workflow["gates"])
    monkeypatch.setattr(gates, "validate_gate", lambda *_: {"findings": []})
    with pytest.raises(agent.SquadError, match="não cobriu"):
        squad.decide_gate(item, "GX", owner, [("tests-executed", "pass")], ["e.txt"])
    for human_gate in ("GM", "GB"):
        with pytest.raises(agent.SquadError, match="aprovação humana"):
            squad.decide_gate(item, human_gate, owner, [("plain", "pass")], ["e.txt"])
    monkeypatch.setattr(gates, "validate_gate", lambda *_: {"findings": []})
    approved = squad.decide_gate(item, "GM", owner, [("plain", "pass")], ["e.txt"], owner, "e.txt")
    assert approved["human_approval"]["required"]
    squad.workflow["gates"]["GL"] = {"owners": [owner], "criteria": ["plain"]}
    squad.gate_ids.add("GL")
    low_item = squad.init_work_item("TASK-LOW-GATE", "low", base=tmp_path)
    (low_item / "e.txt").write_text("e", encoding="utf-8")
    low_decision = squad.decide_gate(low_item, "GL", owner, [("plain", "pass")], ["e.txt"])
    assert not low_decision["human_approval"]["required"]

    discovery_root = tmp_path / "discovery-runtime"
    for name, text in (("plain", "text"), ("unterminated", "---\ndescription: x"),
                       ("scalar", "---\n- x\n---\n"), ("mapped", "---\ndescription: mapped\ntag: x\n---\n")):
        path = discovery_root / name; path.mkdir(parents=True)
        (path / "SKILL.md").write_text(text, encoding="utf-8")
    discovery = object.__new__(agent.AgentSquad); discovery.root = discovery_root
    discovery.catalog_entries = {name: {} for name in ("plain", "unterminated", "scalar", "mapped")}
    assert isinstance(discovery.discover("mapped", 4), list)

    audit_root = tmp_path / "audit-runtime"
    (audit_root / "agents").mkdir(parents=True); (audit_root / "templates").mkdir()
    (audit_root / "agents/bad.md").write_bytes(b"x\0")
    (audit_root / "templates/bad.yaml").write_text("[", encoding="utf-8")
    audit_squad = object.__new__(agent.AgentSquad); audit_squad.root = audit_root
    audit_squad.templates = audit_root / "templates"
    audit_squad.catalog_entries = {
        "absent": {"assigned_to": ["a"]},
        "unknown": {"assigned_to": ["a"]},
    }
    (audit_root / "agents/manifest.yaml").write_text(yaml.safe_dump({
        "assigned": [{"path": "not-catalogued"}], "native": [{"path": "unknown"}],
    }), encoding="utf-8")
    audit_squad.agents = {"a": {"manifest": "agents/manifest.yaml"}}
    audit_errors = audit_squad.audit()
    assert any("controle" in error for error in audit_errors)
    assert any("catalogada ausente" in error for error in audit_errors)

    actual_root = tmp_path / "actual-runtime"
    (actual_root / "agents/a/skill").mkdir(parents=True); (actual_root / "integrations").mkdir(parents=True)
    (actual_root / "agents/a/skill/SKILL.md").write_text("x", encoding="utf-8")
    (actual_root / "integrations/engine.py").write_text("x", encoding="utf-8")
    (actual_root / "integrations/__init__.py").write_text("", encoding="utf-8")
    actual = object.__new__(agent.AgentSquad); actual.root = actual_root
    actual.templates = actual_root / "templates"; actual.templates.mkdir()
    actual.catalog_entries = {}; actual.agents = {}
    assert any("fora do catálogo" in error for error in actual.audit())

    foundation = object.__new__(agent.AgentSquad)
    foundation.contracts = tmp_path / "contracts"; foundation.templates = tmp_path / "templates"
    foundation.contracts.mkdir(); foundation.templates.mkdir()
    assert any("foundation ausente" in e for e in foundation.validate_foundation())
    for schema_name, template_name in {
        "source-artifact.schema.json": "source-artifact.yaml", "disposition.schema.json": "disposition.yaml",
        "squad.schema.json": "squad.yaml", "route.schema.json": "route.yaml",
        "task-template.schema.json": "task-template.yaml", "workflow-template.schema.json": "workflow-template.yaml",
    }.items():
        (foundation.contracts / schema_name).write_text("{}", encoding="utf-8")
        (foundation.templates / template_name).write_text("- invalid", encoding="utf-8")
    assert foundation.validate_foundation()


def test_agent_execute_every_cli_command(monkeypatch, capsys):
    squad = Mock()
    squad.init_work_item.return_value = Path("w")
    squad.discover.return_value = []
    squad.create_handoff.return_value = {"id": "h"}
    squad.ack_handoff.return_value = {}
    squad.decide_gate.return_value = {}
    squad.validate_work_item.side_effect = [[], ["bad"]]
    squad.record_memory.return_value = {}
    squad.activation_packet.return_value = {}
    squad.audit.side_effect = [[], ["bad"]]
    squad.validate_foundation.side_effect = [[], ["bad"]]
    squad.compact_memory.return_value = {"original_lines": 2, "compacted_lines": 1}
    squad._item.return_value = Path("EPIC-X")
    squad.track_tokens.return_value = {}
    squad.decide_quorum.return_value = {}
    squad.run_integration_engine.return_value = {}
    parser = agent._build_parser()
    commands = [
        ["init-work-item", "--id", "EPIC-X"], ["discover", "--query", "q"],
        ["create-handoff", "--work-item", "w", "--from", "a", "--to", "b", "--summary", "s", "--artifacts", "x", "--evidence", "x", "--memory-delta", "x"],
        ["ack-handoff", "--work-item", "w", "--handoff", "h", "--agent", "a"],
        ["decide-gate", "--work-item", "w", "--gate", "G1-product", "--decider", "a", "--criteria", "x=pass", "--evidence", "x"],
        ["validate-work-item", "--work-item", "w"], ["validate-work-item", "--work-item", "w"],
        ["record-memory", "--work-item", "w", "--author", "a", "--statement", "s", "--source", "x", "--kind", "decision"],
        ["record-memory", "--work-item", "w", "--author", "a", "--statement", "s", "--source", "x", "--kind", "risk"],
        ["activate-agent", "--agent", "a", "--work-item", "w"],
        ["audit"], ["audit"], ["validate-foundation"], ["validate-foundation"],
        ["compact-memory", "--work-item", "w"],
        ["track-tokens", "--work-item", "w", "--agent", "a", "--step", "s", "--prompt-tokens", "1", "--completion-tokens", "2"],
        ["decide-quorum", "--work-item", "w", "--gate", "g"],
        ["run-engine", "--engine", "e", "--work-item", "w"],
    ]
    for argv in commands:
        agent._execute_command(squad, parser.parse_args(argv))
    assert squad.run_integration_engine.called
    assert agent._execute_command(squad, argparse.Namespace(command="unknown")) == 0


def test_cli_review_learning_and_curator_cycle(monkeypatch):
    import types

    fake_review = types.ModuleType("background_review")
    fake_review.main = Mock(return_value=0)
    fake_curator = types.ModuleType("curator_cycle")
    fake_curator.main = Mock(return_value=1)
    monkeypatch.setitem(sys.modules, "background_review", fake_review)
    monkeypatch.setitem(sys.modules, "curator_cycle", fake_curator)

    squad = Mock()
    squad.root = ROOT
    parser = agent._build_parser()

    assert agent._execute_command(squad, parser.parse_args([
        "review-learning", "--kind", "gate", "--artifact", "GD-X.yaml",
    ])) == 0
    fake_review.main.assert_called_once_with([
        "--kind", "gate", "--artifact", "GD-X.yaml",
        "--root", str(ROOT), "--model", "granite4.1:3b",
    ])

    assert agent._execute_command(squad, parser.parse_args([
        "review-learning", "--kind", "handoff", "--artifact", "H.yaml",
        "--dry-run", "--model", "granite4.1:8b",
    ])) == 0
    fake_review.main.assert_called_with([
        "--kind", "handoff", "--artifact", "H.yaml",
        "--root", str(ROOT), "--model", "granite4.1:8b", "--dry-run",
    ])

    assert agent._execute_command(squad, parser.parse_args(["curator-cycle"])) == 1
    fake_curator.main.assert_called_once_with([
        "--root", str(ROOT), "--backup-dir", str(ROOT / "backups"),
    ])

    assert agent._execute_command(squad, parser.parse_args([
        "curator-cycle", "--backup-dir", "/tmp/bk",
    ])) == 1
    fake_curator.main.assert_called_with([
        "--root", str(ROOT), "--backup-dir", "/tmp/bk",
    ])


def test_cli_auto_correct(monkeypatch):
    import types

    fake_trigger = types.ModuleType("auto_correction_trigger")
    fake_trigger.main = Mock(return_value=0)
    monkeypatch.setitem(sys.modules, "auto_correction_trigger", fake_trigger)

    squad = Mock()
    squad.root = ROOT
    parser = agent._build_parser()

    assert agent._execute_command(squad, parser.parse_args([
        "auto-correct", "--kind", "incident", "--source", "findings/incident.md",
    ])) == 0
    fake_trigger.main.assert_called_once_with([
        "--kind", "incident", "--source", "findings/incident.md", "--root", str(ROOT),
    ])

    assert agent._execute_command(squad, parser.parse_args([
        "auto-correct", "--kind", "gate-rejected", "--source", "GD-X.yaml", "--apply",
    ])) == 0
    fake_trigger.main.assert_called_with([
        "--kind", "gate-rejected", "--source", "GD-X.yaml", "--root", str(ROOT), "--apply",
    ])


def test_cli_insights(monkeypatch):
    import types

    fake_insights = types.ModuleType("squad_insights")
    fake_insights.main = Mock(return_value=0)
    monkeypatch.setitem(sys.modules, "squad_insights", fake_insights)

    squad = Mock()
    parser = agent._build_parser()

    assert agent._execute_command(squad, parser.parse_args(["insights"])) == 0
    fake_insights.main.assert_called_once_with([])

    assert agent._execute_command(squad, parser.parse_args(
        ["insights", "--project-id", "agent-squad"])) == 0
    fake_insights.main.assert_called_with(["--project", "agent-squad"])


def test_gate_validator_sections_with_complete_text(tmp_path: Path, monkeypatch):
    work = tmp_path / "EPIC-X"
    for name in ("design", "adr", "plans", "tests", "security", "evaluation", "observability", "performance", "release", "gate-decisions"):
        (work / name).mkdir(parents=True, exist_ok=True)
    rich = "Given When Then acceptance rollback observability performance security threat dependency approved pass evidence owner deadline waiver cab deploy release runbook"
    for relative in ["design/architecture.md", "adr/ADR-1.md", "plans/implementation-plan.md", "tests/test-plan.md", "security/threat-model.md", "observability/plan.md", "performance/plan.md", "release/release-plan.md", "release/runbook.md"]:
        (work / relative).write_text(rich, encoding="utf-8")
    (work / "status.yaml").write_text(yaml.safe_dump({"risk": "low", "state": "done"}))
    (work / "gate-decisions/G1.yaml").write_text(yaml.safe_dump({"decision": "pass"}))
    monkeypatch.setattr(gates, "_verification_passed", lambda *_: True)
    monkeypatch.setattr(gates, "_coverage_passed", lambda *_: True)
    monkeypatch.setattr(gates, "_tdd_passed", lambda *_: True)
    monkeypatch.setattr(gates, "validate_features", lambda *_: {"approved": True})
    gates.validate_G2_design(work); gates.validate_G3_readiness(work); gates.validate_G6_governance_release(work)


def test_gate_defensive_iterables_and_rejections(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    work = root / "work/TASK-X"
    (root / "contracts").mkdir(parents=True)
    (work / "evaluation").mkdir(parents=True)
    (root / "contracts/verification-evidence.schema.json").write_text(
        json.dumps({"type": "object", "required": ["required"]}), encoding="utf-8")
    (work / "evaluation/schema.json").write_text("{}", encoding="utf-8")
    assert gates._project_root(work) == root
    assert not gates._verification_passed(work, "schema")

    schema = ROOT / "contracts/verification-evidence.schema.json"
    (root / "contracts/verification-evidence.schema.json").write_bytes(schema.read_bytes())
    outside = tmp_path / "outside"; outside.write_text("x", encoding="utf-8")
    base = {
        "schema_version": 1, "work_item": "TASK-X", "verifier": "v", "persona": "qa-engineer",
        "command": ["ok"], "exit_code": 0, "passed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(), "results": {},
    }
    for name, hashes in (("escape", {"../../../outside": "0" * 64}),
                         ("missing", {"missing.txt": "0" * 64})):
        payload = dict(base, verifier=name, file_hashes=hashes)
        (work / f"evaluation/{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        assert not gates._verification_passed(work, name)

    class Validators(dict):
        def items(self):
            yield "G-pass", lambda _: {"approved": True}
            yield "G-fail", lambda _: {"approved": False}
    monkeypatch.setattr(gates, "_GATE_VALIDATORS", Validators())
    result = gates.validate_all_gates(work)
    assert not result["all_approved"] and len(result["gates"]) == 2


def test_local_db_biomarker_full_paths(tmp_path: Path, monkeypatch):
    db = dbmod.LocalAgentDB(tmp_path / "db.sqlite", "p")
    source = tmp_path / "health.py"
    content = '"""O que é: x.\nResponsabilidade: y.\nPra que serve: z."""\n# c\ndef f():\n    """doc"""\n    return 1\n'
    source.write_text(content)
    db.index_python_file(source)
    health = db.get_code_health(str(source))
    assert health.contract_compliant and health.docstring_coverage == 1.0
    assert health.code_lines > 0
    on_demand = tmp_path / "on_demand.py"
    on_demand.write_text("def created_later():\n    return 1\n", encoding="utf-8")
    assert db.get_code_health(str(on_demand)).symbol_count == 1
