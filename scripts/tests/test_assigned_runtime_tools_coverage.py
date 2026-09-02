from __future__ import annotations

import importlib
import importlib.util
import json
import runpy
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

import scripts.bootstrap_pack as bp
import scripts.quality_gate_runner as qgr
import scripts.handoff_toolkit as ht
import scripts.setup_environment as se
import scripts.sre_incident_loop as sre


def test_import_path_and_quality_gate_entrypoint_branches(monkeypatch):
    scripts_dir = str(Path(bp.__file__).resolve().parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != scripts_dir])
    runpy.run_path(bp.__file__)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.quality_gate_runner", run_name="__main__")
    assert exc.value.code == 2


class Result:
    def __init__(self, code=0, stdout="out", stderr="err"):
        self.returncode, self.stdout, self.stderr = code, stdout, stderr


def test_bootstrap_extractors_and_frontmatter(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    assert bp._extract_work_item_structured(missing)["exists"] is False
    item = tmp_path / "item"; item.mkdir()
    assert bp._extract_work_item_text(item) == ""
    assert bp._extract_work_item_structured(item) == {"path": str(item), "exists": True}
    (item / "status.yaml").write_text("title: T\ndescription: 12\n", encoding="utf-8")
    (item / "epic.md").write_text("EPIC", encoding="utf-8")
    assert bp._extract_work_item_text(item) == "T 12 EPIC"
    structured = bp._extract_work_item_structured(item)
    assert structured["status"]["title"] == "T" and structured["epic_md_preview"] == "EPIC"

    (item / "status.yaml").write_text("- list", encoding="utf-8")
    assert bp._extract_work_item_text(item) == "EPIC"
    (item / "status.yaml").write_text("title: ''\ndescription: ''", encoding="utf-8")
    assert bp._extract_work_item_text(item) == "EPIC"

    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: (_ for _ in ()).throw(OSError()) if self.name in {"status.yaml", "epic.md"} else original(self, **kw))
    assert bp._extract_work_item_text(item) == ""
    assert bp._extract_work_item_structured(item) == {"path": str(item), "exists": True, "status": {}}
    monkeypatch.undo()

    skill = tmp_path / "SKILL.md"
    skill.write_text("\ufeff---\nname: demo\nx: 1\n---\nbody", encoding="utf-8")
    assert bp._skill_frontmatter(skill)["name"] == "demo"
    skill.write_text("body", encoding="utf-8"); assert bp._skill_frontmatter(skill) == {}
    skill.write_text("---\nname: x", encoding="utf-8"); assert bp._skill_frontmatter(skill) == {}
    skill.write_text("---\n- x\n---", encoding="utf-8"); assert bp._skill_frontmatter(skill) == {}
    assert bp._skill_frontmatter(tmp_path / "none") == {}


def _bootstrap_stubs(monkeypatch, root, packets, suggestions=()):
    class Squad:
        agents = {"a": {"manifest": "manifest.yaml"}}
        def __init__(self, root): self.root = root
        def activation_packet(self, *args, **kwargs):
            value = packets.pop(0) if isinstance(packets, list) else packets
            if isinstance(value, Exception): raise value
            return value
    monkeypatch.setattr(bp, "AgentSquad", Squad)
    selector = types.ModuleType("scripts.skill_selector")
    selector.suggest_and_load = lambda **kw: suggestions
    monkeypatch.setitem(sys.modules, "scripts.skill_selector", selector)
    agent_mod = sys.modules[bp.AgentSquad.__module__] if False else types.ModuleType("agent_squad")
    agent_mod.read_yaml = lambda path: {"native": ["n"]}
    monkeypatch.setitem(sys.modules, "agent_squad", agent_mod)
    rendered = types.SimpleNamespace(render_agent_prompt=lambda **kw: "PROMPT")
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: rendered)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a: types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda mod: None)))


