"""Host-Native Specialist Dispatch Module (Milestone R11).

Strictly stdlib-only. Implements hexagonal ports and adapters architecture for
dispatching specialist agents across Antigravity, Codex, Gemini CLI, Claude, and test doubles.
"""

from scripts.runtime.dispatch.contracts import (
    DispatchStatus,
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
    compute_evidence_hash,
    mint_dispatch_receipt,
)
from scripts.runtime.dispatch.errors import (
    DelegationEnvelopeNotReadyError,
    DispatchError,
    DispatchExecutionFailedError,
    DispatchIdempotencyConflictError,
    DispatchReceiptIntegrityError,
    DispatchRejectedError,
    DispatchRetryableError,
    DispatchTerminalError,
    DispatchTimeoutError,
    DuplicateDispatchError,
    HostCapabilityMismatchError,
    HostMisconfiguredError,
    HostResolutionError,
    HostUnavailableError,
    SessionInvalidForDispatchError,
    UnsupportedHostError,
)
from scripts.runtime.dispatch.host_registry import (
    HostRegistry,
    create_default_registry,
    default_host_registry,
)
from scripts.runtime.dispatch.port import HostDispatchPort
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService

__all__ = [
    "DelegationEnvelopeNotReadyError",
    "DispatchError",
    "DispatchExecutionFailedError",
    "DispatchIdempotencyConflictError",
    "DispatchReceiptIntegrityError",
    "DispatchRejectedError",
    "DispatchRepository",
    "DispatchRetryableError",
    "DispatchService",
    "DispatchStatus",
    "DispatchTerminalError",
    "DispatchTimeoutError",
    "DuplicateDispatchError",
    "HostCapabilityMismatchError",
    "HostDispatchPort",
    "HostDispatchResult",
    "HostExecutionBinding",
    "HostMisconfiguredError",
    "HostRegistry",
    "HostResolutionError",
    "HostStatusResult",
    "HostUnavailableError",
    "SessionInvalidForDispatchError",
    "UnsupportedHostError",
    "compute_evidence_hash",
    "create_default_registry",
    "default_host_registry",
    "mint_dispatch_receipt",
]
