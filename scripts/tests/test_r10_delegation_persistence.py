"""Tests for Milestone R10 — Delegation Persistence and Idempotency.

Covers:
- SQLite persistence round-trip of DelegationEnvelope
- Idempotent retrieval for identical activation and session revision
- New envelope generated upon session revision increment
- Blocked delegations tracked with status BLOCKED
- Work item query filtering
"""

from pathlib import Path
import pytest

from scripts.domain.delegation import ActivationPacket, DelegationEnvelope, WorkContext
from scripts.runtime.delegation.errors import PreflightBlockedError
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.delegation.service import DelegationService
from scripts.runtime.delegation.sessions import CanonicalSessionManager


@pytest.fixture
def test_setup(tmp_path):
    db_path = tmp_path / "squad.db"
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    session_mgr = CanonicalSessionManager(db_path=db_path)
    repo = DelegationRepository(db_path=db_path)
    service = DelegationService(runtime_root=tmp_path, db_path=db_path)

    return session_mgr, repo, service, proj_dir


def _create_packet(proj_dir, session_id="ACT-P-01"):
    wc = WorkContext(
        work_item_id="US-PERSIST-1",
        project_id="proj",
        current_stage="IMPLEMENTATION",
        title="Persist Story",
        description="Desc",
        definition_of_done=["Done"],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=["src/"],
    )
    instr = "Instruction content"
    import hashlib
    h = hashlib.sha256(instr.encode()).hexdigest()
    return ActivationPacket(
        session_id=session_id,
        agent_id="06-software-engineer",
        role_name="Software Engineer",
        work_item_id="US-PERSIST-1",
        work_context=wc,
        skill_manifest={"agent": "06-software-engineer"},
        compiled_instruction=instr,
        instruction_hash=h,
    )


def test_delegation_service_prepare_and_persist(test_setup):
    """Prepares and persists delegation with READY_FOR_DISPATCH status."""
    session_mgr, repo, service, proj_dir = test_setup

    sess = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-PERSIST-1",
        project_id="proj",
    )
    session_id = sess["session_id"]
    packet = _create_packet(proj_dir)

    envelope, preflight_res = service.prepare_delegation(
        activation_packet=packet,
        session_id=session_id,
    )

    assert envelope is not None
    assert preflight_res.is_allowed is True

    # Check persisted row in SQLite
    row = repo.get_by_id(envelope.delegation_id)
    assert row is not None
    assert row["status"] == "READY_FOR_DISPATCH"
    assert row["work_item_id"] == "US-PERSIST-1"
    assert row["session_id"] == session_id
    assert row["session_revision"] == 1


def test_delegation_service_idempotent_reuse(test_setup):
    """Calling prepare_delegation again with same session revision returns identical envelope."""
    session_mgr, _, service, proj_dir = test_setup

    sess = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-PERSIST-1",
        project_id="proj",
    )
    session_id = sess["session_id"]
    packet = _create_packet(proj_dir)

    env1, _ = service.prepare_delegation(packet, session_id)
    env2, _ = service.prepare_delegation(packet, session_id)

    assert env1.delegation_id == env2.delegation_id
    assert env1.instruction_hash == env2.instruction_hash


def test_delegation_service_creates_new_envelope_on_session_revision(test_setup):
    """When session revision is incremented, service builds a new delegation envelope."""
    session_mgr, _, service, proj_dir = test_setup

    sess = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-PERSIST-1",
        project_id="proj",
    )
    session_id = sess["session_id"]
    packet = _create_packet(proj_dir)

    env1, _ = service.prepare_delegation(packet, session_id)

    # Resume session (increments revision to 2)
    session_mgr.resume_session(session_id, last_revision=1)

    env2, _ = service.prepare_delegation(packet, session_id)

    assert env1.delegation_id != env2.delegation_id


def test_delegation_blocked_tracked_in_persistence(test_setup):
    """Blocked delegation records are persisted with BLOCKED status and raise PreflightBlockedError."""
    session_mgr, repo, service, proj_dir = test_setup

    sess = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-PERSIST-1",
        project_id="proj",
    )
    session_id = sess["session_id"]
    packet = _create_packet(proj_dir)

    fake_path = str(proj_dir / "nonexistent_file_to_block.txt")

    with pytest.raises(PreflightBlockedError):
        service.prepare_delegation(
            activation_packet=packet,
            session_id=session_id,
            paths=[fake_path],
        )

    # Verify blocked record exists in database
    records = repo.list_by_work_item("US-PERSIST-1")
    blocked = [r for r in records if r["status"] == "BLOCKED"]
    assert len(blocked) >= 1
    assert blocked[0]["preflight_decision"] == "block"
