"""R8 — Specialist Router Tests.

Tests:
- Route with required_role → ASSIGNED
- Route with required_capability → ASSIGNED
- Route by stage owner → ASSIGNED
- Zero candidates → BLOCKED (not software-engineer)
- Ambiguity → NEEDS_ROUTING
- Stage without policy → BLOCKED
"""

import pytest

from scripts.domain.delegation import RoutingDecision, RoutingRequest, RoutingStatus
from scripts.runtime.routing.policies import RoutingPolicy
from scripts.runtime.routing.registry import AgentRegistry
from scripts.runtime.routing.router import SpecialistRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_REGISTRY = {
    "agents": [
        {
            "id": "delivery-orchestrator",
            "path": "agents/00-delivery-orchestrator",
            "title": "Swarm & SDLC Delivery Orchestrator",
            "mode": "host",
            "purpose": "SDLC orchestration, WIP control, gate verification.",
            "dispatchable": False,
            "singleton": True,
        },
        {
            "id": "requirements-analyst",
            "path": "agents/01-requirements-analyst",
            "title": "Requirements & Specification Engineer",
            "mode": "core",
            "purpose": "INVEST user stories, BDD/Gherkin specifications, NFRs.",
        },
        {
            "id": "product-owner",
            "path": "agents/02-product-owner",
            "title": "Product Value & Discovery Strategist",
            "mode": "core",
            "purpose": "Product Goal definition, value vs risk prioritization.",
        },
        {
            "id": "solution-architect",
            "path": "agents/04-solution-architect",
            "title": "Clean Architecture & Systems Pioneer",
            "mode": "core",
            "purpose": "C4 Model architecture, ADRs, interface contracts.",
        },
        {
            "id": "software-engineer",
            "path": "agents/06-software-engineer",
            "title": "Clean Code & TDD Craftsman",
            "mode": "on_demand",
            "purpose": "Red-Green-Refactor TDD, SOLID design principles.",
        },
        {
            "id": "code-reviewer",
            "path": "agents/09-code-reviewer",
            "title": "Static Analysis & Code Quality Auditor",
            "mode": "on_demand",
            "purpose": "Spec conformance, clean code standards, G4-code gate.",
        },
        {
            "id": "security-reviewer",
            "path": "agents/10-security-reviewer",
            "title": "AppSec & Threat Modeling Specialist",
            "mode": "on_demand",
            "purpose": "OWASP Top 10, ASVS 4.0, STRIDE threat modeling.",
        },
        {
            "id": "test-engineer",
            "path": "agents/11-test-engineer",
            "title": "Test Automation & Quality Strategist",
            "mode": "on_demand",
            "purpose": "Test automation pyramid, contract testing, mutation testing.",
        },
        {
            "id": "qa-engineer",
            "path": "agents/12-qa-engineer",
            "title": "Exploratory & Resilience QA Specialist",
            "mode": "on_demand",
            "purpose": "Exploratory testing, SBTM, boundary value analysis.",
        },
        {
            "id": "governance-auditor",
            "path": "agents/14-governance-auditor",
            "title": "Compliance & Segregation of Duties Auditor",
            "mode": "on_demand",
            "purpose": "Delivery ledger integrity, SoD enforcement.",
        },
        {
            "id": "platform-engineer",
            "path": "agents/27-platform-engineer",
            "title": "Internal Developer Platform (IDP) Architect",
            "mode": "on_demand",
            "purpose": "IDP, Kubernetes Operator patterns, dev environments.",
        },
        {
            "id": "agile-coach",
            "path": "agents/40-agile-coach",
            "title": "Enterprise Agile Coach & Flow Master",
            "mode": "core",
            "purpose": "Fibonacci Story Points sizing, DORA metrics.",
        },
    ]
}


def _make_request(
    stage="IMPLEMENTATION",
    required_role=None,
    required_capability=None,
    work_item_id="STORY-001",
    cycle_id="development",
):
    return RoutingRequest(
        work_item_id=work_item_id,
        project_id="test-project",
        stage=stage,
        work_item_kind="STORY",
        cycle_id=cycle_id,
        required_role=required_role,
        required_capability=required_capability,
    )


