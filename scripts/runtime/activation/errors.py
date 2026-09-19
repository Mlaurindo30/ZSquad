"""Activation error hierarchy for Milestone R9.

Strictly stdlib-only.
"""


class ActivationError(Exception):
    """Base exception for all activation package failures."""


class IncompleteContextError(ActivationError):
    """Raised when work item context or ancestor lineage cannot be resolved."""


class SkillResolutionError(ActivationError):
    """Base exception for skill resolution failures."""


class SkillNotFoundError(SkillResolutionError):
    """Raised when a required native or assigned skill cannot be found on disk."""


class SkillBudgetExceededError(SkillResolutionError):
    """Raised when total selected skills exceed the canonical cognitive budget (<= 7)."""


class CompilationError(ActivationError):
    """Raised when specialist instruction compilation fails."""


class ActivationPersistenceError(ActivationError):
    """Raised when activation packet cannot be persisted or retrieved from database."""
