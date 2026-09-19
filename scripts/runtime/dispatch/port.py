"""HostDispatchPort Protocol for Host-Native Specialist Dispatch (Milestone R11).

Strictly stdlib-only. Hexagonal outbound port decoupling Agent Squad control plane
from concrete host platforms.
"""

from __future__ import annotations

from typing import Optional, Protocol, Tuple, runtime_checkable

from scripts.domain.delegation import DelegationEnvelope, HostCapabilities
from scripts.runtime.dispatch.receipts import (
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
)


@runtime_checkable
class HostDispatchPort(Protocol):
    """Outbound port for host-native specialist agent dispatch."""

    @property
    def host_kind(self) -> str:
        """Returns unique host kind identifier (e.g. 'antigravity', 'codex', 'gemini_cli', 'claude', 'fake')."""
        ...

    def get_capabilities(self) -> HostCapabilities:
        """Reports static and runtime-probed capabilities of this host."""
        ...

    def can_dispatch(self, envelope: DelegationEnvelope) -> Tuple[bool, Optional[str]]:
        """Verifies whether this host can execute the given delegation envelope.

        Returns:
            (True, None) if supported.
            (False, reason_string) if unsupported.
        """
        ...

    def dispatch(
        self,
        envelope: DelegationEnvelope,
        binding: HostExecutionBinding,
    ) -> HostDispatchResult:
        """Executes host-native dispatch of the specialist agent.

        Must be idempotent where supported by host.
        Fails closed on any host communication or invocation failure.
        """
        ...

    def check_status(self, host_execution_id: str) -> HostStatusResult:
        """Queries the lifecycle state of an active or completed specialist dispatch."""
        ...

    def cancel(self, host_execution_id: str) -> bool:
        """Terminates or signals cancellation to the host specialist execution."""
        ...
