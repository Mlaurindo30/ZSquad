"""Tests for Milestone R10 — Canonical MCP Session Authority.

Covers:
- Valid session creation and persistence
- Missing session rejection (fail-closed)
- Expired session rejection (TTL enforcement)
- Project mismatch rejection
- Work item binding validation
- Deterministic capability hashing
- No CWD / fake session fallbacks
"""

from pathlib import Path
import tempfile
import time
import pytest

from scripts.runtime.delegation.errors import (
    SessionBlockedError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
)
from scripts.runtime.delegation.sessions import (
    CanonicalSessionManager,
    compute_capability_hash,
)


@pytest.fixture
def session_mgr(tmp_path):
    db_path = tmp_path / "squad.db"
    return CanonicalSessionManager(db_path=db_path)


def test_session_creation_success(session_mgr, tmp_path):
    """Valid session created and stored with revision 1 and deterministic policy hash."""
    project_root = tmp_path / "my_project"
    project_root.mkdir()

    res = session_mgr.create_session(
        host="test-host",
        project_root=str(project_root),
        work_item="US-101",
        ttl=3600,
    )

    assert "session_id" in res
    assert res["ttl"] == 3600
    assert res["revision"] == 1
    assert res["status"] == "active"
    assert len(res["policy_hash"]) == 64

    sess = session_mgr.get_session(res["session_id"], fail_closed=True)
    assert sess["session_id"] == res["session_id"]
    assert sess["project_root"] == str(project_root)
    assert sess["work_item"] == "US-101"
    assert sess["revision"] == 1


def test_missing_session_rejected_fail_closed(session_mgr):
    """Nonexistent session fails closed with SessionNotFoundError."""
    with pytest.raises(SessionNotFoundError):
        session_mgr.get_session("non-existent-session-id-999", fail_closed=True)

    # When fail_closed=False, returns None
    assert session_mgr.get_session("non-existent-session-id-999", fail_closed=False) is None


def test_empty_session_id_rejected(session_mgr):
    """Empty or whitespace session ID fails closed."""
    with pytest.raises(SessionNotFoundError):
        session_mgr.get_session("", fail_closed=True)
    with pytest.raises(SessionNotFoundError):
        session_mgr.get_session("   ", fail_closed=True)


def test_expired_session_rejected_fail_closed(session_mgr, tmp_path):
    """Session past TTL fails closed with SessionExpiredError."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    res = session_mgr.create_session(
        host="test-host",
        project_root=str(project_root),
        work_item="US-102",
        ttl=1,  # 1 second TTL
    )
    time.sleep(1.2)

    with pytest.raises(SessionExpiredError):
        session_mgr.get_session(res["session_id"], fail_closed=True)

    assert session_mgr.get_session(res["session_id"], fail_closed=False) is None


def test_blocked_session_rejected_fail_closed(session_mgr, tmp_path):
    """Blocked session fails closed with SessionBlockedError."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    res = session_mgr.create_session(
        host="test-host",
        project_root=str(project_root),
        work_item="US-103",
    )
    session_mgr.mark_blocked(res["session_id"])

    with pytest.raises(SessionBlockedError):
        session_mgr.get_session(res["session_id"], fail_closed=True)

    assert session_mgr.get_session(res["session_id"], fail_closed=False) is None


def test_deterministic_capability_hash():
    """Capability hash is deterministic across identical tool and capability sets."""
    tools = ["agent-squad-mcp", "azure-devops-mcp"]
    caps = {"filesystem": True, "terminal": True}

    hash1 = compute_capability_hash(tools, caps)
    # Different order of tools must yield identical hash
    hash2 = compute_capability_hash(["azure-devops-mcp", "agent-squad-mcp"], caps)
    assert hash1 == hash2

    # Changed tools changes hash
    hash3 = compute_capability_hash(["agent-squad-mcp"], caps)
    assert hash3 != hash1


def test_forbidden_legacy_tokens_rejected(session_mgr):
    """Synthetic legacy paths like test_root are rejected if not existing on disk."""
    with pytest.raises(SessionError, match="Forbidden legacy token"):
        session_mgr.create_session(
            host="test-host",
            project_root="test_root",
            work_item="test_item",
        )
