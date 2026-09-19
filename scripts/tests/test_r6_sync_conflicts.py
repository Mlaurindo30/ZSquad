"""Canonical Tests for R6 Sync Conflicts & Deterministic Drift Resolution (Section 51).

Covers:
- local/remote same -> NOOP
- local newer -> APPLY_LOCAL_TO_REMOTE when policy allows
- remote newer -> APPLY_REMOTE_TO_LOCAL when legal
- both changed -> CONFLICT_REQUIRES_RESOLUTION
- illegal remote lifecycle -> BLOCK_ILLEGAL_REMOTE_TRANSITION
- no last-write-wins default (strict LWW prohibition)
"""

from __future__ import annotations

import pytest

from scripts.domain.lifecycle import LifecycleStage
from scripts.domain.sync import (
    ReconciliationAction,
    ReconciliationDecision,
)
from scripts.runtime.delivery.reconciliation import ConflictReconciliationEngine
from scripts.runtime.events.store import SqliteEventStore


@pytest.fixture
def store() -> SqliteEventStore:
    return SqliteEventStore(":memory:")


@pytest.fixture
def engine(store: SqliteEventStore) -> ConflictReconciliationEngine:
    return ConflictReconciliationEngine(event_store=store)


def test_conflict_local_and_remote_same_noop(engine: ConflictReconciliationEngine):
    """When both local and remote are in the identical canonical stage, result is NOOP."""
    decision = engine.evaluate(
        work_item_id="STORY-201",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
        remote_state="Active",
        remote_board_column="Implementation",
        local_has_unpushed_changes=False,
        remote_rev_changed=False,
    )

    assert decision.action == ReconciliationAction.NOOP
    assert "identical" in decision.reason.lower() or "synchronized" in decision.reason.lower()


def test_conflict_local_newer_applies_local_to_remote(engine: ConflictReconciliationEngine):
    """When local advanced legally and remote is unchanged, result is APPLY_LOCAL_TO_REMOTE."""
    decision = engine.evaluate(
        work_item_id="STORY-202",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.CODE_REVIEW,
        remote_state="Active",
        remote_board_column="Implementation",
        local_has_unpushed_changes=True,
        remote_rev_changed=False,
    )

    assert decision.action == ReconciliationAction.APPLY_LOCAL_TO_REMOTE
    assert decision.local_state == "CODE_REVIEW"
    assert decision.remote_state == "IMPLEMENTATION"


def test_conflict_remote_newer_applies_remote_to_local(engine: ConflictReconciliationEngine):
    """When remote legally advanced and local is unchanged, result is APPLY_REMOTE_TO_LOCAL."""
    # Remote moves card from Implementation to Code Review legally
    decision = engine.evaluate(
        work_item_id="STORY-203",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
        remote_state="Active",
        remote_board_column="Code Review",
        local_has_unpushed_changes=False,
        remote_rev_changed=True,
    )

    assert decision.action == ReconciliationAction.APPLY_REMOTE_TO_LOCAL
    assert decision.remote_state == "CODE_REVIEW"


def test_conflict_both_changed_requires_resolution(engine: ConflictReconciliationEngine):
    """When local has unpushed edits AND remote also moved legally, result is CONFLICT_REQUIRES_RESOLUTION."""
    # Local has unpushed changes while in IMPLEMENTATION, and remote concurrently moved card legally to Code Review
    decision = engine.evaluate(
        work_item_id="STORY-204",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
        remote_state="Active",
        remote_board_column="Code Review",
        local_has_unpushed_changes=True,
        remote_rev_changed=True,
    )

    assert decision.action == ReconciliationAction.CONFLICT_REQUIRES_RESOLUTION
    assert "concurrent" in decision.reason.lower() or "human resolution" in decision.reason.lower()


def test_conflict_illegal_remote_lifecycle_blocks_transition(engine: ConflictReconciliationEngine):
    """When remote attempts an illegal leap violating R4, result is BLOCK_ILLEGAL_REMOTE_TRANSITION."""
    # Attempting to move straight from BLUEPRINT/INTAKE to DONE
    decision = engine.evaluate(
        work_item_id="STORY-205",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.INTAKE,
        remote_state="Closed",
        remote_board_column="Done",
        local_has_unpushed_changes=False,
        remote_rev_changed=True,
    )

    assert decision.action == ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION
    assert "illegal" in decision.reason.lower()


def test_no_last_write_wins_default(engine: ConflictReconciliationEngine):
    """The system strictly forbids blind Last-Write-Wins (LWW) and never auto-overwrites on conflict."""
    # Invariant: ReconciliationAction enum does not possess LAST_WRITE_WINS
    action_names = [a.name for a in ReconciliationAction]
    assert "LAST_WRITE_WINS" not in action_names
    assert "FORCE_OVERWRITE" not in action_names

    # Concurrent divergence with local having unpushed changes always resolves to CONFLICT_REQUIRES_RESOLUTION
    decision = engine.evaluate(
        work_item_id="STORY-206",
        project_id="PROJ-TEST",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
        remote_state="Active",
        remote_board_column="Code Review",
        local_has_unpushed_changes=True,
        remote_rev_changed=True,
    )

    assert decision.action == ReconciliationAction.CONFLICT_REQUIRES_RESOLUTION
    assert decision.action != ReconciliationAction.APPLY_REMOTE_TO_LOCAL
    assert decision.action != ReconciliationAction.APPLY_LOCAL_TO_REMOTE
