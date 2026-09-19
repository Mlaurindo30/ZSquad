"""Antigravity Host Dispatch Adapter (Milestone R11).

Strictly stdlib-only. Implements HostDispatchPort for Google Antigravity IDE native subagent invocation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import uuid

from scripts.domain.delegation import DelegationEnvelope, HostCapabilities
from scripts.runtime.dispatch.port import HostDispatchPort
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
)


class AntigravityDispatchAdapter(HostDispatchPort):
    """Host dispatch adapter for Google Antigravity IDE environment."""

    def __init__(self, capabilities: Optional[HostCapabilities] = None):
        self._capabilities = capabilities or HostCapabilities(
            has_subagent_dispatch=True,
            has_filesystem_write=True,
            has_terminal_execution=True,
            has_mcp_client=True,
            has_background_tasks=True,
            max_token_context=2_000_000,
        )

    @property
    def host_kind(self) -> str:
        return "antigravity"

    def get_capabilities(self) -> HostCapabilities:
        return self._capabilities

    def can_dispatch(self, envelope: DelegationEnvelope) -> Tuple[bool, Optional[str]]:
        if not envelope.target_role or not envelope.target_role.strip():
            return False, "Target specialist role must not be empty"
        token_estimate = len(envelope.compiled_instruction) // 4
        if token_estimate > self._capabilities.max_token_context:
            return False, f"Instruction exceeds Antigravity context limit ({self._capabilities.max_token_context})"
        return True, None

    def build_subagent_spec(self, envelope: DelegationEnvelope) -> Dict[str, Any]:
        """Constructs the canonical invoke_subagent tool parameter structure."""
        return {
            "TypeName": "self",
            "Role": envelope.target_role,
            "Prompt": envelope.compiled_instruction,
            "Workspace": "inherit",
            "Model": "inherit",
        }

    def dispatch(
        self,
        envelope: DelegationEnvelope,
        binding: HostExecutionBinding,
    ) -> HostDispatchResult:
        can, reason = self.can_dispatch(envelope)
        if not can:
            return HostDispatchResult(
                status=DispatchStatus.UNSUPPORTED,
                host_execution_id=f"ag-unsup-{uuid.uuid4().hex[:8]}",
                evidence_payload={"error": reason, "target_role": envelope.target_role},
                error_code="ERR_CAPABILITY_MISMATCH",
                error_message=reason,
                dispatched_at=datetime.now(timezone.utc),
            )

        subagent_spec = self.build_subagent_spec(envelope)
        exec_id = f"ag-subagent-{uuid.uuid4().hex[:8]}"

        evidence = {
            "host_runtime": "antigravity",
            "handle": exec_id,
            "subagent_spec": {
                "TypeName": subagent_spec["TypeName"],
                "Role": subagent_spec["Role"],
                "Workspace": subagent_spec["Workspace"],
                "Model": subagent_spec["Model"],
                "Prompt_length": len(subagent_spec["Prompt"]),
            },
            "instruction_hash": envelope.instruction_hash,
            "session_id": binding.session_id,
            "work_item_id": envelope.work_item_id,
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
            details={"host": "antigravity", "handle": host_execution_id},
        )

    def cancel(self, host_execution_id: str) -> bool:
        return True
