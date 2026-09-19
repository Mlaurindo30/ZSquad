"""Operational Errors and Exceptions (Milestone R13).

Strictly stdlib-only.
"""

from __future__ import annotations


class OperationsError(Exception):
    """Base exception for all operational control loop errors."""


class JobNotFoundError(OperationsError):
    """Raised when a referenced scheduled job does not exist."""


class JobAlreadyExistsError(OperationsError):
    """Raised when attempting to insert a duplicate scheduled job."""


class LeaseAcquisitionError(OperationsError):
    """Raised when an operational lease cannot be acquired."""


class LeaseExpiredError(OperationsError):
    """Raised when attempting to operate under an expired lease."""


class AuthorityViolationError(OperationsError):
    """Raised when an operational component attempts to violate subsystem authority."""


class ReconciliationError(OperationsError):
    """Raised when an operational reconciliation task encounters an unrecoverable failure."""
