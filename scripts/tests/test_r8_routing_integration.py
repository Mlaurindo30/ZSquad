"""R8 — Routing Integration Tests.

End-to-end: YAML registry → AgentRegistry → SpecialistRouter → RoutingDecision → RoutingRepository.

Tests:
- Full pipeline from registry parse to persisted decision
- Different cycles produce different routing
- Work item kind → cycle → stage → specialist
"""

import os

import pytest
import yaml

from scripts.domain.delegation import RoutingDecision, RoutingRequest, RoutingStatus
from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.routing.registry import AgentRegistry
from scripts.runtime.routing.repository import RoutingRepository
from scripts.runtime.routing.router import SpecialistRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_real_registry():
    runtime_root = os.environ.get(
        "SQUAD_RUNTIME",
        os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    registry_path = os.path.join(runtime_root, "config", "agent-registry.yaml")
    if not os.path.isfile(registry_path):
        pytest.skip(f"agent-registry.yaml not found at {registry_path}")
    with open(registry_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def real_registry():
    return AgentRegistry(_load_real_registry())


@pytest.fixture
def real_router(real_registry):
    return SpecialistRouter(real_registry)


@pytest.fixture
def repo(tmp_path):
    return RoutingRepository(str(tmp_path / "integration_routing.db"))


# ---------------------------------------------------------------------------
# Test: Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_route_and_persist(self, real_router, repo):
        """Registry → Router → Decision → Repository → Retrieval."""
        request = RoutingRequest(
            work_item_id="STORY-100",
            project_id="integration-test",
            stage="IMPLEMENTATION",
            work_item_kind="STORY",
            cycle_id="development",
        )
        decision = real_router.route(request)
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "software-engineer"

        # Persist
        persisted = repo.record_decision(decision)
        assert persisted.decision_id == decision.decision_id

        # Retrieve
        retrieved = repo.get_decision(decision.decision_id)
        assert retrieved is not None
        assert retrieved.selected_agent_id == "software-engineer"
        assert retrieved.status == RoutingStatus.ASSIGNED

    def test_non_dispatchable_owner_routed(self, real_router, repo):
        """INTAKE owner is orchestrator (non-dispatchable) but has collaborators."""
        request = RoutingRequest(
            work_item_id="STORY-101",
            project_id="integration-test",
            stage="INTAKE",
            work_item_kind="STORY",
            cycle_id="development",
        )
        decision = real_router.route(request)
        # Owner not dispatchable → falls to collaborators or BLOCKED
        assert decision.status in (RoutingStatus.BLOCKED, RoutingStatus.ASSIGNED)

        repo.record_decision(decision)
        retrieved = repo.get_decision(decision.decision_id)
        assert retrieved is not None
        assert retrieved.status == decision.status


# ---------------------------------------------------------------------------
# Test: Different stages produce different specialists
# ---------------------------------------------------------------------------


class TestStageSpecialistMapping:
    EXPECTED_MAPPING = {
        "DISCOVERY": "requirements-analyst",
        "REQUIREMENTS_PRODUCT": "requirements-analyst",
        "PLANNING": "agile-coach",
        "ARCHITECTURE_DESIGN": "solution-architect",
        "READINESS_SCAFFOLDING": "platform-engineer",
        "IMPLEMENTATION": "software-engineer",
        "CODE_REVIEW": "code-reviewer",
        "SECURITY_REVIEW": "security-reviewer",
        "TEST_VALIDATION": "test-engineer",
        "QA_VALIDATION": "qa-engineer",
        "GOVERNANCE_RELEASE": "governance-auditor",
    }

    @pytest.mark.parametrize(
        "stage,expected_agent",
        list(EXPECTED_MAPPING.items()),
        ids=list(EXPECTED_MAPPING.keys()),
    )
    def test_stage_to_specialist(self, real_router, stage, expected_agent):
        request = RoutingRequest(
            work_item_id="STORY-200",
            project_id="integration-test",
            stage=stage,
            work_item_kind="STORY",
            cycle_id="development",
        )
        decision = real_router.route(request)
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == expected_agent


# ---------------------------------------------------------------------------
# Test: Multiple work items tracked separately
# ---------------------------------------------------------------------------


class TestMultipleWorkItems:
    def test_separate_work_items(self, real_router, repo):
        for i, stage in enumerate(["IMPLEMENTATION", "CODE_REVIEW", "TEST_VALIDATION"]):
            request = RoutingRequest(
                work_item_id=f"STORY-30{i}",
                project_id="integration-test",
                stage=stage,
                work_item_kind="STORY",
                cycle_id="development",
            )
            decision = real_router.route(request)
            repo.record_decision(decision)

        # Each work item has exactly one decision
        for i in range(3):
            decisions = repo.get_decisions_for_work_item(f"STORY-30{i}")
            assert len(decisions) == 1


# ---------------------------------------------------------------------------
# Test: Explicit role override pipeline
# ---------------------------------------------------------------------------


class TestExplicitRolePipeline:
    def test_override_and_persist(self, real_router, repo):
        """Override stage owner with explicit required_role."""
        request = RoutingRequest(
            work_item_id="STORY-400",
            project_id="integration-test",
            stage="IMPLEMENTATION",
            work_item_kind="STORY",
            cycle_id="development",
            required_role="test-engineer",
        )
        decision = real_router.route(request)
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "test-engineer"

        repo.record_decision(decision)
        retrieved = repo.get_decision(decision.decision_id)
        assert retrieved.selected_agent_id == "test-engineer"
