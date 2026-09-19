"""Tests proving get_assignment is a strict compatibility facade over SpecialistRouter.

R8.2 Governance Contract:
- get_assignment delegates to SpecialistRouter
- Zero independent lexical scoring algorithm deciding assignment
- Zero silent fallback to software-engineer
- Fails closed on unknown or ambiguous routing requests
"""

import inspect
import pytest

from integrations.resolvers import assignment_resolver
from integrations.resolvers.assignment_resolver import get_assignment
from scripts.domain.delegation import RoutingStatus
from scripts.runtime.routing.router import SpecialistRouter


class DummyContext:
    def __init__(self, config_dir="config"):
        self.config_dir = config_dir


class DummySessionStore:
    def __init__(self, session=None):
        self._session = session

    def get_session(self, session_id):
        return self._session


# ---------------------------------------------------------------------------
# Test: Architecture and Source Inspection
# ---------------------------------------------------------------------------


class TestRoutingSingleAuthoritySource:
    def test_legacy_resolver_has_no_independent_scoring_or_fallback(self):
        """Source must NOT contain lexical keyword scoring loop or fallback assignment."""
        src = inspect.getsource(get_assignment)

        # Must not contain keyword scoring logic
        assert "score += 10" not in src
        assert "score += 5" not in src
        assert "score += 3" not in src
        assert "score += 1" not in src
        assert "best_score" not in src

        # Must not contain silent software-engineer fallback
        assert 'next((a for a in agents if a.get("id") == "software-engineer")' not in src
        assert '{"id": "software-engineer", "title": "Software Engineer"}' not in src

        # Must explicitly delegate to SpecialistRouter
        assert "SpecialistRouter" in src
        assert "router.route(request)" in src


# ---------------------------------------------------------------------------
# Test: Facade Behavior
# ---------------------------------------------------------------------------


class TestLegacyRoutingFacadeBehavior:
    def test_known_role_assigned_via_router(self):
        ctx = DummyContext()
        store = DummySessionStore()
        args = {"objective_digest": "requirements-analyst"}

        res = get_assignment(args, ctx, store)
        assert res.get("status") == "ASSIGNED"
        assert res.get("persona_id") == "requirements-analyst"

    def test_aliased_role_normalized_via_router(self):
        """09-code-reviewer should resolve to code-reviewer via R8 normalization."""
        ctx = DummyContext()
        store = DummySessionStore()
        args = {"target_role": "09-code-reviewer"}

        res = get_assignment(args, ctx, store)
        assert res.get("status") == "ASSIGNED"
        assert res.get("persona_id") == "code-reviewer"

    def test_ambiguous_objective_fails_closed_never_software_engineer(self):
        """An arbitrary unknown objective must NEVER fall back to software-engineer."""
        ctx = DummyContext()
        store = DummySessionStore()
        args = {
            "session": "test-session",
            "objective_digest": "completely unknown request with zero matched keywords 99999",
        }

        res = get_assignment(args, ctx, store)
        assert res.get("status") in (RoutingStatus.BLOCKED.value, RoutingStatus.NEEDS_ROUTING.value)
        assert res.get("persona_id") is None
        assert res.get("persona_id") != "software-engineer"

    def test_missing_objective_defaults_to_stage_owner_via_router(self):
        """When empty, stage IMPLEMENTATION routes to stage owner via canonical policies."""
        ctx = DummyContext()
        store = DummySessionStore()
        args = {"stage": "CODE_REVIEW"}

        res = get_assignment(args, ctx, store)
        assert res.get("status") == "ASSIGNED"
        assert res.get("persona_id") == "code-reviewer"