def test_create_bootstrap_pack_paths(tmp_path, monkeypatch):
    root = tmp_path / "root"; (root / "scripts").mkdir(parents=True); (root / "skills" / "s").mkdir(parents=True)
    (root / "skills" / "s" / "SKILL.md").write_text("---\nname: Skill\ndescription: Desc\ntag: v\n---", encoding="utf-8")
    item = root / "work" / "W"; item.mkdir(parents=True); (item / "status.yaml").write_text("title: task", encoding="utf-8")
    suggestion = types.SimpleNamespace(loaded=True, ranked=types.SimpleNamespace(skill=types.SimpleNamespace(path="skills/s/SKILL.md")))
    packet = {"load_order": ["skills/s/SKILL.md", "skills/s/SKILL.md", "skills/s/SKILL.md"], "native": ["skills/s/SKILL.md"], "assigned": [], "discovered": [], "prompt": "p"}
    _bootstrap_stubs(monkeypatch, root, packet, [suggestion, types.SimpleNamespace(loaded=False)])
    out = bp.create_bootstrap_pack("a", tmp_path / "out", "work/W", assigned=[], auto_select_skills=True, root=root)
    assert out.exists() and (out / "system_prompt.md").read_text() == "PROMPT"
    assert len(json.loads((out / "skills_manifest.json").read_text())["skills"]) == 3
    assert yaml.safe_load((out / "runtime_hints.yaml").read_text())["agent"]["id"] == "a"

    # no item/discovery, and assigned/discovered source classifications
    packet2 = {"load_order": ["x", "y"], "native": [], "assigned": ["x"], "discovered": ["y"], "prompt": None}
    _bootstrap_stubs(monkeypatch, root, packet2)
    bp.create_bootstrap_pack("a", tmp_path / "out2", discovered=["y"], root=root)


@pytest.mark.parametrize("mode", ["reduce", "same", "fallback", "raise", "selector_error", "empty_task"])
def test_bootstrap_error_paths(tmp_path, monkeypatch, mode):
    root = tmp_path / "r"; (root / "scripts").mkdir(parents=True); (root / "work" / "W").mkdir(parents=True)
    (root / "work" / "W" / "status.yaml").write_text("title: task" if mode != "empty_task" else "{}", encoding="utf-8")
    err = bp.SquadError("bad")
    if mode == "reduce": packets = [err, {"load_order": [], "native": [], "assigned": [], "discovered": [], "prompt": None}]; assigned=[]
    elif mode == "same": packets = [err, {"load_order": [], "native": [], "assigned": [], "discovered": [], "prompt": None}]; assigned=[1,2,3,4,5,6]
    elif mode == "fallback": packets = [err, {"load_order": [], "native": [], "assigned": [], "discovered": [], "prompt": None}]; assigned=[]
    elif mode == "raise": packets = [err]; assigned=[]
    else: packets = {"load_order": [], "native": [], "assigned": [], "discovered": [], "prompt": None}; assigned=[]
    suggestion = types.SimpleNamespace(loaded=True, ranked=types.SimpleNamespace(skill=types.SimpleNamespace(path="d")))
    _bootstrap_stubs(monkeypatch, root, packets, [suggestion])
    if mode == "selector_error": sys.modules["scripts.skill_selector"].suggest_and_load = lambda **kw: (_ for _ in ()).throw(RuntimeError())
    if mode == "fallback": sys.modules["agent_squad"].read_yaml = lambda p: (_ for _ in ()).throw(RuntimeError())
    kwargs = dict(agent="a", output_dir=tmp_path/mode, work_item="work/W", assigned=assigned, discovered=[] if mode != "raise" else None, auto_select_skills=mode != "raise", root=root)
    if mode == "raise":
        with pytest.raises(bp.SquadError): bp.create_bootstrap_pack(**kwargs)
    else:
        bp.create_bootstrap_pack(**kwargs)


