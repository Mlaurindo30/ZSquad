"""Typed error hierarchy for the Canonical Lifecycle Engine.

Strictly stdlib-only.
"""

from __future__ import annotations

try:
    from scripts.agent_squad import SquadError
except ImportError:
    try:
        from agent_squad import SquadError
    except ImportError:
        class SquadError(Exception):
            """Base Squad error fallback."""
            pass


class LifecycleError(SquadError):
    """Base exception for all lifecycle state machine and policy errors."""
    pass


class InvalidTransitionError(LifecycleError):
    """Raised when an illegal state transition is attempted according to the FSM or policy."""
    pass


class GateNotEligibleError(LifecycleError):
    """Raised when a gate evaluation is attempted while the work item is not in an eligible stage."""
    pass


class GateNotPassedError(LifecycleError):
    """Raised when a required gate has not been formally approved before transitioning."""
    pass


class HandoffPendingError(LifecycleError):
    """Raised when an active handoff is still pending acknowledgement, blocking advancement."""
    pass


class WIPLimitExceededError(LifecycleError):
    """Raised when admitting a work item into a stage would breach the project's WIP quota."""
    pass


class TimeboxExceededError(LifecycleError):
    """Raised when the maximum duration allowed for a stage has been exceeded."""
    pass


class ConfigurationError(LifecycleError):
    """Raised when lifecycle or cycle configuration is malformed, missing, or invalid."""
    pass


# Parity and backward-compatibility aliases
StageTransitionIllegalError = InvalidTransitionError
GatePrerequisiteViolationError = GateNotPassedError
GateEligibilityViolationError = GateNotEligibleError
HandoffNotAcknowledgedError = HandoffPendingError
ReceiptPrerequisiteViolationError = LifecycleError
