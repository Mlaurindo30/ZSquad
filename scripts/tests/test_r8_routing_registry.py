"""R8 — Agent Registry Tests.

Tests:
- Registry parse from valid YAML data
- get_agent returns correct agent
- resolve_role with alias
- find_by_capability works
- Non-dispatchable filtered
- Empty registry → RegistryConfigError
- Role alias resolution for CANONICAL_STAGE_POLICIES mismatches
"""

import pytest

from scripts.runtime.routing.errors import RegistryConfigError
from scripts.runtime.routing.registry import (
    ROLE_ALIAS_MAP,
    AgentCapability,
    AgentRegistry,
    _extract_capabilities,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_REGISTRY = {
    "agents": [
        {
            "id": "software-engineer",
            "path": "agents/06-software-engineer",
            "title": "Clean Code & TDD Craftsman",
            "mode": "on_demand",
            "purpose": "TDD, SOLID design principles, clean code contracts, unit & integration tests.",
        },
        {
            "id": "security-reviewer",
            "path": "agents/10-security-reviewer",
            "title": "AppSec & Threat Modeling Specialist",
            "mode": "on_demand",
            "purpose": "OWASP Top 10, ASVS 4.0, STRIDE threat modeling, dependency CVE auditing.",
        },
        {
            "id": "delivery-orchestrator",
            "path": "agents/00-delivery-orchestrator",
            "title": "Swarm & SDLC Delivery Orchestrator",
            "mode": "host",
            "purpose": "SDLC orchestration, WIP control, gate verification.",
            "dispatchable": False,
            "singleton": True,
        },
    ]
}


# ---------------------------------------------------------------------------
# Test: Registry Parse
# ---------------------------------------------------------------------------


class TestRegistryParse:
    def test_parse_valid_registry(self):
        reg = AgentRegistry(MINIMAL_REGISTRY)
        assert len(reg.list_all()) == 3

    def test_empty_registry_raises(self):
        with pytest.raises(RegistryConfigError, match="no agents"):
            AgentRegistry({"agents": []})

    def test_none_registry_raises(self):
        with pytest.raises(RegistryConfigError, match="non-empty dict"):
            AgentRegistry(None)

    def test_no_agents_key_raises(self):
        with pytest.raises(RegistryConfigError, match="no agents"):
            AgentRegistry({"version": 2})


# ---------------------------------------------------------------------------
# Test: Agent Lookup
# ---------------------------------------------------------------------------


class TestAgentLookup:
    def setup_method(self):
        self.reg = AgentRegistry(MINIMAL_REGISTRY)

    def test_get_agent_by_id(self):
        agent = self.reg.get_agent("software-engineer")
        assert agent is not None
        assert agent.agent_id == "software-engineer"
        assert agent.title == "Clean Code & TDD Craftsman"

    def test_get_agent_not_found(self):
        agent = self.reg.get_agent("nonexistent-agent")
        assert agent is None

    def test_agent_dispatchable_default_true(self):
        agent = self.reg.get_agent("software-engineer")
        assert agent.dispatchable is True

    def test_agent_not_dispatchable(self):
        agent = self.reg.get_agent("delivery-orchestrator")
        assert agent.dispatchable is False


# ---------------------------------------------------------------------------
# Test: Role Alias Resolution
# ---------------------------------------------------------------------------


class TestRoleAlias:
    def setup_method(self):
        self.reg = AgentRegistry(MINIMAL_REGISTRY)

    def test_resolve_direct_id(self):
        agent = self.reg.resolve_role("software-engineer")
        assert agent is not None
        assert agent.agent_id == "software-engineer"

    def test_resolve_aliased_role(self):
        """10-security-specialist → security-reviewer via ROLE_ALIAS_MAP."""
        agent = self.reg.resolve_role("10-security-specialist")
        assert agent is not None
        assert agent.agent_id == "security-reviewer"

    def test_resolve_numeric_prefix_alias(self):
        """06-software-engineer → software-engineer via ROLE_ALIAS_MAP."""
        agent = self.reg.resolve_role("06-software-engineer")
        assert agent is not None
        assert agent.agent_id == "software-engineer"

    def test_resolve_unknown_role_returns_none(self):
        agent = self.reg.resolve_role("99-phantom-agent")
        assert agent is None


# ---------------------------------------------------------------------------
# Test: Capability Search
# ---------------------------------------------------------------------------


class TestCapabilitySearch:
    def setup_method(self):
        self.reg = AgentRegistry(MINIMAL_REGISTRY)

    def test_find_by_capability_keyword(self):
        results = self.reg.find_by_capability("TDD")
        assert len(results) >= 1
        assert any(a.agent_id == "software-engineer" for a in results)

    def test_find_by_capability_no_match(self):
        results = self.reg.find_by_capability("quantum-computing-specialized")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Test: Dispatchable Filter
# ---------------------------------------------------------------------------


class TestDispatchableFilter:
    def setup_method(self):
        self.reg = AgentRegistry(MINIMAL_REGISTRY)

    def test_find_dispatchable_excludes_host(self):
        dispatchable = self.reg.find_dispatchable()
        ids = [a.agent_id for a in dispatchable]
        assert "delivery-orchestrator" not in ids
        assert "software-engineer" in ids


# ---------------------------------------------------------------------------
# Test: Capability Extraction
# ---------------------------------------------------------------------------


class TestCapabilityExtraction:
    def test_extract_from_purpose(self):
        caps = _extract_capabilities("TDD, SOLID design principles, clean code contracts")
        assert "tdd" in caps
        assert "solid design principles" in caps

    def test_extract_empty(self):
        caps = _extract_capabilities("")
        assert len(caps) == 0

    def test_extract_filters_short(self):
        caps = _extract_capabilities("A, BB, CCC, DDDD stuff")
        # "A" and "BB" are too short (< 3 chars)
        assert "a" not in caps
        assert "bb" not in caps


# ---------------------------------------------------------------------------
# Test: COMPATIBILITY_ONLY Normalization Boundary
# ---------------------------------------------------------------------------


class TestRoleNormalizationBoundary:
    """Verifies that role aliasing is strictly COMPATIBILITY_ONLY and algorithmic."""

    def test_known_legacy_exception_security_specialist(self):
        """The primary semantic exception: 10-security-specialist → security-reviewer."""
        from scripts.runtime.routing.registry import LEGACY_ROLE_EXCEPTIONS, normalize_role_reference
        assert LEGACY_ROLE_EXCEPTIONS["10-security-specialist"] == "security-reviewer"
        assert normalize_role_reference("10-security-specialist") == "security-reviewer"

    def test_algorithmic_prefix_strip(self):
        """Standard roles are normalized algorithmically without shadow entries."""
        from scripts.runtime.routing.registry import normalize_role_reference
        assert normalize_role_reference("01-requirements-analyst") == "requirements-analyst"
        assert normalize_role_reference("06-software-engineer") == "software-engineer"
        assert normalize_role_reference("07-frontend-engineer") == "frontend-engineer"
        assert normalize_role_reference("08-backend-engineer") == "backend-engineer"
        assert normalize_role_reference("27-platform-engineer") == "platform-engineer"
        assert normalize_role_reference("40-agile-coach") == "agile-coach"

    def test_unprefixed_role_preserved(self):
        from scripts.runtime.routing.registry import normalize_role_reference
        assert normalize_role_reference("software-engineer") == "software-engineer"
        assert normalize_role_reference("platform-engineer") == "platform-engineer"

    def test_structured_capabilities_source(self):
        """Verifies capabilities come from STRUCTURED_CANONICAL metadata."""
        from scripts.runtime.routing.registry import CANONICAL_AGENT_CAPABILITIES
        reg = AgentRegistry(MINIMAL_REGISTRY)
        se = reg.get_agent("software-engineer")
        assert se is not None
        assert "tdd" in se.capabilities
        assert "clean-code" in se.capabilities

