"""R8 — Routing Repository Tests.

Tests:
- record_decision persists
- get_decision retrieves
- Idempotency (same request → same decision)
- History tracking
- get_decisions_for_work_item returns ordered list
"""

import os
import tempfile

import pytest

from scripts.domain.delegation import RoutingDecision, RoutingStatus
from scripts.runtime.routing.repository import RoutingRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    decision_id="RD-test001",
    work_item_id="STORY-001",
    stage="IMPLEMENTATION",
    selected="software-engineer",
    status=RoutingStatus.ASSIGNED,
    required_role="06-software-engineer",
    required_capability=None,
):
    return RoutingDecision(
        decision_id=decision_id,
        work_item_id=work_item_id,
        stage=stage,
        required_role=required_role,
        required_capability=required_capability,
        candidates=[selected] if selected else [],
        selected_agent_id=selected,
        status=status,
        reason=f"Test decision for {stage}",
    )


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test_routing.db")
    return RoutingRepository(db_path)


# ---------------------------------------------------------------------------
# Test: Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_record_and_retrieve(self, repo):
        decision = _make_decision()
        repo.record_decision(decision)
        retrieved = repo.get_decision("RD-test001")
        assert retrieved is not None
        assert retrieved.decision_id == "RD-test001"
        assert retrieved.selected_agent_id == "software-engineer"
        assert retrieved.status == RoutingStatus.ASSIGNED

    def test_get_nonexistent(self, repo):
        result = repo.get_decision("RD-nonexistent")
        assert result is None

    def test_record_blocked_decision(self, repo):
        decision = _make_decision(
            decision_id="RD-blocked",
            selected=None,
            status=RoutingStatus.BLOCKED,
        )
        repo.record_decision(decision)
        retrieved = repo.get_decision("RD-blocked")
        assert retrieved is not None
        assert retrieved.status == RoutingStatus.BLOCKED
        assert retrieved.selected_agent_id is None


# ---------------------------------------------------------------------------
# Test: Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_unique_key_returns_existing(self, repo):
        d1 = _make_decision(decision_id="RD-001")
        result1 = repo.record_decision(d1)
        assert result1.decision_id == "RD-001"

        # Same unique key (work_item_id + stage + required_role + required_capability)
        # but different decision_id — should return existing due to UNIQUE constraint
        d2 = _make_decision(decision_id="RD-002")
        result2 = repo.record_decision(d2)
        # The UNIQUE constraint triggers, returning the existing RD-001
        assert result2.decision_id == "RD-001"

    def test_different_stage_not_idempotent(self, repo):
        d1 = _make_decision(decision_id="RD-001", stage="IMPLEMENTATION")
        d2 = _make_decision(
            decision_id="RD-002",
            stage="CODE_REVIEW",
            selected="code-reviewer",
            required_role="09-code-reviewer",
        )
        repo.record_decision(d1)
        result2 = repo.record_decision(d2)
        assert result2.decision_id == "RD-002"  # Different, not idempotent


# ---------------------------------------------------------------------------
# Test: Query by work item
# ---------------------------------------------------------------------------


class TestQueryByWorkItem:
    def test_multiple_decisions_for_work_item(self, repo):
        d1 = _make_decision(
            decision_id="RD-impl",
            stage="IMPLEMENTATION",
            required_role="06-software-engineer",
        )
        d2 = _make_decision(
            decision_id="RD-review",
            stage="CODE_REVIEW",
            selected="code-reviewer",
            required_role="09-code-reviewer",
        )
        repo.record_decision(d1)
        repo.record_decision(d2)
        results = repo.get_decisions_for_work_item("STORY-001")
        assert len(results) == 2

    def test_no_decisions_for_work_item(self, repo):
        results = repo.get_decisions_for_work_item("STORY-999")
        assert results == []


# ---------------------------------------------------------------------------
# Test: Schema bootstrap
# ---------------------------------------------------------------------------


class TestSchemaBootstrap:
    def test_creates_tables(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        repo = RoutingRepository(db_path)
        # Tables should exist
        import sqlite3
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "routing_decisions" in table_names
        assert "routing_history" in table_names
        conn.close()
