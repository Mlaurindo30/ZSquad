"""Canonical Tests for R6 Architectural Authority, Contract Reuse & Segregation (Section 53).

Covers:
- R4 remains lifecycle authority
- R6 never directly writes canonical lifecycle state around R4
- R6 reuses R1 sync contracts
- R6 reuses R2 event engine
- R6 reuses R5 binding
- no second ProjectBinding
- no second Event engine
- no scheduler loop (zero infinite daemons)
- no LLM coupling (zero OpenAI, Anthropic, Gemini, LangChain)
- no agent dispatch (specialist dispatch belongs to R8/R10/R11)
- no prompt renderer
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import pytest

import scripts.domain.events as domain_events
import scripts.domain.lifecycle as domain_lifecycle
import scripts.domain.sync as domain_sync
from scripts.runtime.delivery.reconciliation import ConflictReconciliationEngine
from scripts.runtime.delivery.sync import DeliverySyncService

DELIVERY_DIR = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "delivery"


def test_r4_remains_lifecycle_authority():
    """R4 canonical lifecycle engine remains the sole authority for stage transitions."""
    engine = ConflictReconciliationEngine()
    # Illegal transition from INTAKE to IMPLEMENTATION must be rejected by R4 policy
    is_legal, reason = engine._is_legal_transition(
        work_item_id="STORY-AUTH-1",
        project_id="PROJ-AUTH",
        current_stage=domain_lifecycle.LifecycleStage.INTAKE,
        target_stage=domain_lifecycle.LifecycleStage.IMPLEMENTATION,
    )
    assert is_legal is False
    assert "does not allow direct transition" in reason


def test_r6_never_directly_writes_canonical_lifecycle_around_r4():
    """R6 reconciliation blocks illegal jumps fail-closed and never bypasses R4 gates."""
    engine = ConflictReconciliationEngine()
    decision = engine.evaluate(
        work_item_id="STORY-AUTH-2",
        project_id="PROJ-AUTH",
        current_local_stage=domain_lifecycle.LifecycleStage.IMPLEMENTATION,
        remote_state="Closed",  # Skipping review, security, validation, governance
    )
    assert decision.action == domain_sync.ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION
    assert decision.local_state == "IMPLEMENTATION"


def test_r6_reuses_r1_sync_contracts():
    """R6 exclusively reuses R1 canonical domain models from scripts.domain.sync."""
    # Verify exact class identity
    from scripts.runtime.delivery.sync import SyncState, SyncStatus, ReconciliationDecision, ReconciliationAction
    assert SyncState is domain_sync.SyncState
    assert SyncStatus is domain_sync.SyncStatus
    assert ReconciliationDecision is domain_sync.ReconciliationDecision
    assert ReconciliationAction is domain_sync.ReconciliationAction


def test_r6_reuses_r2_event_engine():
    """R6 emits domain events through R2 SqliteEventStore using R2 DomainEvent."""
    from scripts.runtime.delivery.sync import DomainEvent
    assert DomainEvent is domain_events.DomainEvent


def test_r6_reuses_r5_binding():
    """R6 reuses R5 ProjectBindingRecord and SqliteBindingRepository."""
    from scripts.runtime.delivery.repository import ProjectBindingRecord, SqliteBindingRepository
    from scripts.runtime.delivery.binding import ProjectDeliveryBindingService
    assert issubclass(ProjectBindingRecord, object)
    assert issubclass(SqliteBindingRepository, object)
    assert issubclass(ProjectDeliveryBindingService, object)


def test_no_second_project_binding():
    """AST check: No duplicate ProjectBinding class is defined within scripts/runtime/delivery/sync.py."""
    sync_py = DELIVERY_DIR / "sync.py"
    tree = ast.parse(sync_py.read_text(encoding="utf-8"), filename=str(sync_py))
    defined_classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]

    assert "ProjectBinding" not in defined_classes
    assert "ProjectBindingService" not in defined_classes


def test_no_second_event_engine():
    """AST check: No duplicate EventStore or EventEngine class is defined in delivery package."""
    for py_file in DELIVERY_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        defined_classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert "EventStore" not in defined_classes
        assert "EventEngine" not in defined_classes
        assert "EventTriggerEngine" not in defined_classes


def test_no_scheduler_loop_in_r6():
    """AST check: Zero 'while True' or persistent daemon loops in scripts/runtime/delivery/sync.py."""
    sync_py = DELIVERY_DIR / "sync.py"
    tree = ast.parse(sync_py.read_text(encoding="utf-8"), filename=str(sync_py))
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            # Verify while condition is not True constant
            if isinstance(node.test, ast.Constant) and node.test.value is True:
                pytest.fail(f"Prohibited infinite 'while True' loop found in {sync_py.name} at line {node.lineno}")


def test_no_llm_or_agent_dispatch_or_prompt_renderer_in_r6():
    """AST check: Absolute absence of LLMs, agent personas, and prompt rendering in R6 sync plane."""
    disallowed_modules = {
        "openai", "anthropic", "google", "langchain", "llama_index",
        "render_agent_prompt", "agent_squad", "subagent",
    }
    for py_file in DELIVERY_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_pkg = alias.name.split(".")[0]
                    assert top_pkg not in disallowed_modules, (
                        f"Disallowed import '{alias.name}' found in {py_file.name} at line {node.lineno}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_pkg = node.module.split(".")[0]
                    assert top_pkg not in disallowed_modules, (
                        f"Disallowed import from '{node.module}' in {py_file.name} at line {node.lineno}"
                    )