def test_handoff_model_validation_ack_and_cli(tmp_path, monkeypatch, capsys):
    root = tmp_path; wi = root / "work" / "W" / "handoffs"; wi.mkdir(parents=True)
    (wi / "HANDOFF-old.yaml").write_text("x")
    h = ht.create_handoff("W", "from", "to", "sum", [], ["proof"], root=root)
    assert h.id.endswith("002") and h.decisions == []
    assert ht._work_item_dir("W", root).name == "W"

    good = {"id":"H", "work_item_id":"W", "from":"a", "to":"b", "created_at":"now", "status":"ready", "summary":"s", "artifacts":[], "evidence":["e"], "memory_delta":"", "next_gate":None, "acceptance":{}, "acknowledgement":{"status":"accepted", "acknowledged_by":""}}
    path = wi / "HANDOFF-test.yaml"; path.write_text(yaml.safe_dump(good), encoding="utf-8")
    result = ht.validate_handoff(path); assert result["valid"] and result["warnings"]
    local_contract = tmp_path / "work" / "contracts" / "handoff.schema.json"; local_contract.parent.mkdir(); local_contract.write_text("{}")
    good["acknowledgement"] = {"status": "pending", "acknowledged_by": ""}; good["status"] = "done"
    path.write_text(yaml.safe_dump(good)); assert ht.validate_handoff(path) == {"valid": True, "errors": [], "warnings": []}
    good["status"] = "ready"; good["evidence"] = []; path.write_text(yaml.safe_dump(good)); assert not ht.validate_handoff(path)["valid"]
    path.write_text("- list"); assert "mapping" in ht.validate_handoff(path)["errors"][0]
    path.write_text("[bad"); assert "parse" in ht.validate_handoff(path)["errors"][0]
    path.write_text("{}") ; assert len(ht.validate_handoff(path)["errors"]) == 13

    path.write_text(yaml.safe_dump(good)); ack = ht.acknowledge_handoff(path, "me", "rejected", "why"); assert ack["status"] == "rejected"
    with pytest.raises(ValueError): ht.acknowledge_handoff(path, "me", "bad")
    path.write_text("- bad");
    with pytest.raises(ValueError): ht.acknowledge_handoff(path, "me")

    path.write_text(yaml.safe_dump(good))
    assert ht.main(["validate", str(path)]) == 1
    good["evidence"] = ["e"]; path.write_text(yaml.safe_dump(good)); assert ht.main(["validate", str(path)]) == 0
    good["acknowledgement"] = {"status": "accepted", "acknowledged_by": ""}; path.write_text(yaml.safe_dump(good)); assert ht.main(["validate", str(path)]) == 0
    assert ht.main(["ack", str(path), "--by", "me"]) == 0
    assert ht.main([]) == 0
    assert "ERRORS:" in capsys.readouterr().out


def test_setup_environment_all_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(se, "ROOT", tmp_path)
    se.print_step("x")
    # sync scripts absent, success, failure (stderr and stdout fallback)
    se.sync_vendor_repositories(); se.sync_mcps()
    clone = tmp_path/"integrations"/"clone_or_update_repos.py"; clone.parent.mkdir(); clone.write_text("")
    mcp = tmp_path/"scripts"/"sync_mcp_servers.py"; mcp.parent.mkdir(); mcp.write_text("")
    results = iter([Result(), Result(1), Result(1, stdout="fallback", stderr=""), Result(), Result(1), Result(1, stdout="fallback", stderr="")])
    monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: next(results))
    for _ in range(3): se.sync_vendor_repositories()
    for _ in range(3): se.sync_mcps()

    venv = tmp_path/".venv"
    monkeypatch.setattr(se.shutil, "which", lambda x: "uv")
    monkeypatch.setattr(se.subprocess, "run", Mock())
    assert se.ensure_virtualenv() == Path(sys.executable)
    venv.mkdir(); assert se.ensure_virtualenv() == Path(sys.executable)
    venv.rmdir(); monkeypatch.setattr(se.shutil, "which", lambda x: None); monkeypatch.setattr(se.sys, "platform", "linux")
    se.ensure_virtualenv()
    py = venv/"bin"/"python"; py.parent.mkdir(parents=True, exist_ok=True); py.write_text(""); assert se.ensure_virtualenv() == py
    monkeypatch.setattr(se.sys, "platform", "win32"); (venv/"Scripts").mkdir(); (venv/"Scripts"/"python.exe").write_text(""); assert se.ensure_virtualenv().name == "python.exe"

    with pytest.raises(SystemExit): se.init_database()
    schema = tmp_path/"banco"/"schema.sql"; schema.write_text("create table if not exists x(id integer);")
    se.init_database()

    vendor = tmp_path/"integrations"/"vendor"/"codebase-memory-mcp"/"pkg"/"pypi"
    se.install_vendor_mcp_packages(Path("python")); vendor.mkdir(parents=True)
    for result in [Result(), Result(1), Result(1, stdout="fallback", stderr="")]:
        monkeypatch.setattr(se.subprocess, "run", lambda *a, _r=result, **k: _r); se.install_vendor_mcp_packages(Path("python"))

    monkeypatch.setattr(se.shutil, "which", lambda x: None); se.provision_docker_stack()
    monkeypatch.setattr(se.shutil, "which", lambda x: "docker")
    for sequence in [[Result(1)], [Result(), Result()], [Result(), Result(1)], [Result(), Result(1, stdout="fallback", stderr="")]]:
        it=iter(sequence); monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: next(it)); se.provision_docker_stack()
    monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))); se.provision_docker_stack()

    for sequence, raises in [([Result(), Result()], False), ([Result(1)], True), ([Result(), Result(1)], True)]:
        it=iter(sequence); monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: next(it))
        if raises:
            with pytest.raises(SystemExit): se.validate_suite()
        else: se.validate_suite()

    calls=[]
    for name in ["sync_vendor_repositories","init_database","sync_mcps","provision_docker_stack","validate_suite"]: monkeypatch.setattr(se,name,lambda n=name:calls.append(n))
    monkeypatch.setattr(se,"ensure_virtualenv",lambda:Path("py")); monkeypatch.setattr(se,"install_vendor_mcp_packages",lambda p:calls.append(str(p)))
    se.main(); assert len(calls)==6
    assert capsys.readouterr().out


