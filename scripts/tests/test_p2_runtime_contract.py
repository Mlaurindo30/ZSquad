"""Targeted automated tests for P2 runtime and render contracts (Section 40 specification).

Covers:
1. Canonical 3-Pillar Memory Topology & SQLite Authority (config/memory.yaml)
2. Legacy Memory Compatibility & DERIVED_COMPATIBILITY projection semantics
3. Cross-project memory isolation (no leakage between tenant databases)
4. Deterministic static render (byte-for-byte reproducibility / identical SHA-256)
5. Manifest-driven engine filtering (only declared engines injected)
6. Irrelevant engine exclusion (omitted for non-technical specialists)
7. Absence of obsolete generic CoT/ToT prescription blocks
8. Uniqueness of shared contract section headers (no duplicate injections)
9. Orchestrator delegation-only constraint (non-dispatchable primary host)
10. Specialist role preservation (distinct identities, purposes, and frameworks)
11. Graceful degradation when optional memory artifacts are absent
12. Absence of unresolved template placeholders
13. Portable environment paths (%SQUAD_RUNTIME% formatting)
14. Derived compatibility semantics across prompt guidelines and configuration
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError, read_yaml
from local_agent_db import LocalAgentDB
from render_agent_prompt import _render_cache, render_agent_prompt


@pytest.fixture(autouse=True)
def _reset_render_cache():
    """Ensure render cache does not bleed across tests."""
    _render_cache.clear()
    yield
    _render_cache.clear()


def test_canonical_memory_model():
    """Verify 3-pillar definitions and SQLite authority in config/memory.yaml."""
    memory_cfg_path = ROOT / "config" / "memory.yaml"
    assert memory_cfg_path.exists(), "config/memory.yaml must exist"

    cfg = yaml.safe_load(memory_cfg_path.read_text(encoding="utf-8")) or {}
    assert cfg.get("version") == 2
    assert cfg.get("model") == "3-pillar-memory-architecture"

    topology = cfg.get("topology", {})
    assert "primary_project_memory" in topology
    assert "collaboration_traceability" in topology
    assert "global_second_brain" in topology

    # Pilar 1: Local / Canonical Primary Memory
    pilar1 = topology["primary_project_memory"]
    assert pilar1.get("engine") == "sqlite-wal"
    assert pilar1.get("database") == "banco/squad.db"
    assert pilar1.get("scope") == "project-local"
    assert pilar1.get("authoritative") is True
    assert "facts" in pilar1.get("tables", {})
    assert "symbols" in pilar1.get("tables", {})
    assert "dependencies" in pilar1.get("tables", {})
    assert "metrics" in pilar1.get("tables", {})

    # Pilar 2: Azure DevOps Collaboration & Traceability
    pilar2 = topology["collaboration_traceability"]
    assert pilar2.get("platform") == "azure-devops"
    assert pilar2.get("scope") == "project-shared"
    assert pilar2.get("authoritative") is True

    # Pilar 3: Hive-Mind Global Second Brain
    pilar3 = topology["global_second_brain"]
    assert pilar3.get("name") == "Hive-Mind"
    assert pilar3.get("scope") == "cross-project-global"
    assert pilar3.get("authoritative_for_project") is False


def test_legacy_memory_compatibility():
    """Assert summary.md header, status, and DERIVED_COMPATIBILITY policy."""
    memory_cfg_path = ROOT / "config" / "memory.yaml"
    cfg = yaml.safe_load(memory_cfg_path.read_text(encoding="utf-8")) or {}

    deprecated = cfg.get("deprecated_locations", {})
    assert deprecated.get("status") == "deprecated"
    assert "shared" in deprecated

    shared = deprecated["shared"]
    assert shared.get("path") == "work/<project>/<WORK-ID>/memory/shared/summary.md"
    assert shared.get("status") == "DERIVED_COMPATIBILITY"
    assert shared.get("projection_source") == "banco/squad.db"
    assert "not an authoritative store" in shared.get("semantics", "")

    # Also verify that compiled prompts announce this derived compatibility policy
    rendered = render_agent_prompt("software-engineer")
    assert "DERIVED_COMPATIBILITY" in rendered
    assert "banco/squad.db" in rendered


def test_derived_compatibility_semantics():
    """Verify DERIVED_COMPATIBILITY projection semantics in configuration and prompt instructions."""
    memory_cfg_path = ROOT / "config" / "memory.yaml"
    cfg = yaml.safe_load(memory_cfg_path.read_text(encoding="utf-8")) or {}
    shared_spec = cfg["deprecated_locations"]["shared"]

    assert shared_spec["status"] == "DERIVED_COMPATIBILITY"
    assert shared_spec["projection_source"] == "banco/squad.db"

    # Verify that the memory architecture prompt specifies that summary.md is non-authoritative
    rendered = render_agent_prompt("software-engineer")
    assert "projeção gerada a partir do SQLite" in rendered
    assert "não sendo fonte primária de verdade" in rendered


def test_no_cross_project_memory_leakage(tmp_path: Path):
    """Verify work-item memory isolation between distinct project namespaces."""
    db_path = tmp_path / "banco" / "squad.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_alpha = LocalAgentDB(db_path, project_id="tenant-alpha")
    db_beta = LocalAgentDB(db_path, project_id="tenant-beta")

    # Record memory fact under tenant-alpha
    fact_id = db_alpha.record_memory_fact(
        project_id="tenant-alpha",
        work_item_id="TASK-TENANT-001",
        author="software-engineer",
        kind="decision",
        statement="Use AES-GCM-256 for payload encryption",
        source="unit-test",
    )
    assert fact_id > 0

    # Tenant alpha can query its own fact
    facts_alpha = db_alpha.get_memory_facts("tenant-alpha", "TASK-TENANT-001")
    assert len(facts_alpha) == 1
    assert facts_alpha[0]["statement"] == "Use AES-GCM-256 for payload encryption"

    # Tenant beta cannot see tenant alpha's fact for the same work_item_id
    facts_beta = db_beta.get_memory_facts("tenant-beta", "TASK-TENANT-001")
    assert len(facts_beta) == 0

    # Attempting to query tenant-alpha using tenant-beta client raises ValueError
    with pytest.raises(ValueError, match="incompatível com namespace"):
        db_beta.get_memory_facts("tenant-alpha", "TASK-TENANT-001")


def test_deterministic_static_render():
    """Assert render_agent_prompt static renders produce byte-for-byte identical output."""
    for agent_id in ("software-engineer", "delivery-orchestrator", "test-engineer"):
        _render_cache.clear()
        run1 = render_agent_prompt(agent_id)
        hash1 = hashlib.sha256(run1.encode("utf-8")).hexdigest()

        _render_cache.clear()
        run2 = render_agent_prompt(agent_id)
        hash2 = hashlib.sha256(run2.encode("utf-8")).hexdigest()

        assert run1 == run2, f"Static render for {agent_id} must be byte-for-byte identical"
        assert hash1 == hash2, f"SHA-256 hash mismatch across runs for {agent_id}"


def test_engine_filtering_manifest_driven():
    """Verify that agents with declared engines receive only their declared engines."""
    squad = AgentSquad(ROOT)
    agent_id = "software-engineer"
    manifest = read_yaml(ROOT / squad.agents[agent_id]["manifest"])

    declared_paths = {
        item.get("path") if isinstance(item, dict) else item
        for item in manifest.get("native", []) + manifest.get("assigned", [])
    }
    declared_engine_names = {Path(p).name for p in declared_paths if "integrations" in str(p)}

    rendered = render_agent_prompt(agent_id)
    assert "# MOTORES DE INTEGRAÇÃO (`integrations/`)" in rendered

    # Every engine name rendered in table rows must be declared in manifest
    table_match = re.search(r"\| Engine \| Quando usar \|\n\|--------\|-------------\|\n((?:\| `[^`]+` \|[^\n]+\n)+)", rendered)
    assert table_match is not None, "Expected an engines table in rendered output"
    rendered_engines = set(re.findall(r"\| `([^`]+)` \|", table_match.group(1)))

    catalog_by_name = {
        e.get("name", Path(e.get("path", "")).name): e.get("path", "")
        for e in squad.skills_catalog.get("catalog", [])
        if e.get("domain") == "integration-engines"
    }

    assert len(rendered_engines) > 0
    for eng in rendered_engines:
        declared_path = catalog_by_name.get(eng)
        assert declared_path in declared_paths, (
            f"Engine '{eng}' (path '{declared_path}') rendered in {agent_id} prompt but not declared in manifest"
        )


def test_irrelevant_engine_exclusion():
    """Verify that agents without declared engines completely omit the engines section."""
    for non_technical_agent in ("technical-writer", "agile-coach"):
        rendered = render_agent_prompt(non_technical_agent)
        assert "# MOTORES DE INTEGRAÇÃO" not in rendered, (
            f"Agent '{non_technical_agent}' has no declared engines and must not receive engines section"
        )


def test_obsolete_cot_tot_absence():
    """Assert rendered prompts do not contain generic CoT/ToT prescriptions."""
    obsolete_patterns = [
        re.compile(r"\bUse Chain of Thought\b", re.IGNORECASE),
        re.compile(r"\bUse Tree of Thoughts\b", re.IGNORECASE),
        re.compile(r"\bChain of Thought \(CoT\)\b", re.IGNORECASE),
        re.compile(r"\bTree of Thoughts \(ToT\)\b", re.IGNORECASE),
        re.compile(r"<cot_guidelines>", re.IGNORECASE),
        re.compile(r"<thinking_process>", re.IGNORECASE),
        re.compile(r"<thought_tree>", re.IGNORECASE),
    ]

    representative_agents = (
        "delivery-orchestrator",
        "software-engineer",
        "test-engineer",
        "technical-writer",
        "agile-coach",
    )

    for agent_id in representative_agents:
        rendered = render_agent_prompt(agent_id)
        for pat in obsolete_patterns:
            assert not pat.search(rendered), (
                f"Obsolete CoT/ToT pattern '{pat.pattern}' found in rendered prompt for {agent_id}"
            )

        # Confirm outcome-oriented quality requirements are present instead
        assert "Requisitos de Qualidade Orientados a Resultado" in rendered
        assert "Evidência Comprovável" in rendered


def test_no_duplicate_shared_contract_sections():
    """Assert shared contract section headers appear at most once in compiled output."""
    shared_headers = [
        "# CONTRATO COGNITIVO, ANTI-ALUCINAÇÃO & QUALIDADE DE EXECUÇÃO",
        "# ARQUITETURA CANÔNICA DE MEMÓRIA EM 3 PILARES",
        "# HABILIDADES E CONHECIMENTOS CARREGADOS (SKILLS)",
        "## Azure DevOps — Contexto Operacional e Modelo de Contas (SoD)",
    ]

    for agent_id in ("software-engineer", "solution-architect", "delivery-orchestrator"):
        rendered = render_agent_prompt(agent_id)
        for header in shared_headers:
            count = rendered.count(header)
            assert count == 1, f"Header '{header}' appeared {count} times in {agent_id} prompt (expected 1)"


def test_orchestrator_delegation_only():
    """Assert 00-delivery-orchestrator preserves delegation/orchestration contract."""
    squad = AgentSquad(ROOT)
    orch_entry = squad.agents["delivery-orchestrator"]

    # Invariant: orchestrator is host, provider-primary, non-dispatchable
    assert orch_entry.get("mode") == "host"
    assert orch_entry.get("provider_primary") is True
    assert orch_entry.get("dispatchable") is False

    # Attempting to compile orchestrator prompt with output_path fails closed
    with pytest.raises(SquadError, match="provider-primary host não pode ser despachado"):
        render_agent_prompt("delivery-orchestrator", output_path="out/orch.txt")

    # In-memory render succeeds and emphasizes delegation
    rendered = render_agent_prompt("delivery-orchestrator")
    assert "Delivery Orchestrator" in rendered or "delivery-orchestrator" in rendered
    assert "Orquestração" in rendered or "orchestration" in rendered.lower()


def test_specialist_roles_preserved():
    """Assert representative specialists remain specialized, distinct, and configured."""
    squad = AgentSquad(ROOT)
    specialist_keys = {
        "06": "software-engineer",
        "09": "code-reviewer",
        "10": "security-reviewer",
        "11": "test-engineer",
        "13": "devops-release-engineer",
        "19": "technical-writer",
        "27": "platform-engineer",
        "37": "fullstack-engineer",
        "40": "agile-coach",
    }

    # Verify all representative specialists exist with distinct IDs and prompt files
    prompt_files: set[str] = set()
    titles: set[str] = set()

    for prefix, agent_id in specialist_keys.items():
        assert agent_id in squad.agents, f"Specialist agent {agent_id} ({prefix}) must exist in registry"
        entry = squad.agents[agent_id]
        assert prefix in entry["path"], f"Expected prefix {prefix} in path {entry['path']}"

        prompt_file = f"{entry['path']}/PROMPT.md"
        assert prompt_file not in prompt_files, f"Duplicate prompt file {prompt_file} for {agent_id}"
        prompt_files.add(prompt_file)

        title = entry["title"]
        assert title not in titles, f"Duplicate title '{title}' for {agent_id}"
        titles.add(title)

        # Verify prompt file exists on disk and has specialist content
        full_prompt_path = ROOT / prompt_file
        assert full_prompt_path.exists(), f"Prompt file {full_prompt_path} does not exist"
        prompt_content = full_prompt_path.read_text(encoding="utf-8")
        assert len(prompt_content.strip()) > 100, f"Prompt content for {agent_id} is unexpectedly empty"


def test_missing_optional_memory_behavior(tmp_path: Path):
    """Verify missing summary.md or memory files degrade gracefully without crashing render."""
    # Create an authorized work item with only status.yaml
    work_item_dir = ROOT / "work" / "agent_squad" / "TASK-TEST-DEGRADE-01"
    work_item_dir.mkdir(parents=True, exist_ok=True)
    try:
        (work_item_dir / "status.yaml").write_text(
            "id: TASK-TEST-DEGRADE-01\ntype: task\nstate: blueprint\nrisk: low\n",
            encoding="utf-8",
        )
        # Verify no memory directory exists
        assert not (work_item_dir / "memory").exists()

        # Render prompt with this minimal work item
        rendered = render_agent_prompt(
            "software-engineer",
            work_item="work/agent_squad/TASK-TEST-DEGRADE-01",
            project_name="agent_squad",
        )
        assert rendered is not None
        assert "TASK-TEST-DEGRADE-01" in rendered
        assert "# STATUS DO WORK ITEM" in rendered
    finally:
        import shutil

        shutil.rmtree(work_item_dir, ignore_errors=True)


def test_no_unresolved_placeholders():
    """Assert rendered prompts contain no unresolved template placeholders."""
    placeholder_pattern = re.compile(r"\{\{[^}]+\}\}|<placeholder>|<TODO>|<INSERT_[^>]+>", re.IGNORECASE)

    test_agents = ("software-engineer", "solution-architect", "code-reviewer", "technical-writer")
    for agent_id in test_agents:
        rendered = render_agent_prompt(agent_id)
        matches = placeholder_pattern.findall(rendered)
        assert not matches, f"Unresolved placeholders found in {agent_id}: {matches}"


def test_portable_environment_paths():
    """Assert rendered environment paths use portable %SQUAD_RUNTIME% formatting."""
    work_item_dir = ROOT / "work" / "agent_squad" / "TASK-PORTABLE-ENV-01"
    work_item_dir.mkdir(parents=True, exist_ok=True)
    try:
        (work_item_dir / "status.yaml").write_text(
            "id: TASK-PORTABLE-ENV-01\ntype: task\nstate: blueprint\nrisk: low\n",
            encoding="utf-8",
        )

        rendered = render_agent_prompt(
            "software-engineer",
            work_item="work/agent_squad/TASK-PORTABLE-ENV-01",
            project_name="agent_squad",
        )

        # Environment details must exist and use %SQUAD_RUNTIME%
        assert "<environment_details>" in rendered
        assert "</environment_details>" in rendered

        env_block = rendered[rendered.index("<environment_details>") : rendered.index("</environment_details>")]
        assert "%SQUAD_RUNTIME%" in env_block

        # No absolute local Windows drive letters or UNIX absolute paths inside environment paths
        assert not re.search(r"Working directory:\s+[A-Za-z]:[\\/]", env_block)
        assert not re.search(r"Workspace root folder:\s+[A-Za-z]:[\\/]", env_block)
    finally:
        import shutil

        shutil.rmtree(work_item_dir, ignore_errors=True)
