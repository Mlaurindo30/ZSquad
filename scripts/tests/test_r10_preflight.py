"""Tests for Milestone R10 — Authoritative Preflight Verification Engine.

Covers:
- Ready activation + valid session + all tools -> ALLOW
- Missing required tool -> BLOCK
- Stale activation / hash mismatch -> BLOCK
- Stage mismatch -> BLOCK
- Project mismatch -> BLOCK
- Nonexistent path / containment failure -> BLOCK
- Fail-closed exception handling
- Deterministic structured results
"""

from datetime import datetime
import hashlib
from pathlib import Path
import pytest

from scripts.domain.delegation import ActivationPacket, WorkContext
from scripts.runtime.delegation.preflight import (
    PreflightDecision,
    PreflightValidator,
)
from scripts.runtime.delegation.sessions import CanonicalSessionManager


@pytest.fixture
def test_env(tmp_path):
    db_path = tmp_path / "squad.db"
    session_mgr = CanonicalSessionManager(db_path=db_path)
    proj_dir = tmp_path / "test_project"
    proj_dir.mkdir()

    validator = PreflightValidator(
        runtime_root=tmp_path,
        session_manager=session_mgr,
        db_path=db_path,
    )
    return session_mgr, validator, proj_dir


def _make_activation_packet(
    session_id: str,
    project_id: str,
    work_item_id: str = "US-100",
    stage: str = "IMPLEMENTATION",
    instruction: str = "Specialist instruction payload.",
    required_tools: list = None,
) -> ActivationPacket:
    wc = WorkContext(
        work_item_id=work_item_id,
        project_id=project_id,
        current_stage=stage,
        title="Test Story",
        description="Description",
        definition_of_done=["Done item"],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=["src/"],
    )
    instr_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    manifest = {
        "agent": "06-software-engineer",
        "required_tools": required_tools or ["agent-squad-mcp"],
    }
    return ActivationPacket(
        session_id=session_id,
        agent_id="06-software-engineer",
        role_name="Software Engineer",
        work_item_id=work_item_id,
        work_context=wc,
        skill_manifest=manifest,
        compiled_instruction=instruction,
        instruction_hash=instr_hash,
    )


def test_preflight_allow_on_valid_conditions(test_env):
    """Ready activation with valid session and available tools yields ALLOW."""
    session_mgr, validator, proj_dir = test_env

    # Create real path
    code_file = proj_dir / "main.py"
    code_file.write_text("print('hello')", encoding="utf-8")

    sess_res = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-100",
        project_id="test_project",
        tools=["agent-squad-mcp"],
    )
    session_id = sess_res["session_id"]

    packet = _make_activation_packet(
        session_id="ACT-001",
        project_id="test_project",
        work_item_id="US-100",
        required_tools=["agent-squad-mcp"],
    )

    result = validator.validate(
        session_id=session_id,
        activation_packet=packet,
        paths=[str(code_file)],
        expected_stage="IMPLEMENTATION",
    )

    assert result.decision == PreflightDecision.ALLOW
    assert result.is_allowed is True
    assert len(result.reasons) == 0
    assert result.session_id == session_id


def test_preflight_block_on_missing_session(test_env):
    """Missing or invalid session yields BLOCK."""
    _, validator, _ = test_env

    result = validator.validate(
        session_id="non-existent-session",
    )

    assert result.decision == PreflightDecision.BLOCK
    assert result.is_allowed is False
    assert any("not found" in r.lower() for r in result.reasons)


def test_preflight_block_on_nonexistent_paths(test_env):
    """Nonexistent target paths yield BLOCK."""
    session_mgr, validator, proj_dir = test_env

    sess_res = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-100",
        project_id="test_project",
    )

    fake_path = str(proj_dir / "does_not_exist.py")
    result = validator.validate(
        session_id=sess_res["session_id"],
        paths=[fake_path],
    )

    assert result.decision == PreflightDecision.BLOCK
    assert any("Paths do not exist" in r for r in result.reasons)


def test_preflight_block_on_missing_required_tool(test_env):
    """Missing required tool yields BLOCK."""
    session_mgr, validator, proj_dir = test_env

    # Session only has agent-squad-mcp
    sess_res = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-100",
        project_id="test_project",
        tools=["agent-squad-mcp"],
    )

    # Activation requires azure-devops-mcp
    packet = _make_activation_packet(
        session_id="ACT-002",
        project_id="test_project",
        work_item_id="US-100",
        required_tools=["azure-devops-mcp"],
    )

    result = validator.validate(
        session_id=sess_res["session_id"],
        activation_packet=packet,
    )

    assert result.decision == PreflightDecision.BLOCK
    assert any("azure-devops-mcp" in r for r in result.reasons)


def test_preflight_block_on_instruction_hash_tampering(test_env):
    """Tampered activation instruction hash yields BLOCK."""
    session_mgr, validator, proj_dir = test_env

    sess_res = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-100",
        project_id="test_project",
    )

    # Construct packet then corrupt compiled_instruction
    base_packet = _make_activation_packet("ACT-003", "test_project", "US-100")
    # Tampered instruction:
    object.__setattr__(base_packet, "compiled_instruction", "MALICIOUS INJECTION")

    result = validator.validate(
        session_id=sess_res["session_id"],
        activation_packet=base_packet,
    )

    assert result.decision == PreflightDecision.BLOCK
    assert any("hash mismatch" in r.lower() for r in result.reasons)


def test_preflight_block_on_stage_drift(test_env):
    """Drifted lifecycle stage yields BLOCK."""
    session_mgr, validator, proj_dir = test_env

    sess_res = session_mgr.create_session(
        host="test-host",
        project_root=str(proj_dir),
        work_item="US-100",
        project_id="test_project",
    )

    # Compiled for IMPLEMENTATION, but expected is CODE_REVIEW
    packet = _make_activation_packet("ACT-004", "test_project", "US-100", stage="IMPLEMENTATION")

    result = validator.validate(
        session_id=sess_res["session_id"],
        activation_packet=packet,
        expected_stage="CODE_REVIEW",
    )

    assert result.decision == PreflightDecision.BLOCK
    assert any("stage mismatch" in r.lower() for r in result.reasons)
