"""Delegation Service for Milestone R10.

Coordinates MCP Session validation, deterministic Preflight enforcement,
DelegationEnvelope construction, and durable persistence.
Strictly stdlib-only. ZERO Host Dispatch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from scripts.domain.delegation import ActivationPacket, DelegationEnvelope
from .envelope import DelegationEnvelopeBuilder
from .errors import PreflightBlockedError, SessionError
from .preflight import PreflightDecision, PreflightResult, PreflightValidator
from .repository import DelegationRepository
from .sessions import CanonicalSessionManager


class DelegationService:
    """Canonical service that transforms an ActivationPacket into a validated DelegationEnvelope."""

    def __init__(
        self,
        runtime_root: Union[str, Path],
        db_path: Optional[Union[str, Path]] = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        if db_path is None:
            db_path = self.runtime_root / "banco" / "squad.db"
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.session_manager = CanonicalSessionManager(self.db_path)
        self.preflight_validator = PreflightValidator(
            runtime_root=self.runtime_root,
            session_manager=self.session_manager,
            db_path=self.db_path,
        )
        self.repository = DelegationRepository(self.db_path)

    def prepare_delegation(
        self,
        activation_packet: ActivationPacket,
        session_id: str,
        sender_role: str = "00-delivery-orchestrator",
        assignment_id: Optional[str] = None,
        paths: Optional[List[str]] = None,
        required_tools: Optional[List[str]] = None,
        context_fingerprint: Optional[str] = None,
        scope_summary: Optional[str] = None,
        action_requested: Optional[str] = None,
    ) -> Tuple[DelegationEnvelope, PreflightResult]:
        """Transforms a ready ActivationPacket into a validated, session-bound DelegationEnvelope.

        Idempotent: Identical activation_id and active session_revision returns existing envelope.
        Fails closed on any session, preflight, or contract failure.
        """
        # 1. Fetch & Validate Session (fail closed)
        session_data = self.session_manager.get_session(session_id, fail_closed=True)
        session_revision = int(session_data["revision"])

        # 2. Check Idempotent Cache in Repository
        activation_id = activation_packet.session_id
        existing = self.repository.find_existing(
            activation_id=activation_id,
            session_id=session_id,
            session_revision=session_revision,
        )
        if existing:
            cached_preflight = PreflightResult(
                decision=PreflightDecision.ALLOW,
                preflight_id="PRF-IDEMPOTENT-REUSED",
                session_id=session_id,
                activation_id=activation_id,
                checks=[],
                reasons=["Idempotent reuse of existing valid delegation envelope"],
            )
            return existing, cached_preflight

        # 3. Deterministic Preflight
        preflight_result = self.preflight_validator.validate(
            session_id=session_id,
            activation_packet=activation_packet,
            paths=paths,
            required_tools=required_tools,
        )

        resolved_fingerprint = context_fingerprint or hashlib.sha256(
            f"{activation_packet.work_item_id}:{activation_packet.work_context.current_stage}".encode()
        ).hexdigest()
        resolved_assignment_id = assignment_id or f"ASN-{activation_packet.work_item_id}"

        # 4. Handle Blocked Preflight
        if preflight_result.decision != PreflightDecision.ALLOW:
            # Persist BLOCKED record for audit trail
            blocked_envelope_id = f"DEL-BLOCKED-{uuid.uuid4().hex[:8]}"
            reasons_str = "; ".join(preflight_result.reasons) or "Preflight checks failed"

            # Create synthetic envelope for audit record
            blocked_env = DelegationEnvelope.create(
                delegation_id=blocked_envelope_id,
                sender_role=sender_role,
                target_role=activation_packet.role_name,
                work_item_id=activation_packet.work_item_id,
                scope_summary="BLOCKED DELEGATION",
                action_requested="BLOCKED",
                compiled_instruction=activation_packet.compiled_instruction,
            )

            self.repository.save(
                envelope=blocked_env,
                activation_id=activation_id,
                assignment_id=resolved_assignment_id,
                session_id=session_id,
                session_revision=session_revision,
                project_id=activation_packet.work_context.project_id,
                selected_agent_id=activation_packet.agent_id,
                stage=activation_packet.work_context.current_stage,
                status="BLOCKED",
                context_fingerprint=resolved_fingerprint,
                preflight_id=preflight_result.preflight_id,
                preflight_decision=preflight_result.decision.value,
            )

            raise PreflightBlockedError(
                f"Delegation preparation blocked by preflight: {reasons_str}"
            )

        # 5. Build Canonical DelegationEnvelope
        envelope = DelegationEnvelopeBuilder.build(
            activation_packet=activation_packet,
            preflight_result=preflight_result,
            sender_role=sender_role,
            scope_summary=scope_summary,
            action_requested=action_requested,
        )

        # 6. Persist to SQLite
        self.repository.save(
            envelope=envelope,
            activation_id=activation_id,
            assignment_id=resolved_assignment_id,
            session_id=session_id,
            session_revision=session_revision,
            project_id=activation_packet.work_context.project_id,
            selected_agent_id=activation_packet.agent_id,
            stage=activation_packet.work_context.current_stage,
            status="READY_FOR_DISPATCH",
            context_fingerprint=resolved_fingerprint,
            preflight_id=preflight_result.preflight_id,
            preflight_decision=preflight_result.decision.value,
        )

        return envelope, preflight_result