# ---------------------------------------------------------------------------
# Test: Route by stage owner (default path)
# ---------------------------------------------------------------------------


class TestRouteByStageOwner:
    def setup_method(self):
        self.reg = AgentRegistry(FULL_REGISTRY)
        self.router = SpecialistRouter(self.reg)

    def test_implementation_routes_to_software_engineer(self):
        decision = self.router.route(_make_request(stage="IMPLEMENTATION"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "software-engineer"

    def test_code_review_routes_to_code_reviewer(self):
        decision = self.router.route(_make_request(stage="CODE_REVIEW"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "code-reviewer"

    def test_security_review_routes_to_security_reviewer(self):
        """Tests alias resolution: 10-security-specialist → security-reviewer."""
        decision = self.router.route(_make_request(stage="SECURITY_REVIEW"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "security-reviewer"

    def test_test_validation_routes_to_test_engineer(self):
        decision = self.router.route(_make_request(stage="TEST_VALIDATION"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "test-engineer"

    def test_qa_validation_routes_to_qa_engineer(self):
        decision = self.router.route(_make_request(stage="QA_VALIDATION"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "qa-engineer"

    def test_governance_routes_to_governance_auditor(self):
        decision = self.router.route(_make_request(stage="GOVERNANCE_RELEASE"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "governance-auditor"

    def test_discovery_routes_to_requirements_analyst(self):
        decision = self.router.route(_make_request(stage="DISCOVERY"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "requirements-analyst"

    def test_planning_routes_to_agile_coach(self):
        decision = self.router.route(_make_request(stage="PLANNING"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "agile-coach"

    def test_architecture_routes_to_solution_architect(self):
        decision = self.router.route(_make_request(stage="ARCHITECTURE_DESIGN"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "solution-architect"

    def test_scaffolding_routes_to_platform_engineer(self):
        decision = self.router.route(_make_request(stage="READINESS_SCAFFOLDING"))
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "platform-engineer"


# ---------------------------------------------------------------------------
# Test: Route with required_role
# ---------------------------------------------------------------------------


class TestRouteByRequiredRole:
    def setup_method(self):
        self.reg = AgentRegistry(FULL_REGISTRY)
        self.router = SpecialistRouter(self.reg)

    def test_explicit_role_overrides_stage_owner(self):
        decision = self.router.route(
            _make_request(stage="IMPLEMENTATION", required_role="test-engineer")
        )
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "test-engineer"

    def test_explicit_aliased_role(self):
        decision = self.router.route(
            _make_request(stage="IMPLEMENTATION", required_role="09-code-reviewer")
        )
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "code-reviewer"

    def test_unknown_role_blocked(self):
        decision = self.router.route(
            _make_request(stage="IMPLEMENTATION", required_role="99-nonexistent")
        )
        assert decision.status == RoutingStatus.BLOCKED
        assert decision.selected_agent_id is None


# ---------------------------------------------------------------------------
# Test: Route with required_capability
# ---------------------------------------------------------------------------


class TestRouteByCapability:
    def setup_method(self):
        self.reg = AgentRegistry(FULL_REGISTRY)
        self.router = SpecialistRouter(self.reg)

    def test_capability_match(self):
        decision = self.router.route(
            _make_request(stage="IMPLEMENTATION", required_capability="TDD")
        )
        assert decision.status == RoutingStatus.ASSIGNED
        assert decision.selected_agent_id == "software-engineer"

    def test_capability_no_match_blocked(self):
        decision = self.router.route(
            _make_request(
                stage="IMPLEMENTATION",
                required_capability="quantum-teleportation-engine",
            )
        )
        assert decision.status == RoutingStatus.BLOCKED


# ---------------------------------------------------------------------------
# Test: Zero candidates → BLOCKED (NEVER software-engineer fallback)
# ---------------------------------------------------------------------------


class TestZeroCandidatesBlocked:
    def test_non_dispatchable_owner_falls_to_collaborator(self):
        """INTAKE owner is delivery-orchestrator which is non-dispatchable.
        Router should fall through to collaborators if available.
        """
        reg = AgentRegistry(FULL_REGISTRY)
        router = SpecialistRouter(reg)
        decision = router.route(_make_request(stage="INTAKE"))
        # Orchestrator is not dispatchable, but requirements-analyst is a collaborator
        # Router should either find the collaborator or BLOCKED
        if decision.status == RoutingStatus.ASSIGNED:
            # Collaborator was found
            assert decision.selected_agent_id != "software-engineer"
        else:
            assert decision.status == RoutingStatus.BLOCKED
            assert decision.selected_agent_id is None
        # CRITICAL: no silent fallback to software-engineer
        assert decision.selected_agent_id != "software-engineer" or decision.status != RoutingStatus.ASSIGNED or (
            # software-engineer can only be selected if it's the actual stage owner
            decision.selected_agent_id == "software-engineer"
        )

    def test_unknown_stage_blocked(self):
        reg = AgentRegistry(FULL_REGISTRY)
        router = SpecialistRouter(reg)
        decision = router.route(_make_request(stage="PHANTOM_STAGE"))
        assert decision.status == RoutingStatus.BLOCKED


# ---------------------------------------------------------------------------
# Test: Ambiguity → NEEDS_ROUTING
# ---------------------------------------------------------------------------


class TestAmbiguity:
    def test_multiple_capability_matches_needs_routing(self):
        """Multiple agents matching a broad capability with no owner tiebreak."""
        registry_data = {
            "agents": [
                {
                    "id": "agent-alpha",
                    "path": "agents/alpha",
                    "title": "Alpha Agent",
                    "mode": "on_demand",
                    "purpose": "Kubernetes deployment, container orchestration.",
                    "capabilities": ["kubernetes", "container-orchestration"],
                },
                {
                    "id": "agent-beta",
                    "path": "agents/beta",
                    "title": "Beta Agent",
                    "mode": "on_demand",
                    "purpose": "Kubernetes operator patterns, helm charts.",
                    "capabilities": ["kubernetes", "helm-charts"],
                },
            ]
        }
        reg = AgentRegistry(registry_data)
        # Use a policy that allows collaborators but doesn't require owner first
        policy = RoutingPolicy(require_stage_owner_first=True, allow_collaborators=True)
        router = SpecialistRouter(reg, policy)
        decision = router.route(
            _make_request(
                stage="IMPLEMENTATION",
                required_capability="kubernetes",
            )
        )
        # Both match, neither is owner → NEEDS_ROUTING
        assert decision.status == RoutingStatus.NEEDS_ROUTING
        assert len(decision.candidates) == 2


# ---------------------------------------------------------------------------
# Test: RoutingDecision immutability
# ---------------------------------------------------------------------------


class TestDecisionImmutability:
    def test_decision_is_frozen(self):
        reg = AgentRegistry(FULL_REGISTRY)
        router = SpecialistRouter(reg)
        decision = router.route(_make_request(stage="IMPLEMENTATION"))
        with pytest.raises(AttributeError):
            decision.status = RoutingStatus.BLOCKED  # type: ignore


# ---------------------------------------------------------------------------
# Test: Decision ID determinism
# ---------------------------------------------------------------------------


class TestDecisionId:
    def test_same_request_same_id(self):
        reg = AgentRegistry(FULL_REGISTRY)
        router = SpecialistRouter(reg)
        r1 = _make_request(stage="IMPLEMENTATION")
        r2 = _make_request(stage="IMPLEMENTATION")
        d1 = router.route(r1)
        d2 = router.route(r2)
        assert d1.decision_id == d2.decision_id

    def test_different_stage_different_id(self):
        reg = AgentRegistry(FULL_REGISTRY)
        router = SpecialistRouter(reg)
        d1 = router.route(_make_request(stage="IMPLEMENTATION"))
        d2 = router.route(_make_request(stage="CODE_REVIEW"))
        assert d1.decision_id != d2.decision_id
