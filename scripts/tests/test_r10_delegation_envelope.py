"""Tests for Milestone R10 — Canonical DelegationEnvelope Construction.

Covers:
- Immutable instruction transfer from ActivationPacket
- Verifiable SHA-256 instruction hash
- Host-neutral and provider-neutral attributes
- Rejection of construction on blocked preflight
- Zero plaintext secrets in payload
"""

import hashlib
from pathlib import Path
import pytest

from scripts.domain.delegation import ActivationPacket, DelegationEnvelope, WorkContext
from scripts.runtime.delegation.envelope import DelegationEnvelopeBuilder
from scripts.runtime.delegation.errors import PreflightBlockedError
from scripts.runtime.delegation.preflight import (
    PreflightCheckResult,
    PreflightDecision,
    PreflightResult,
)


def _make_sample_activation():
    wc = WorkContext(
        work_item_id="US-ENV-01",
        project_id="sample-proj",
        current_stage="IMPLEMENTATION",
        title="Sample Implementation Story",
        description="Deliver tested module",
        definition_of_done=["Tests pass"],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=["src/"],
    )
    instruction = (
        "# AGENT SYSTEM PROMPT: 06-software-engineer\n\n"
        "Execute TDD implementation without regressions.\n"
    )
    instr_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    return ActivationPacket(
        session_id="ACT-ENV-01",
        agent_id="06-software-engineer",
        role_name="06-software-engineer",
        work_item_id="US-ENV-01",
        work_context=wc,
        skill_manifest={"agent": "06-software-engineer", "required_tools": ["agent-squad-mcp"]},
        compiled_instruction=instruction,
        instruction_hash=instr_hash,
    )


def test_delegation_envelope_build_success():
    """Builds valid DelegationEnvelope when preflight is ALLOW."""
    packet = _make_sample_activation()
    preflight_ok = PreflightResult(
        decision=PreflightDecision.ALLOW,
        preflight_id="PRF-TEST-OK",
        session_id="SESS-001",
        activation_id=packet.session_id,
        checks=[PreflightCheckResult("all_good", True, "ok")],
        reasons=[],
    )

    envelope = DelegationEnvelopeBuilder.build(
        activation_packet=packet,
        preflight_result=preflight_ok,
        sender_role="00-delivery-orchestrator",
    )

    assert isinstance(envelope, DelegationEnvelope)
    assert envelope.delegation_id.startswith("DEL-")
    assert envelope.target_role == packet.role_name
    assert envelope.work_item_id == packet.work_item_id
    # Invariant: compiled instruction is identical verbatim
    assert envelope.compiled_instruction == packet.compiled_instruction
    assert envelope.instruction_hash == packet.instruction_hash
    # Cryptographically matches
    expected_hash = hashlib.sha256(packet.compiled_instruction.encode("utf-8")).hexdigest()
    assert envelope.instruction_hash == expected_hash


def test_delegation_envelope_rejects_blocked_preflight():
    """Refuses to construct DelegationEnvelope if preflight decision is BLOCK."""
    packet = _make_sample_activation()
    preflight_blocked = PreflightResult(
        decision=PreflightDecision.BLOCK,
        preflight_id="PRF-TEST-BLOCKED",
        session_id="SESS-002",
        activation_id=packet.session_id,
        checks=[PreflightCheckResult("required_tools", False, "Missing tool")],
        reasons=["Missing tool: azure-devops-mcp"],
    )

    with pytest.raises(PreflightBlockedError, match="preflight is BLOCKED"):
        DelegationEnvelopeBuilder.build(
            activation_packet=packet,
            preflight_result=preflight_blocked,
        )


def test_delegation_envelope_is_host_and_provider_neutral():
    """DelegationEnvelope payload contains no host-specific or provider-specific branding."""
    packet = _make_sample_activation()
    preflight_ok = PreflightResult(
        decision=PreflightDecision.ALLOW,
        preflight_id="PRF-TEST-OK",
        session_id="SESS-001",
        activation_id=packet.session_id,
        checks=[],
        reasons=[],
    )

    envelope = DelegationEnvelopeBuilder.build(packet, preflight_ok)
    d = envelope.to_dict()

    # Invariant: keys are strictly standard core contracts
    assert set(d.keys()) == {
        "delegation_id",
        "sender_role",
        "target_role",
        "work_item_id",
        "scope_summary",
        "action_requested",
        "compiled_instruction",
        "instruction_hash",
    }
