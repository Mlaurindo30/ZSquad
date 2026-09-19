"""Typed errors for MCP Session Authority, Preflight Verification and Delegation.

Strictly stdlib-only.
"""

from __future__ import annotations


class DelegationError(Exception):
    """Base error for all delegation and session operations."""


class SessionError(DelegationError):
    """Base error for session authority operations."""


class SessionNotFoundError(SessionError, ValueError):
    """Raised when a requested session is absent or not recognized."""


class SessionExpiredError(SessionError, ValueError):
    """Raised when a session TTL has elapsed and the session is no longer valid."""


class SessionBlockedError(SessionError, ValueError):
    """Raised when a session has been marked blocked by governance or failure."""


class SessionRevisionConflictError(SessionError, ValueError):
    """Raised when the caller's last_revision does not match the active session revision."""


class SessionProjectMismatchError(SessionError, ValueError):
    """Raised when session project does not match the activation or bound project."""


class SessionWorkItemMismatchError(SessionError, ValueError):
    """Raised when session is bound to a different work item than the activation target."""


class PreflightError(DelegationError):
    """Base error for preflight verification failures."""


class PreflightBlockedError(PreflightError):
    """Raised when preflight evaluation determines execution must be blocked."""


class ActivationStaleError(PreflightBlockedError):
    """Raised when activation packet context fingerprint is stale relative to current artifacts."""


class AssignmentStaleError(PreflightBlockedError):
    """Raised when underlying R8 execution assignment is no longer active."""


class LifecycleStaleError(PreflightBlockedError):
    """Raised when work item lifecycle stage has drifted from activation stage."""


class ToolRequirementMissingError(PreflightBlockedError):
    """Raised when a required control-plane or execution tool is unavailable in session."""


class PathContainmentError(PreflightBlockedError):
    """Raised when requested execution paths violate project containment or do not exist."""
