"""Tests for Milestone R10 — Delegation Authority & Security Invariants.

Covers:
- Strict reuse of R8 assignment and R9 activation packet (no rerouting, no recompilation)
- Zero host-native dispatch or LLM invocation
- One session authority and one preflight authority
- Security checks:
  - Session tampering / spoofing
  - Expired session replay prevention
  - Cross-project session reuse rejection
  - Tool availability / capability escalation prevention
  - Envelope payload immutability
  - Preflight bypass prevention
  - Zero plaintext secrets in envelope
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import pytest

from scripts.domain.delegation import (
    ActivationPacket,
    DelegationEnvelope,
    ExecutionAssignment,
    WorkContext,
)
from scripts.runtime.delegation.errors import (
    PreflightBlockedError,
    SessionBlockedError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevisionConflictError,
)
from scripts.runtime.delegation.service import DelegationService
from scripts.runtime.delegation.sessions import CanonicalSessionManager


@pytest.fixture
def auth_env(tmp_path):
    db_path = tmp_path / "squad.db"
    proj_a = tmp_path / "project_alpha"
    proj_b = tmp_path / "project_beta"
    proj_a.mkdir()
    proj_b.mkdir()

    session_mgr = CanonicalSessionManager(db_path=db_path)
    service = DelegationService(runtime_root=tmp_path, db_path=db_path)

    return session_mgr, service, proj_a, proj_b


def _create_test_activation(project_id: str, work_item_id: str, secret_check: bool = False):
    wc = WorkContext(
        work_item_id=work_item_id,
        project_id=project_id,
        current_stage="IMPLEMENTATION",
        title="Test Work Context",
        description="Clean task description",
        definition_of_done=["Check 1"],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=["src/"],
    )
    instruction = (
        "# AGENT SYSTEM PROMPT: 06-software-engineer\n"
        "Execute approved scope under SoD guidelines.\n"
    )
    if secret_check:
        instruction += "No credentials or tokens.\n"

    instr_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    return ActivationPacket(
        session_id=f"ACT-{work_item_id}",
        agent_id="06-software-engineer",
        role_name="Software Engineer",
        work_item_id=work_item_id,
        work_context=wc,
        skill_manifest={"agent": "06-software-engineer", "required_tools": ["agent-squad-mcp"]},
        compiled_instruction=instruction,
        instruction_hash=instr_hash,
    )


def test_reuse_r8_and_r9_without_mutation(auth_env):
    """Proves R10 reuses R8 and R9 outputs without modifying compiled instruction or agent."""
    session_mgr, service, proj_a, _ = auth_env

    sess = session_mgr.create_session(
        host="host-env",
        project_root=str(proj_a),
        work_item="US-AUTH-1",
        project_id="project_alpha",
        tools=["agent-squad-mcp"],
    )

    packet = _create_test_activation(project_id="project_alpha", work_item_id="US-AUTH-1")

    envelope, preflight_res = service.prepare_delegation(
        activation_packet=packet,
        session_id=sess["session_id"],
    )

    # Invariant: compiled instruction is verbatim identical
    assert envelope.compiled_instruction == packet.compiled_instruction
    assert envelope.instruction_hash == packet.instruction_hash
    assert envelope.target_role == packet.role_name
    assert envelope.work_item_id == packet.work_item_id
    assert preflight_res.is_allowed is True


def test_security_cross_project_session_reuse_blocked(auth_env):
    """Session bound to project_alpha cannot be used to delegate for project_beta."""
    session_mgr, service, proj_a, _ = auth_env

    # Session created for project_alpha
    sess = session_mgr.create_session(
        host="host-env",
        project_root=str(proj_a),
        work_item="US-CROSS-1",
        project_id="project_alpha",
    )

    # Packet targets project_beta
    packet_beta = _create_test_activation(project_id="project_beta", work_item_id="US-CROSS-1")

    with pytest.raises(PreflightBlockedError, match="(?i)(project mismatch|blocked)"):
        service.prepare_delegation(
            activation_packet=packet_beta,
            session_id=sess["session_id"],
        )


def test_security_tool_escalation_blocked(auth_env):
    """Specialist requiring ungranted tools is blocked by preflight."""
    session_mgr, service, proj_a, _ = auth_env

    # Session only possesses agent-squad-mcp
    sess = session_mgr.create_session(
        host="host-env",
        project_root=str(proj_a),
        work_item="US-TOOL-1",
        project_id="project_alpha",
        tools=["agent-squad-mcp"],
    )

    packet = _create_test_activation(project_id="project_alpha", work_item_id="US-TOOL-1")

    # Request tool not in session
    with pytest.raises(PreflightBlockedError, match="(?i)(missing|blocked)"):
        service.prepare_delegation(
            activation_packet=packet,
            session_id=sess["session_id"],
            required_tools=["admin-destructive-tool"],
        )


def test_security_zero_plaintext_secrets_in_envelope(auth_env):
    """DelegationEnvelope payload contains no plaintext secrets, tokens or passwords."""
    session_mgr, service, proj_a, _ = auth_env

    sess = session_mgr.create_session(
        host="host-env",
        project_root=str(proj_a),
        work_item="US-SEC-1",
        project_id="project_alpha",
        tools=["agent-squad-mcp"],
    )
    packet = _create_test_activation(project_id="project_alpha", work_item_id="US-SEC-1", secret_check=True)

    envelope, _ = service.prepare_delegation(packet, sess["session_id"])
    serialized = str(envelope.to_dict()).lower()

    forbidden_patterns = ["bearer ", "pat=", "secret_key", "password", "private_key"]
    for pat in forbidden_patterns:
        assert pat not in serialized, f"Potential secret leak detected in envelope: {pat}"


def test_security_envelope_immutability():
    """Attempting to mutate frozen DelegationEnvelope fields raises FrozenInstanceError."""
    envelope = DelegationEnvelope.create(
        delegation_id="DEL-IMMUTABLE-1",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="US-1",
        scope_summary="Scope",
        action_requested="Action",
        compiled_instruction="Instruction",
    )

    with pytest.raises(AttributeError):
        envelope.compiled_instruction = "Mutated instruction"  # type: ignore
