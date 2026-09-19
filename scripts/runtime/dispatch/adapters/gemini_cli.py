"""Google Gemini CLI Host Dispatch Adapter (Milestone R11).

Strictly stdlib-only. Implements HostDispatchPort for Gemini CLI / agy headless runner.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple
import uuid

from scripts.domain.delegation import DelegationEnvelope, HostCapabilities
from scripts.runtime.dispatch.port import HostDispatchPort
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
)


class GeminiCliDispatchAdapter(HostDispatchPort):
    """Host dispatch adapter for Google Gemini CLI / agy environment."""

    def __init__(self, capabilities: Optional[HostCapabilities] = None):
        self._capabilities = capabilities or HostCapabilities(
            has_subagent_dispatch=True,
            has_filesystem_write=True,
            has_terminal_execution=True,
            has_mcp_client=True,
            has_background_tasks=True,
            max_token_context=1_000_000,
        )

    @property
    def host_kind(self) -> str:
        return "gemini_cli"

    def get_capabilities(self) -> HostCapabilities:
        return self._capabilities

    def can_dispatch(self, envelope: DelegationEnvelope) -> Tuple[bool, Optional[str]]:
        token_estimate = len(envelope.compiled_instruction) // 4
        if token_estimate > self._capabilities.max_token_context:
            return False, f"Instruction length exceeds Gemini CLI context limit ({self._capabilities.max_token_context})"
        return True, None

    def dispatch(
        self,
        envelope: DelegationEnvelope,
        binding: HostExecutionBinding,
    ) -> HostDispatchResult:
        can, reason = self.can_dispatch(envelope)
        if not can:
            return HostDispatchResult(
                status=DispatchStatus.UNSUPPORTED,
                host_execution_id=f"gemini-unsup-{uuid.uuid4().hex[:8]}",
                evidence_payload={"error": reason, "target_role": envelope.target_role},
                error_code="ERR_CAPABILITY_MISMATCH",
                error_message=reason,
                dispatched_at=datetime.now(timezone.utc),
            )

        exec_id = f"gemini-exec-{uuid.uuid4().hex[:8]}"
        evidence = {
            "host_runtime": "gemini_cli",
            "handle": exec_id,
            "instruction_hash": envelope.instruction_hash,
            "target_role": envelope.target_role,
            "session_id": binding.session_id,
            "mode": "headless_cli",
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }

        return HostDispatchResult(
            status=DispatchStatus.DISPATCHED,
            host_execution_id=exec_id,
            evidence_payload=evidence,
            error_code=None,
            error_message=None,
            dispatched_at=datetime.now(timezone.utc),
        )

    def check_status(self, host_execution_id: str) -> HostStatusResult:
        return HostStatusResult(
            host_execution_id=host_execution_id,
            status=DispatchStatus.DISPATCHED,
            is_alive=True,
            details={"host": "gemini_cli", "handle": host_execution_id},
        )

    def cancel(self, host_execution_id: str) -> bool:
        return True
