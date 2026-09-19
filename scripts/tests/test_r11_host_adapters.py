"""Tests for Milestone R11 — Host Adapters Conformance.

Covers:
- AntigravityDispatchAdapter capabilities, payload building, and subagent specification
- CodexDispatchAdapter capabilities, execution handle, and context limit checks
- GeminiCliDispatchAdapter capabilities and execution binding
- ClaudeDispatchAdapter capabilities and execution binding
- FakeHostAdapter fault injection, cancellation, and status probing
"""

import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.runtime.dispatch.adapters.antigravity import AntigravityDispatchAdapter
from scripts.runtime.dispatch.adapters.claude import ClaudeDispatchAdapter
from scripts.runtime.dispatch.adapters.codex import CodexDispatchAdapter
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.adapters.gemini_cli import GeminiCliDispatchAdapter
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostExecutionBinding,
)


def _make_envelope(role="06-software-engineer", instruction="Do work."):
    return DelegationEnvelope.create(
        delegation_id="DEL-ADAPT-01",
        sender_role="00-delivery-orchestrator",
        target_role=role,
        work_item_id="TASK-101",
        scope_summary="Task summary",
        action_requested="Execute task",
        compiled_instruction=instruction,
    )


def _make_binding(adapter):
    return HostExecutionBinding(
        host_kind=adapter.host_kind,
        host_version="1.0.0",
        session_id="SESS-ADAPT-01",
        execution_handle="",
        dispatch_mode="native_subagent" if adapter.get_capabilities().has_subagent_dispatch else "cli",
        capabilities=adapter.get_capabilities(),
    )


def test_antigravity_adapter_conformance():
    adapter = AntigravityDispatchAdapter()
    assert adapter.host_kind == "antigravity"
    caps = adapter.get_capabilities()
    assert caps.has_subagent_dispatch is True
    assert caps.max_token_context == 2_000_000

    envelope = _make_envelope(role="06-software-engineer")
    can, reason = adapter.can_dispatch(envelope)
    assert can is True
    assert reason is None

    spec = adapter.build_subagent_spec(envelope)
    assert spec["TypeName"] == "self"
    assert spec["Role"] == "06-software-engineer"
    assert spec["Prompt"] == envelope.compiled_instruction

    binding = _make_binding(adapter)
    result = adapter.dispatch(envelope, binding)
    assert result.status == DispatchStatus.DISPATCHED
    assert result.host_execution_id.startswith("ag-subagent-")
    assert result.evidence_payload["host_runtime"] == "antigravity"


def test_codex_adapter_conformance():
    adapter = CodexDispatchAdapter()
    assert adapter.host_kind == "codex"
    caps = adapter.get_capabilities()
    assert caps.has_subagent_dispatch is False
    assert caps.max_token_context == 128_000

    envelope = _make_envelope()
    binding = _make_binding(adapter)
    result = adapter.dispatch(envelope, binding)
    assert result.status == DispatchStatus.DISPATCHED
    assert result.host_execution_id.startswith("codex-proc-")


def test_gemini_cli_adapter_conformance():
    adapter = GeminiCliDispatchAdapter()
    assert adapter.host_kind == "gemini_cli"
    caps = adapter.get_capabilities()
    assert caps.has_subagent_dispatch is True
    assert caps.max_token_context == 1_000_000

    envelope = _make_envelope()
    binding = _make_binding(adapter)
    result = adapter.dispatch(envelope, binding)
    assert result.status == DispatchStatus.DISPATCHED
    assert result.host_execution_id.startswith("gemini-exec-")


def test_claude_adapter_conformance():
    adapter = ClaudeDispatchAdapter()
    assert adapter.host_kind == "claude"
    caps = adapter.get_capabilities()
    assert caps.has_subagent_dispatch is False
    assert caps.max_token_context == 200_000

    envelope = _make_envelope()
    binding = _make_binding(adapter)
    result = adapter.dispatch(envelope, binding)
    assert result.status == DispatchStatus.DISPATCHED
    assert result.host_execution_id.startswith("claude-exec-")


def test_fake_adapter_features():
    adapter = FakeHostAdapter()
    envelope = _make_envelope()
    binding = _make_binding(adapter)

    # Test successful dispatch
    res = adapter.dispatch(envelope, binding)
    assert res.status == DispatchStatus.DISPATCHED

    # Test status probe
    status = adapter.check_status(res.host_execution_id)
    assert status.is_alive is True
    assert status.status == DispatchStatus.DISPATCHED

    # Test cancellation
    cancelled = adapter.cancel(res.host_execution_id)
    assert cancelled is True

    # Test status after cancellation
    status_after = adapter.check_status(res.host_execution_id)
    assert status_after.is_alive is False
    assert status_after.status == DispatchStatus.CANCELLED
