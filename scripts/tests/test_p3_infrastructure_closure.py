"""Targeted automated tests for P3 infrastructure, Docker topology, incident cycle, and runtime modes.

Covers:
1. Dockerfile security: no dead port 8080, non-root squaduser, audit entrypoint
2. Root docker-compose.yml canonicality, absence of port 8080 and host-specific paths
3. Retirement of legacy banco/docker-compose.yml
4. Canonical local CLI audit command execution
5. run-continuous enforcement: requires --work-item, fails closed if omitted
6. Local-native mode execution independent of Docker
7. Native incident cycle initialization and state transitions (triage -> mitigation -> postmortem -> done)
8. Incident work item strict schema conformance with contracts/work-item.schema.json
9. config/squads.yaml architectural specification header & zero runtime dependency
10. Complete parse validation across all config/*.yaml files
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError, read_yaml
from local_agent_db import LocalAgentDB
from render_agent_prompt import render_agent_prompt


def _link_or_copy(src: Path, dst: Path) -> None:
    if not src.exists() or dst.exists():
        return
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(src), str(dst))
            return
        except Exception:
            pass
    try:
        os.symlink(src, dst, target_is_directory=src.is_dir())
    except Exception:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _make_authorized_runtime(tmp_path: Path, project_id: str = "agent_squad") -> tuple[AgentSquad, Path]:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in ("agents", "config", "contracts", "templates", "skills", "integrations", "banco"):
        _link_or_copy(ROOT / name, runtime_dir / name)
    work_dir = runtime_dir / "work" / project_id
    work_dir.mkdir(parents=True, exist_ok=True)
    squad = AgentSquad(runtime_dir, project_name=project_id)
    return squad, work_dir


def test_dockerfile_no_port_8080_and_nonroot_user():
    """Verify Dockerfile removes dead port 8080, creates squaduser, and sets audit default CMD."""
    dockerfile_path = ROOT / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile must exist at repository root"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Port 8080 must not be exposed
    assert "8080" not in content, "Dead port 8080 must be removed from Dockerfile"
    assert "EXPOSE" not in content, "No dead ports should be exposed in container definition"

    # Non-root squaduser creation and usage
    assert "useradd" in content and "squaduser" in content, "Dockerfile must create squaduser"
    assert "USER squaduser" in content, "Dockerfile must switch to non-root USER squaduser"

    # Default CMD audit
    assert 'CMD ["python", "scripts/agent_squad.py", "audit"]' in content


def test_canonical_compose_no_dead_port_and_portable_paths():
    """Verify root docker-compose.yml has no dead port 8080 and uses portable volume paths."""
    compose_path = ROOT / "docker-compose.yml"
    assert compose_path.is_file(), "docker-compose.yml must exist at repository root"

    content = compose_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content) or {}

    # Port 8080 must not be published
    services = data.get("services", {})
    assert "squad-core" in services, "squad-core service must be defined"
    core_service = services["squad-core"]
    assert "ports" not in core_service, "squad-core must not expose dead ports like 8080"
    assert "8080" not in content, "Port 8080 must not appear in docker-compose.yml"

    # No host-specific absolute Windows drive letters
    assert not re.search(r"[A-Za-z]:\\", content), "docker-compose.yml must not contain Windows drive paths"
    assert not re.search(r"/[Uu]sers/", content), "docker-compose.yml must not contain hardcoded host user paths"

    # Relative portable volumes
    volumes = core_service.get("volumes", [])
    for v in volumes:
        v_str = str(v)
        assert v_str.startswith("./") or not Path(v_str).is_absolute(), f"Volume binding must be portable: {v_str}"

    # Standby command must use constant-memory while True loop, avoiding infinite list comprehension
    cmd_parts = core_service.get("command", [])
    cmd_str = " ".join(cmd_parts) if isinstance(cmd_parts, list) else str(cmd_parts)
    assert "while True:" in cmd_str, "Standby command must use while True loop"
    assert "time.sleep" in cmd_str, "Standby command must sleep"
    assert "signal.SIGTERM" in cmd_str, "Standby command must register SIGTERM handler"
    assert "iter(int, 1)" not in cmd_str, "Standby command must not use iter(int, 1) infinite loop"
    assert "[" not in cmd_str.split("while True:")[0] or "for _ in" not in cmd_str, (
        "Standby command must not use infinite list comprehension which leaks memory"
    )


def test_banco_legacy_compose_retired():
    """Assert legacy banco/docker-compose.yml is retired and only root compose remains."""
    legacy_compose = ROOT / "banco" / "docker-compose.yml"
    assert not legacy_compose.exists(), "Legacy banco/docker-compose.yml must be retired/removed"

    root_compose = ROOT / "docker-compose.yml"
    assert root_compose.is_file(), "Root docker-compose.yml must be the sole canonical compose file"


def test_canonical_local_cli_audit():
    """Verify scripts/agent_squad.py audit executes successfully with exit code 0."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "agent_squad.py"), "audit"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"agent_squad.py audit failed (exit {result.returncode}): {result.stderr}"
    assert "AUDIT_OK" in result.stdout


