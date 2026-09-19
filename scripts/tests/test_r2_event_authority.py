"""Canonical Test Suite for R2 Event Authority and Architectural Invariants (Section 43).

Covers:
- Static/runtime check: event engine does NOT import Azure DevOps connectors
- Event engine does NOT import MCP server modules
- Event engine does NOT import prompt renderers
- Event engine does NOT import host adapters
- Event engine does NOT import LLM providers (OpenAI, Anthropic, Gemini, Ollama, etc.)
- Event engine does NOT call advance_state or decide_gate
- Event engine does NOT dispatch subagents or subprocesses
- Event engine does NOT implement infinite scheduler/cron loops
- Event engine strictly imports and utilizes canonical domain contracts from scripts/domain/
"""

import ast
from pathlib import Path
import pytest

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
    RetryPolicy,
    TriggerActionKind,
    TriggerPolicy,
)
import scripts.runtime.events as events_runtime_pkg

EVENTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "events"


class TestR2EventAuthorityAndInvariants:
    """Rigorous verification of Hexagonal boundaries, SoD, and absence of prohibited dependencies."""

    def test_zero_azure_connector_imports(self):
        """Validates that scripts/runtime/events/ does not import Azure DevOps connectors or SDKs."""
        forbidden = {"azure", "azure_devops", "azure_devops_connector"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )

    def test_zero_mcp_server_imports(self):
        """Validates that scripts/runtime/events/ does not import MCP server packages."""
        forbidden = {"mcp", "agent_squad_mcp", "server", "fastmcp"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )

    def test_zero_prompt_renderer_imports(self):
        """Validates that scripts/runtime/events/ does not import agent prompt renderers."""
        forbidden = {"render_agent_prompt", "prompt_renderer", "jinja2"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )

    def test_zero_host_adapters_imports(self):
        """Validates that scripts/runtime/events/ does not import host adapter layers."""
        forbidden = {"adapters", "host_adapters", "cli_adapter"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )

    def test_zero_llm_providers_imports(self):
        """Validates that scripts/runtime/events/ does not import any LLM providers or frameworks."""
        forbidden = {
            "openai",
            "anthropic",
            "google",
            "gemini",
            "langchain",
            "langchain_core",
            "langgraph",
            "litellm",
            "ollama",
            "transformers",
        }

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )

    def test_event_engine_does_not_call_advance_state_or_decide_gate(self):
        """Validates that scripts/runtime/events/ contains zero calls or references to advance_state or decide_gate."""
        prohibited_calls = {"advance_state", "decide_gate"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    func_name = None
                    if isinstance(func, ast.Name):
                        func_name = func.id
                    elif isinstance(func, ast.Attribute):
                        func_name = func.attr
                    assert func_name not in prohibited_calls, (
                        f"{py_file.name} illegally calls '{func_name}'"
                    )

    def test_event_engine_does_not_dispatch_agents(self):
        """Validates that scripts/runtime/events/ does not instantiate or invoke subagent processes or threads."""
        forbidden_calls = {"invoke_subagent", "dispatch_agent", "start_session", "run_continuous"}
        forbidden_modules = {"subprocess", "multiprocessing", "threading"}

        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in forbidden_modules, (
                            f"{py_file.name} illegally imports '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module.split(".")[0] not in forbidden_modules, (
                            f"{py_file.name} illegally imports from '{node.module}'"
                        )
                elif isinstance(node, ast.Call):
                    func = node.func
                    func_name = getattr(func, "id", None) or getattr(func, "attr", None)
                    assert func_name not in forbidden_calls, (
                        f"{py_file.name} illegally calls dispatch function '{func_name}'"
                    )

    def test_event_engine_does_not_implement_scheduler_cron_loop(self):
        """Validates that scripts/runtime/events/ exposes synchronous methods without infinite loops or sleep delays."""
        for py_file in EVENTS_DIR.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                # Detect while True loops
                if isinstance(node, ast.While):
                    test_node = node.test
                    if isinstance(test_node, ast.Constant) and test_node.value is True:
                        pytest.fail(f"Found infinite 'while True' loop in {py_file.name}")
                # Detect time.sleep calls
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "sleep":
                        pytest.fail(f"Found blocking sleep call in {py_file.name}")

    def test_event_engine_imports_canonical_domain_contracts(self):
        """Validates that scripts/runtime/events/ cleanly consumes canonical domain models."""
        engine_file = EVENTS_DIR / "engine.py"
        content = engine_file.read_text(encoding="utf-8")
        assert "from scripts.domain.events import" in content
        assert "DomainEvent" in content
        assert "EventDelivery" in content
        assert "RetryPolicy" in content
        assert "DeliveryStatus" in content

        store_file = EVENTS_DIR / "store.py"
        store_content = store_file.read_text(encoding="utf-8")
        assert "from scripts.domain.events import" in store_content
        assert "from scripts.domain.common import canonical_hash, canonical_json" in store_content