def test_sre_creation_cli_and_main_guard(tmp_path, monkeypatch, capsys):
    values = {"%Y%m%d-%H%M%S":"20240101-010203", "%Y-%m-%dT%H:%M:%SZ":"iso", "%Y-%m-%d %H:%M:%S UTC":"human", "%Y-%m-%d":"date"}
    monkeypatch.setattr(sre.time, "strftime", lambda fmt: values[fmt])
    # create_incident_bug agora delega a AgentSquad.init_work_item (schema-validado),
    # que exige a raiz real do squad (config/contracts/templates); isolar via cópia
    # em tmp_path evita poluir work/ do repositório real a cada execução do teste.
    import shutil
    for folder in ("config", "contracts", "templates"):
        shutil.copytree(sre.ROOT / folder, tmp_path / folder)
    monkeypatch.setattr(sre, "ROOT", tmp_path)
    loop=sre.SREIncidentLoop(tmp_path); bug=loop.create_incident_bug("alert","svc","trace","critical")
    assert bug == "BUG-INCIDENT-SVC-20240101-010203" and (tmp_path/"work"/bug/"documentation"/"delivery-ledger.md").exists()
    monkeypatch.setattr(sre,"SREIncidentLoop",lambda *a, **k: loop)
    assert sre.main(["--alert","a","--service","s"]) == 0

    # runpy reexecuta o módulo como um __main__ novo (ROOT recalculado a partir do
    # arquivo real): usa --project-name para isolar em work/<projeto>/, com limpeza.
    real_root = Path(sre.__file__).resolve().parents[1]
    test_project = "test-sre-incident-runpy"
    shutil.rmtree(real_root / "work" / test_project, ignore_errors=True)
    monkeypatch.setattr(sys,"argv",["sre_incident_loop.py","--alert","a","--service","s","--project-name",test_project])
    try:
        with pytest.raises(SystemExit) as exc: runpy.run_path(str(Path(sre.__file__)), run_name="__main__")
        assert exc.value.code == 0 and "INCIDENT_WORK_ITEM_CREATED" in capsys.readouterr().out
    finally:
        shutil.rmtree(real_root / "work" / test_project, ignore_errors=True)


def test_module_main_guards(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["handoff_toolkit.py"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(ht.__file__)), run_name="__main__")
    assert exc.value.code == 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    monkeypatch.setattr(se.shutil, "which", lambda name: None)
    connection = Mock(); monkeypatch.setattr(se.sqlite3, "connect", lambda path: connection)
    runpy.run_path(str(Path(se.__file__)), run_name="__main__")


def test_bootstrap_import_path_branch(monkeypatch):
    scripts_dir = str(Path(bp.__file__).resolve().parent)
    monkeypatch.setattr(sys, "path", [scripts_dir, *[p for p in sys.path if p != scripts_dir]])
    runpy.run_path(str(Path(bp.__file__)), run_name="bootstrap_copy")
    assert sys.path[0] == scripts_dir
