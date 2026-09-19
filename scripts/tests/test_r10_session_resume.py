"""Tests for Milestone R10 — MCP Session Resume and Revision Control.

Covers:
- Successful session resume with matching revision
- Rejection of stale/mismatched revision
- Rejection of resume on expired session
- Rejection of resume on blocked session
- Rejection of resume on capability hash mismatch
- Monotonic revision increment
"""

from pathlib import Path
import time
import pytest

from scripts.runtime.delegation.errors import (
    SessionBlockedError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevisionConflictError,
)
from scripts.runtime.delegation.sessions import CanonicalSessionManager


@pytest.fixture
def session_mgr(tmp_path):
    db_path = tmp_path / "squad.db"
    return CanonicalSessionManager(db_path=db_path)


def test_resume_session_success(session_mgr, tmp_path):
    """Resume with matching revision increments revision and extends TTL."""
    proj = tmp_path / "proj"
    proj.mkdir()

    res = session_mgr.create_session(
        host="host",
        project_root=str(proj),
        work_item="US-1",
        ttl=100,
    )
    session_id = res["session_id"]
    assert res["revision"] == 1

    # Resume with revision 1
    res_resume = session_mgr.resume_session(
        session_id=session_id,
        last_revision=1,
        extension_ttl=3600,
    )

    assert res_resume["revision"] == 2
    assert res_resume["ttl"] == 3600
    assert res_resume["status"] == "active"

    # Verify state in DB
    sess = session_mgr.get_session(session_id, fail_closed=True)
    assert sess["revision"] == 2


def test_resume_session_revision_conflict(session_mgr, tmp_path):
    """Resume with mismatched revision raises SessionRevisionConflictError."""
    proj = tmp_path / "proj"
    proj.mkdir()

    res = session_mgr.create_session(
        host="host",
        project_root=str(proj),
        work_item="US-2",
    )
    session_id = res["session_id"]

    # Active revision is 1, caller supplies 0 or 2
    with pytest.raises(SessionRevisionConflictError, match="Session revision conflict"):
        session_mgr.resume_session(session_id=session_id, last_revision=0)

    with pytest.raises(SessionRevisionConflictError, match="Session revision conflict"):
        session_mgr.resume_session(session_id=session_id, last_revision=99)


def test_resume_session_expired_fails(session_mgr, tmp_path):
    """Resume on expired session raises SessionExpiredError."""
    proj = tmp_path / "proj"
    proj.mkdir()

    res = session_mgr.create_session(
        host="host",
        project_root=str(proj),
        work_item="US-3",
        ttl=1,
    )
    time.sleep(1.2)

    with pytest.raises(SessionExpiredError, match="expired and cannot be resumed"):
        session_mgr.resume_session(session_id=res["session_id"], last_revision=1)


def test_resume_session_blocked_fails(session_mgr, tmp_path):
    """Resume on blocked session raises SessionBlockedError."""
    proj = tmp_path / "proj"
    proj.mkdir()

    res = session_mgr.create_session(
        host="host",
        project_root=str(proj),
        work_item="US-4",
    )
    session_mgr.mark_blocked(res["session_id"])

    with pytest.raises(SessionBlockedError, match="Cannot resume blocked session"):
        session_mgr.resume_session(session_id=res["session_id"], last_revision=1)


def test_resume_session_capability_mismatch(session_mgr, tmp_path):
    """Resume with unexpected capability hash raises SessionError."""
    proj = tmp_path / "proj"
    proj.mkdir()

    res = session_mgr.create_session(
        host="host",
        project_root=str(proj),
        work_item="US-5",
    )

    with pytest.raises(SessionError, match="capability mismatch"):
        session_mgr.resume_session(
            session_id=res["session_id"],
            last_revision=1,
            expected_capability_hash="tampered_or_drifted_hash_123456",
        )
