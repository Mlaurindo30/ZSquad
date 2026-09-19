"""R8 — Routing Policies Tests.

Tests:
- resolve_stage_demand for all 13 canonical stages
- Owner/collaborator correctness
- Unknown stage raises RoutingError
- Default routing policy values
"""

import pytest

from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.runtime.routing.errors import RoutingError
from scripts.runtime.routing.policies import (
    DEFAULT_ROUTING_POLICY,
    RoutingPolicy,
    StageDemand,
    resolve_stage_demand,
)


# ---------------------------------------------------------------------------
# Test: resolve_stage_demand for all 13 stages
# ---------------------------------------------------------------------------


class TestResolveAllStages:
    """Every canonical stage must resolve to a valid StageDemand."""

    @pytest.mark.parametrize(
        "stage_value",
        [s.value for s in LifecycleStage],
        ids=[s.name for s in LifecycleStage],
    )
    def test_all_stages_resolve(self, stage_value):
        demand = resolve_stage_demand(stage_value)
        assert isinstance(demand, StageDemand)
        assert demand.stage == LifecycleStage(stage_value)
        assert demand.owner_role  # Must have an owner
        assert isinstance(demand.collaborator_roles, list)


class TestStageDemandOwners:
    """Verify specific owner_role assignments match CANONICAL_STAGE_POLICIES."""

    def test_intake_owner(self):
        d = resolve_stage_demand("INTAKE")
        assert d.owner_role == "00-delivery-orchestrator"

    def test_discovery_owner(self):
        d = resolve_stage_demand("DISCOVERY")
        assert d.owner_role == "01-requirements-analyst"

    def test_requirements_owner(self):
        d = resolve_stage_demand("REQUIREMENTS_PRODUCT")
        assert d.owner_role == "01-requirements-analyst"

    def test_planning_owner(self):
        d = resolve_stage_demand("PLANNING")
        assert d.owner_role == "40-agile-coach"

    def test_architecture_owner(self):
        d = resolve_stage_demand("ARCHITECTURE_DESIGN")
        assert d.owner_role == "04-solution-architect"

    def test_scaffolding_owner(self):
        d = resolve_stage_demand("READINESS_SCAFFOLDING")
        assert d.owner_role == "27-platform-engineer"

    def test_implementation_owner(self):
        d = resolve_stage_demand("IMPLEMENTATION")
        assert d.owner_role == "06-software-engineer"

    def test_code_review_owner(self):
        d = resolve_stage_demand("CODE_REVIEW")
        assert d.owner_role == "09-code-reviewer"

    def test_security_review_owner(self):
        d = resolve_stage_demand("SECURITY_REVIEW")
        assert d.owner_role == "10-security-specialist"

    def test_test_validation_owner(self):
        d = resolve_stage_demand("TEST_VALIDATION")
        assert d.owner_role == "11-test-engineer"

    def test_qa_validation_owner(self):
        d = resolve_stage_demand("QA_VALIDATION")
        assert d.owner_role == "12-qa-engineer"

    def test_governance_owner(self):
        d = resolve_stage_demand("GOVERNANCE_RELEASE")
        assert d.owner_role == "14-governance-auditor"

    def test_done_owner(self):
        d = resolve_stage_demand("DONE")
        assert d.owner_role == "00-delivery-orchestrator"


class TestStageDemandGates:
    """Verify gate requirements match CANONICAL_STAGE_POLICIES."""

    def test_intake_no_gate(self):
        d = resolve_stage_demand("INTAKE")
        assert d.required_gate is None

    def test_requirements_g1(self):
        d = resolve_stage_demand("REQUIREMENTS_PRODUCT")
        assert d.required_gate == GateId.G1_PRODUCT

    def test_architecture_g2(self):
        d = resolve_stage_demand("ARCHITECTURE_DESIGN")
        assert d.required_gate == GateId.G2_DESIGN

    def test_scaffolding_g3(self):
        d = resolve_stage_demand("READINESS_SCAFFOLDING")
        assert d.required_gate == GateId.G3_READINESS

    def test_security_g4(self):
        d = resolve_stage_demand("SECURITY_REVIEW")
        assert d.required_gate == GateId.G4_CODE_SECURITY

    def test_qa_g5(self):
        d = resolve_stage_demand("QA_VALIDATION")
        assert d.required_gate == GateId.G5_QUALITY

    def test_governance_g6(self):
        d = resolve_stage_demand("GOVERNANCE_RELEASE")
        assert d.required_gate == GateId.G6_GOVERNANCE_RELEASE


class TestStageDemandCollaborators:
    def test_architecture_has_security_collab(self):
        d = resolve_stage_demand("ARCHITECTURE_DESIGN")
        assert "10-security-specialist" in d.collaborator_roles

    def test_code_review_isolated(self):
        d = resolve_stage_demand("CODE_REVIEW")
        assert d.collaborator_roles == []


# ---------------------------------------------------------------------------
# Test: Unknown stage
# ---------------------------------------------------------------------------


class TestUnknownStage:
    def test_unknown_stage_raises(self):
        with pytest.raises(RoutingError, match="Cannot resolve stage demand"):
            resolve_stage_demand("PHANTOM_STAGE_99")


# ---------------------------------------------------------------------------
# Test: Legacy alias stages resolve
# ---------------------------------------------------------------------------


class TestLegacyAliasResolution:
    def test_legacy_intake(self):
        d = resolve_stage_demand("intake")
        assert d.stage == LifecycleStage.INTAKE

    def test_legacy_blueprint(self):
        d = resolve_stage_demand("blueprint")
        assert d.stage == LifecycleStage.REQUIREMENTS_PRODUCT

    def test_legacy_review(self):
        d = resolve_stage_demand("review")
        assert d.stage == LifecycleStage.CODE_REVIEW


# ---------------------------------------------------------------------------
# Test: Default policy
# ---------------------------------------------------------------------------


class TestDefaultPolicy:
    def test_default_values(self):
        p = DEFAULT_ROUTING_POLICY
        assert p.require_stage_owner_first is True
        assert p.allow_collaborators is True
        assert p.enforce_wip is False
        assert p.enforce_sod is True
        assert p.max_candidates == 10
