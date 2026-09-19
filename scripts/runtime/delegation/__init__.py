"""Canonical Delegation Package for Milestone R10.

Includes Session Authority, Preflight Engine, Delegation Envelope Builder,
Repository, and Service.
"""

from .envelope import DelegationEnvelopeBuilder
from .errors import (
    ActivationStaleError,
    AssignmentStaleError,
    DelegationError,
    LifecycleStaleError,
    PathContainmentError,
    PreflightBlockedError,
    PreflightError,
    SessionBlockedError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionProjectMismatchError,
    SessionRevisionConflictError,
    SessionWorkItemMismatchError,
    ToolRequirementMissingError,
)
from .preflight import (
    PreflightCheckResult,
    PreflightDecision,
    PreflightResult,
    PreflightValidator,
)
from .repository import DelegationRepository
from .service import DelegationService
from .sessions import (
    CanonicalSessionManager,
    DEFAULT_SESSION_TTL_SECONDS,
    MCPSession,
    compute_capability_hash,
)

__all__ = [
    "ActivationStaleError",
    "AssignmentStaleError",
    "CanonicalSessionManager",
    "compute_capability_hash",
    "DEFAULT_SESSION_TTL_SECONDS",
    "DelegationEnvelopeBuilder",
    "DelegationError",
    "DelegationRepository",
    "DelegationService",
    "LifecycleStaleError",
    "MCPSession",
    "PathContainmentError",
    "PreflightBlockedError",
    "PreflightCheckResult",
    "PreflightDecision",
    "PreflightError",
    "PreflightResult",
    "PreflightValidator",
    "SessionBlockedError",
    "SessionError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "SessionProjectMismatchError",
    "SessionRevisionConflictError",
    "SessionWorkItemMismatchError",
    "ToolRequirementMissingError",
]
