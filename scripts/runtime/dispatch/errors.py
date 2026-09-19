"""Typed Exception Hierarchy for Host-Native Specialist Dispatch (Milestone R11).

Strictly stdlib-only. Fail-closed error taxonomy.
"""

from __future__ import annotations

from typing import Optional


class DispatchError(Exception):
    """Base exception for all dispatch-related failures."""

    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or "ERR_DISPATCH_GENERIC"


class UnsupportedHostError(DispatchError):
    """Raised when an unknown, unsupported, or incompatible host kind is requested."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_UNRESOLVED"):
        super().__init__(message, error_code=error_code)


class HostResolutionError(UnsupportedHostError):
    """Raised when the runtime is unable to resolve a valid host adapter."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_UNRESOLVED"):
        super().__init__(message, error_code=error_code)


class HostUnavailableError(DispatchError):
    """Raised when the resolved host substrate is currently unreachable or offline."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_UNAVAILABLE"):
        super().__init__(message, error_code=error_code)


class HostMisconfiguredError(DispatchError):
    """Raised when the host substrate environment or binary is improperly configured."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_MISCONFIGURED"):
        super().__init__(message, error_code=error_code)


class DispatchRejectedError(DispatchError):
    """Raised when the host rejects execution, e.g. capability mismatch or policy guard."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_CAPABILITY_MISMATCH"):
        super().__init__(message, error_code=error_code)


class HostCapabilityMismatchError(DispatchRejectedError):
    """Raised when the resolved host lacks required capabilities for the envelope."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_CAPABILITY_MISMATCH"):
        super().__init__(message, error_code=error_code)


class DispatchTimeoutError(DispatchError):
    """Raised when a host subagent spawn or dispatch operation times out."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_SPAWN_TIMEOUT"):
        super().__init__(message, error_code=error_code)


class DuplicateDispatchError(DispatchError):
    """Raised when a conflicting or duplicate dispatch is attempted in violation of concurrency controls."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_CONCURRENT_DISPATCH_CONFLICT"):
        super().__init__(message, error_code=error_code)


class DispatchIdempotencyConflictError(DuplicateDispatchError):
    """Raised when an in-flight dispatch collides with a concurrent dispatch attempt."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_CONCURRENT_DISPATCH_CONFLICT"):
        super().__init__(message, error_code=error_code)


class SessionInvalidForDispatchError(DispatchError):
    """Raised when the associated MCP session is expired, blocked, or invalid for dispatch."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_SESSION_INVALID"):
        super().__init__(message, error_code=error_code)


class DelegationEnvelopeNotReadyError(DispatchError):
    """Raised when attempting to dispatch a delegation envelope not in READY_FOR_DISPATCH state."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_ENVELOPE_NOT_READY"):
        super().__init__(message, error_code=error_code)


class DispatchExecutionFailedError(DispatchError):
    """Raised when the underlying host invocation fails."""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = "ERR_HOST_INVOCATION_FAILED",
        retryable: bool = False,
    ):
        super().__init__(message, error_code=error_code)
        self.retryable = retryable


class DispatchRetryableError(DispatchExecutionFailedError):
    """Raised on transient, retryable host failure (e.g. rate limit, spawn timeout)."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_SPAWN_TIMEOUT"):
        super().__init__(message, error_code=error_code, retryable=True)


class DispatchTerminalError(DispatchExecutionFailedError):
    """Raised on permanent, non-retryable host failure (e.g. missing binary, fatal syntax error)."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_HOST_INVOCATION_FAILED"):
        super().__init__(message, error_code=error_code, retryable=False)


class DispatchReceiptIntegrityError(DispatchError):
    """Raised when hash verification fails on a minted DispatchReceipt."""

    def __init__(self, message: str, error_code: Optional[str] = "ERR_INSTRUCTION_HASH_MISMATCH"):
        super().__init__(message, error_code=error_code)
