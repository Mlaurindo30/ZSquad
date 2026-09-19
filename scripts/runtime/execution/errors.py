"""Errors and exceptions for execution receipt recording and validation.

Strictly stdlib-only.
"""


class ExecutionReceiptError(Exception):
    """Base exception for execution receipt operations."""
    pass


class InvalidEvidenceError(ExecutionReceiptError):
    """Raised when evidence is invalid, missing, unparseable, or hash mismatch."""
    pass


class SoDViolationError(ExecutionReceiptError):
    """Raised when Segregation of Duties (SoD) is violated."""
    pass


class GatePrerequisiteError(ExecutionReceiptError):
    """Raised when gate prerequisites are not met."""
    pass


class StageIneligibleError(ExecutionReceiptError):
    """Raised when an operation or receipt is not permitted for the given stage."""
    pass


class TamperedReceiptError(ExecutionReceiptError):
    """Raised when a receipt's content does not match its hash or signature."""
    pass
