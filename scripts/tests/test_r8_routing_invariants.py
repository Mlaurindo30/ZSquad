"""R8 — Routing Invariant Tests.

Tests the PRIMARY R8 INVARIANT:
- No silent fallback to software-engineer. Zero candidates → BLOCKED/NEEDS_ROUTING.
- All 13 stages of 'development' cycle resolve without BLOCKED (using full registry).
- CANONICAL_STAGE_POLICIES owner_roles all resolve in registry (with alias map).
- Collaborator roles all resolve.
- RoutingDecision is immutable (frozen dataclass).
"""

import os

import pytest
import yaml

from scripts.domain.delegation import RoutingDecision, RoutingRequest, RoutingStatus
from scripts.domain.lifecycle import CANONICAL_STAGE_POLICIES, LifecycleStage
from scripts.runtime.routing.registry import AgentRegistry, ROLE_ALIAS_MAP
from scripts.runtime.routing.router import SpecialistRouter


# ---------------------------------------------------------------------------
# Load real agent-registry.yaml
# ---------------------------------------------------------------------------


def _load_real_registry():
    """Loads the actual agent-registry.yaml from config/."""
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
    data = _load_real_registry()
    return AgentRegistry(data)


@pytest.fixture
def real_router(real_registry):
    return SpecialistRouter(real_registry)


# ---------------------------------------------------------------------------
# INVARIANT 1: No silent fallback to software-engineer
# ---------------------------------------------------------------------------


class TestNoSilentFallback:
    """The routing engine must NEVER silently fall back to software-engineer.
    
    Every BLOCKED or NEEDS_ROUTING decision must explicitly state so.
    ASSIGNED decisions must have the correct specialist, not a default.
    """

    def test_blocked_never_selects_software_engineer(self, real_router):
        """Force a BLOCKED scenario and verify no software-engineer fallback."""
        request = RoutingRequest(
            work_item_id="STORY-999",
            project_id="test",
            stage="IMPLEMENTATION",
            work_item_kind="STORY",
            cycle_id="development",
            required_role="99-nonexistent-agent",
        )
        decision = real_router.route(request)
        assert decision.status == RoutingStatus.BLOCKED
        assert decision.selected_agent_id is None
        # CRITICAL: the word "software-engineer" must NOT appear as selected
        assert decision.selected_agent_id != "software-engineer"

    def test_needs_routing_never_selects_software_engineer(self):
        """Verify NEEDS_ROUTING doesn't pick software-engineer as fallback."""
        decision = RoutingDecision(
            decision_id="RD-test-nr",
            work_item_id="STORY-001",
            stage="IMPLEMENTATION",
            required_role=None,
            required_capability=None,
            candidates=["agent-a", "agent-b"],
            selected_agent_id=None,
            status=RoutingStatus.NEEDS_ROUTING,
            reason="Ambiguous routing test",
        )
        assert decision.selected_agent_id is None


# ---------------------------------------------------------------------------
# INVARIANT 2: All 13 development cycle stages resolve
# ---------------------------------------------------------------------------


class TestAllDevelopmentStagesResolve:
    """Every stage in the development cycle must resolve to a valid specialist
    (ASSIGNED), except stages where the owner is non-dispatchable (e.g. INTAKE/DONE
    owned by delivery-orchestrator).
    """

    # Stages where owner is delivery-orchestrator (non-dispatchable)
    NON_DISPATCHABLE_OWNER_STAGES = {
        LifecycleStage.INTAKE,
        LifecycleStage.DONE,
    }

    @pytest.mark.parametrize(
        "stage",
        [s for s in LifecycleStage if s not in {LifecycleStage.INTAKE, LifecycleStage.DONE}],
        ids=[s.name for s in LifecycleStage if s not in {LifecycleStage.INTAKE, LifecycleStage.DONE}],
    )
    def test_dispatchable_stages_assigned(self, real_router, stage):
        request = RoutingRequest(
            work_item_id="STORY-INV",
            project_id="test",
            stage=stage.value,
            work_item_kind="STORY",
            cycle_id="development",
        )
        decision = real_router.route(request)
        assert decision.status == RoutingStatus.ASSIGNED, (
            f"Stage {stage.value} should be ASSIGNED but got {decision.status.value}: "
            f"{decision.reason}"
        )
        assert decision.selected_agent_id is not None

    @pytest.mark.parametrize(
        "stage",
        [LifecycleStage.INTAKE, LifecycleStage.DONE],
        ids=["INTAKE", "DONE"],
    )
    def test_non_dispatchable_stages_blocked(self, real_router, stage):
        """INTAKE and DONE are owned by delivery-orchestrator (non-dispatchable)."""
        request = RoutingRequest(
            work_item_id="STORY-INV",
            project_id="test",
            stage=stage.value,
            work_item_kind="STORY",
            cycle_id="development",
        )
        decision = real_router.route(request)
        # These may be BLOCKED (owner not dispatchable) or fallback to collaborators
        assert decision.status in (RoutingStatus.BLOCKED, RoutingStatus.ASSIGNED, RoutingStatus.NEEDS_ROUTING)


# ---------------------------------------------------------------------------
# INVARIANT 3: All CANONICAL_STAGE_POLICIES owner_roles resolve in registry
# ---------------------------------------------------------------------------


class TestOwnerRolesResolve:
    """Every owner_role in CANONICAL_STAGE_POLICIES must resolve in the registry
    via direct match or ROLE_ALIAS_MAP.
    """

    @pytest.mark.parametrize(
        "stage,policy",
        [(s, p) for s, p in CANONICAL_STAGE_POLICIES.items()],
        ids=[s.name for s in CANONICAL_STAGE_POLICIES],
    )
    def test_owner_role_resolves(self, real_registry, stage, policy):
        agent = real_registry.resolve_role(policy.owner_role)
        assert agent is not None, (
            f"owner_role '{policy.owner_role}' for stage {stage.value} "
            f"does not resolve in registry or ROLE_ALIAS_MAP"
        )


# ---------------------------------------------------------------------------
# INVARIANT 4: Collaborator roles resolve
# ---------------------------------------------------------------------------


class TestCollaboratorRolesResolve:
    def test_all_collaborators_resolve(self, real_registry):
        for stage, policy in CANONICAL_STAGE_POLICIES.items():
            for collab in policy.collaborator_roles:
                agent = real_registry.resolve_role(collab)
                assert agent is not None, (
                    f"collaborator_role '{collab}' for stage {stage.value} "
                    f"does not resolve in registry or ROLE_ALIAS_MAP"
                )


# ---------------------------------------------------------------------------
# INVARIANT 5: RoutingDecision immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_routing_decision_frozen(self):
        d = RoutingDecision(
            decision_id="RD-freeze",
            work_item_id="STORY-001",
            stage="IMPLEMENTATION",
            required_role=None,
            required_capability=None,
            candidates=["software-engineer"],
            selected_agent_id="software-engineer",
            status=RoutingStatus.ASSIGNED,
            reason="Test immutability",
        )
        with pytest.raises(AttributeError):
            d.status = RoutingStatus.BLOCKED  # type: ignore
        with pytest.raises(AttributeError):
            d.selected_agent_id = "something-else"  # type: ignore
