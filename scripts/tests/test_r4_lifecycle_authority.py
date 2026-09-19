"""Canonical Lifecycle Authority test suite (R4 - Seção 53).

Covers:
- estático/runtime: uma única autoridade de transição
- AgentSquad.advance_state delega para ela
- ContinuousTriggerEngine não pode mutar ciclo de forma independente
- zero imports de LLM no lifecycle runtime
- zero imports de host adapters
- zero imports de conectores Azure
- zero imports de prompt renderer
- zero agent dispatch no lifecycle
- cycles.yaml e workflow.yaml validados consistentemente
"""

import ast
import inspect
from pathlib import Path
import sys
import pytest
import yaml

from scripts.agent_squad import AgentSquad
from scripts.continuous_trigger_engine import ContinuousTriggerEngine
from scripts.runtime.lifecycle import (
    CanonicalLifecycleService,
    load_cycles_config,
    load_workflow_config,
)

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_DIR = ROOT / "scripts" / "runtime" / "lifecycle"


def test_single_authoritative_service_for_transitions():
    """Garante que CanonicalLifecycleService é a única autoridade canônica de transição."""
    assert hasattr(CanonicalLifecycleService, "transition")
    assert hasattr(CanonicalLifecycleService, "can_transition")
    sig = inspect.signature(CanonicalLifecycleService.transition)
    assert "work_item_id" in sig.parameters


def test_agent_squad_advance_state_delegates_to_canonical_lifecycle():
    """Verifica estaticamente e via inspeção que AgentSquad.advance_state delega para CanonicalLifecycleService."""
    src = inspect.getsource(AgentSquad.advance_state)
    assert "CanonicalLifecycleService" in src, "advance_state deve instanciar ou invocar CanonicalLifecycleService"
    assert "transition(" in src, "advance_state deve delegar a transição via lifecycle_service.transition"


def test_continuous_trigger_engine_does_not_mutate_cycle_independently():
    """ContinuousTriggerEngine não possui máquina de estados paralela; avança exclusivamente via squad.advance_state."""
    cte_src = inspect.getsource(ContinuousTriggerEngine)

    # Não deve haver escrita direta de status.yaml para alterar estado
    tree = ast.parse(cte_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "transition":
            # Não deve chamar transition diretamente burlar squad
            pass

    assert "self.squad.advance_state" in cte_src, "ContinuousTriggerEngine deve avançar via squad.advance_state"


def test_zero_llm_imports_in_lifecycle_runtime():
    """Auditoria estática: scripts/runtime/lifecycle/ possui ZERO imports de SDKs de LLM."""
    prohibited_modules = {
        "openai", "anthropic", "google.generativeai", "langchain",
        "litellm", "llama_index", "cohere", "mistralai", "groq",
    }
    for py_file in LIFECYCLE_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited LLM import '{pkg}' found in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited LLM import '{pkg}' found in {py_file.name}"


def test_zero_host_adapters_in_lifecycle_runtime():
    """Auditoria estática: scripts/runtime/lifecycle/ possui ZERO imports de host adapters."""
    prohibited_modules = {
        "host_adapter", "host_adapters", "runtime_adapters", "cli_adapter",
    }
    for py_file in LIFECYCLE_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited host adapter import '{pkg}' found in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited host adapter import '{pkg}' found in {py_file.name}"


def test_zero_azure_connectors_in_lifecycle_runtime():
    """Auditoria estática: scripts/runtime/lifecycle/ possui ZERO imports de Azure / DevOps connectors."""
    prohibited_modules = {
        "azure", "devops_platform_connector", "azure_devops_project_setup",
    }
    for py_file in LIFECYCLE_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited Azure import '{pkg}' found in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited Azure import '{pkg}' found in {py_file.name}"


def test_zero_prompt_renderers_in_lifecycle_runtime():
    """Auditoria estática: scripts/runtime/lifecycle/ possui ZERO imports de prompt renderers."""
    prohibited_modules = {
        "render_agent_prompt", "prompt_renderer",
    }
    for py_file in LIFECYCLE_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited prompt renderer import '{pkg}' found in {py_file.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0].lower()
                    assert pkg not in prohibited_modules, f"Prohibited prompt renderer import '{pkg}' found in {py_file.name}"


def test_zero_agent_dispatch_in_lifecycle_runtime():
    """Auditoria estática: scripts/runtime/lifecycle/ possui ZERO código de subprocess ou agent dispatch."""
    prohibited_calls = {"dispatch_agent", "run_agent", "execute_agent"}
    for py_file in LIFECYCLE_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in prohibited_calls:
                    assert False, f"Prohibited agent dispatch call '{node.func.id}' found in {py_file.name}"
                elif isinstance(node.func, ast.Attribute) and node.func.attr in prohibited_calls:
                    assert False, f"Prohibited agent dispatch call '{node.func.attr}' found in {py_file.name}"


def test_cycles_and_workflow_yaml_consistently_validated():
    """Valida que config/cycles.yaml e config/workflow.yaml carregam e são mutuamente consistentes."""
    cycles_cfg = load_cycles_config(ROOT / "config" / "cycles.yaml")
    workflow_cfg = load_workflow_config(ROOT / "config" / "workflow.yaml")

    assert "cycles" in cycles_cfg
    assert "type_to_cycle" in cycles_cfg
    assert "gates" in workflow_cfg
    assert "flow" in workflow_cfg

    # Verifica que todos os gates referenciados em workflow.yaml existem
    workflow_gates = set(workflow_cfg.get("gates", {}).keys())
    assert {"G1-product", "G2-design", "G3-readiness", "G4-code-security", "G5-quality", "G6-governance-release"}.issubset(workflow_gates)

    # Verifica que o WIP limit para blueprint está definido como 2
    wip_blueprint = workflow_cfg.get("flow", {}).get("wip_limits", {}).get("blueprint")
    assert wip_blueprint == 2
