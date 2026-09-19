"""Delegation Envelope Builder for Milestone R10.

Constructs canonical host-neutral, provider-neutral DelegationEnvelope instances
from preflight-approved ActivationPackets and active MCP sessions.
Strictly stdlib-only.
"""

from __future__ import annotations

import hashlib
from typing import Optional
import uuid

from scripts.domain.delegation import ActivationPacket, DelegationEnvelope
from .errors import PreflightBlockedError
from .preflight import PreflightDecision, PreflightResult


class DelegationEnvelopeBuilder:
    """Canonical factory for constructing authoritative DelegationEnvelopes."""

    @staticmethod
    def build(
        activation_packet: ActivationPacket,
        preflight_result: PreflightResult,
        sender_role: str = "00-delivery-orchestrator",
        delegation_id: Optional[str] = None,
        scope_summary: Optional[str] = None,
        action_requested: Optional[str] = None,
    ) -> DelegationEnvelope:
        """Constructs an authoritative DelegationEnvelope verbatim from ActivationPacket.

        Raises:
            PreflightBlockedError: If preflight did not return ALLOW.
        """
        if preflight_result.decision != PreflightDecision.ALLOW:
            reasons_str = "; ".join(preflight_result.reasons) or "Preflight checks failed"
            raise PreflightBlockedError(
                f"Cannot build DelegationEnvelope: preflight is BLOCKED ({reasons_str})"
            )

        assigned_id = delegation_id or f"DEL-{uuid.uuid4().hex[:12]}"
        resolved_scope = (
            scope_summary
            or activation_packet.work_context.title
            or "Specialist task execution"
        )
        resolved_action = (
            action_requested
            or f"Execute lifecycle stage {activation_packet.work_context.current_stage}"
        )

        # Invariant: compiled_instruction and instruction_hash are passed verbatim
        return DelegationEnvelope.create(
            delegation_id=assigned_id,
            sender_role=sender_role,
            target_role=activation_packet.role_name,
            work_item_id=activation_packet.work_item_id,
            scope_summary=resolved_scope,
            action_requested=resolved_action,
            compiled_instruction=activation_packet.compiled_instruction,
        )
