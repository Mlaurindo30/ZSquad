"""Tests for Milestone R10 — Legacy prepare_delegation resolver facade.

Covers:
- Rejection of missing/invalid session (fail-closed)
- Preservation of authoritative rendered_prompt and matching SHA-256 hash
- Graceful error propagation
- Zero subagent dispatch
"""

import hashlib
from pathlib import Path
import pytest

from integrations.mcp_session_store import SessionStore
from integrations.resolvers import ResolverContext
from integrations.resolvers.assignment_resolver import prepare_delegation


@pytest.fixture
def resolver_env(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "squad.db"))
    ctx = ResolverContext(
        db_path=str(tmp_path / "squad.db"),
        config_dir=str(Path(__file__).resolve().parents[2] / "config"),
    )
    proj_dir = tmp_path / "test_proj"
    proj_dir.mkdir()
    return store, ctx, proj_dir


def test_prepare_delegation_missing_session_fails_closed(resolver_env):
    """Calling prepare_delegation with nonexistent session raises ValueError."""
    store, ctx, _ = resolver_env

    with pytest.raises(ValueError, match="(?i)(invalid session|not found|expired)"):
        prepare_delegation(
            {"session": "non-existent-session-999"},
            ctx,
            store,
        )


def test_prepare_delegation_empty_session_fails_closed(resolver_env):
    """Calling prepare_delegation without session raises ValueError."""
    store, ctx, _ = resolver_env

    with pytest.raises(ValueError, match="(?i)(invalid session|parameter is missing)"):
        prepare_delegation({}, ctx, store)


def test_prepare_delegation_success_produces_authoritative_hash(resolver_env):
    """Successful delegation briefing produces hash matching rendered_prompt."""
    store, ctx, proj_dir = resolver_env

    sess = store.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-TEST",
        capability_report_hash="test_hash",
    )

    args = {
        "session": sess["session_id"],
        "target_role": "software-engineer",
        "scope": "Testing legacy resolver",
        "action": "Implementation",
    }

    result = prepare_delegation(args, ctx, store)

    assert "rendered_prompt" in result
    assert "briefing" in result
    assert "hash" in result
    assert len(result["rendered_prompt"]) > 0

    expected_hash = hashlib.sha256(result["rendered_prompt"].encode("utf-8")).hexdigest()
    assert result["hash"] == expected_hash
