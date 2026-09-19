"""Deterministic Test Double Host Adapter (Milestone R11).

Strictly stdlib-only. Implements HostDispatchPort for hermetic testing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from scripts.domain.delegation import DelegationEnvelope, HostCapabilities
from scripts.runtime.dispatch.port import HostDispatchPort
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
)


class FakeHostAdapter(HostDispatchPort):
    """Deterministic in-memory host adapter for test execution."""

    def __init__(
        self,
        capabilities: Optional[HostCapabilities] = None,
        should_fail: bool = False,
        failure_status: DispatchStatus = DispatchStatus.FAILED_TERMINAL,
        failure_code: str = "ERR_HOST_INVOCATION_FAILED",
        failure_message: str = "Simulated test host failure",
        reject_dispatch: bool = False,
        rejection_reason: str = "Host policy rejection simulated",
    ):
        self._capabilities = capabilities or HostCapabilities(
            has_subagent_dispatch=True,
            has_filesystem_write=True,
            has_terminal_execution=True,
            has_mcp_client=True,
            has_background_tasks=True,
            max_token_context=1_000_000,
        )
        self.should_fail = should_fail
        self.failure_status = failure_status
        self.failure_code = failure_code
        self.failure_message = failure_message
        self.reject_dispatch = reject_dispatch
        self.rejection_reason = rejection_reason

        self.dispatches: List[Dict[str, Any]] = []
        self.cancellations: List[str] = []
        self.status_queries: List[str] = []

    @property
    def host_kind(self) -> str:
        return "fake"

    def get_capabilities(self) -> HostCapabilities:
        return self._capabilities

    def can_dispatch(self, envelope: DelegationEnvelope) -> Tuple[bool, Optional[str]]:
        if self.reject_dispatch:
            return False, self.rejection_reason
        # Check token budget if instruction is exceptionally large
        char_count = len(envelope.compiled_instruction)
        token_estimate = char_count // 4
        if token_estimate > self._capabilities.max_token_context:
            return False, f"Instruction length (~{token_estimate} tokens) exceeds host context window ({self._capabilities.max_token_context})"
        return True, None

    def dispatch(
        self,
        envelope: DelegationEnvelope,
        binding: HostExecutionBinding,
    ) -> HostDispatchResult:
        self.dispatches.append({"envelope": envelope, "binding": binding})

        if self.should_fail:
            return HostDispatchResult(
                status=self.failure_status,
                host_execution_id=f"fake-err-{uuid.uuid4().hex[:8]}",
                evidence_payload={
                    "error": self.failure_message,
                    "code": self.failure_code,
                    "target_role": envelope.target_role,
                },
                error_code=self.failure_code,
                error_message=self.failure_message,
                dispatched_at=datetime.now(timezone.utc),
            )

        exec_id = f"fake-exec-{uuid.uuid4().hex[:8]}"
        evidence = {
            "handle": exec_id,
            "host": self.host_kind,
            "sender": envelope.sender_role,
            "target": envelope.target_role,
            "work_item_id": envelope.work_item_id,
            "instruction_hash": envelope.instruction_hash,
            "session_id": binding.session_id,
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
        self.status_queries.append(host_execution_id)
        is_cancelled = host_execution_id in self.cancellations
        return HostStatusResult(
            host_execution_id=host_execution_id,
            status=DispatchStatus.CANCELLED if is_cancelled else DispatchStatus.DISPATCHED,
            is_alive=not is_cancelled,
            details={"queries_count": len(self.status_queries)},
        )

    def cancel(self, host_execution_id: str) -> bool:
        self.cancellations.append(host_execution_id)
        return True