def test_run_continuous_requires_work_item(tmp_path: Path):
    """Verify run-continuous enforces required --work-item and fails closed when missing."""
    # 1. CLI invocation without --work-item must exit non-zero
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "agent_squad.py"), "run-continuous"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    assert result.returncode != 0, "CLI run-continuous without --work-item must fail closed"
    assert "required" in result.stderr.lower() and "work-item" in result.stderr.lower()

    # 2. Programmatic invocation with empty/None item must raise SquadError
    squad, _ = _make_authorized_runtime(tmp_path)
    with pytest.raises(SquadError, match="work item"):
        squad.run_continuous("")


def test_local_native_mode_independent_of_docker():
    """Verify full local-native lifecycle executes without Docker daemon or sockets."""
    # Ensure no DOCKER_HOST or docker daemon requirement for local operations
    squad = AgentSquad(ROOT, project_name="agent_squad")

    # Local SQLite DB initializes and queries purely in-process
    db = LocalAgentDB(ROOT / "banco" / "squad.db", project_id="agent_squad")
    assert db.db_path.is_file()
    with db._connection() as conn:
        row = conn.execute("SELECT 1").fetchone()
        assert row[0] == 1

    # Prompt compilation operates in-process
    prompt = render_agent_prompt("delivery-orchestrator")
    assert "Delivery Orchestrator" in prompt

    # Registry and catalogs resolve without containers
    assert len(squad.agents) == 41
    assert len(squad.catalog_entries) >= 170


def test_incident_cycle_initialization_and_transitions(tmp_path: Path):
    """Verify incident work items initialize at triage and transition through mitigation and postmortem to done."""
    squad, work_dir = _make_authorized_runtime(tmp_path)

    # 1. Initialize incident item
    work_id = "INCIDENT-20260917-001"
    item_path = squad.init_work_item(work_id, risk="high", base=work_dir, item_type="incident")
    assert item_path.is_dir()

    status = read_yaml(item_path / "status.yaml")
    assert status["id"] == work_id
    assert status["type"] == "incident"
    assert status["cycle"] == "incident"
    assert status["state"] == "triage"
    assert status["current_gate"] is None, "Incident cycle must bypass formal G1-G6 gates"

    # 2. Advance: triage -> mitigation
    res1 = squad.advance_state(item_path)
    assert res1["previous_state"] == "triage"
    assert res1["state"] == "mitigation"
    assert res1["current_gate"] is None

    # 3. Advance: mitigation -> postmortem
    res2 = squad.advance_state(item_path)
    assert res2["previous_state"] == "mitigation"
    assert res2["state"] == "postmortem"
    assert res2["current_gate"] is None

    # 4. Advance: postmortem -> done
    res3 = squad.advance_state(item_path)
    assert res3["previous_state"] == "postmortem"
    assert res3["state"] == "done"
    assert res3["current_gate"] is None

    # 5. Done is terminal: advancing further must raise SquadError
    with pytest.raises(SquadError, match="estado terminal"):
        squad.advance_state(item_path)


def test_incident_schema_conformance():
    """Verify incident work items strictly conform to contracts/work-item.schema.json."""
    schema_path = ROOT / "contracts" / "work-item.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    base_incident = {
        "id": "INCIDENT-20260917-SYS01",
        "type": "incident",
        "cycle": "incident",
        "state": "triage",
        "risk": "critical",
        "owner": "26-sre-observability-engineer",
        "active_agents": ["26-sre-observability-engineer", "00-delivery-orchestrator"],
        "next_action": "Publish initial comms and begin triage runbook",
        "artifacts": ["docs/runbook.md", "docs/incident-log.md"],
    }

    # All incident states must validate cleanly against JSON schema
    for valid_state in ("triage", "mitigation", "postmortem", "done"):
        base_incident["state"] = valid_state
        jsonschema.validate(instance=base_incident, schema=schema)

    # Invalid state must fail schema validation
    base_incident["state"] = "unknown_incident_state"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=base_incident, schema=schema)

    # Invalid ID pattern must fail schema validation
    base_incident["state"] = "triage"
    base_incident["id"] = "INVALIDPREFIX-001"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=base_incident, schema=schema)


def test_config_squads_yaml_spec_header():
    """Verify config/squads.yaml contains non-runtime spec header and has zero runtime dependencies."""
    squads_cfg = ROOT / "config" / "squads.yaml"
    assert squads_cfg.is_file(), "config/squads.yaml must exist"

    content = squads_cfg.read_text(encoding="utf-8")
    assert "ARCHITECTURAL SPECIFICATION" in content
    assert "NON-RUNTIME" in content
    assert "NÃO é consumido diretamente pelos motores de execução em runtime" in content

    # Scan all production scripts: no script may import or load config/squads.yaml
    scripts_dir = ROOT / "scripts"
    for py_file in scripts_dir.rglob("*.py"):
        if "tests" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "squads.yaml" not in text, f"Runtime script {py_file.name} must not reference non-runtime config/squads.yaml"


def test_all_config_files_parse_valid():
    """Verify all YAML configuration files in config/ parse cleanly into valid non-empty dictionaries."""
    config_dir = ROOT / "config"
    cfg_files = sorted(list(config_dir.glob("*.yaml")) + list(config_dir.glob("*.yml")))

    assert len(cfg_files) >= 8, f"Expected at least 8 config files, found {len(cfg_files)}"

    for cfg_file in cfg_files:
        parsed = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict), f"Config file {cfg_file.name} did not parse as a dictionary"
        assert len(parsed) > 0, f"Config file {cfg_file.name} must not be empty"
