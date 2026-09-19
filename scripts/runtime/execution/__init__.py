"""Canonical Execution, Validation Receipts, and Segregation of Duties Package.

Strictly stdlib-only. Sole authoritative execution evidence layer.
"""

from scripts.runtime.execution.errors import (
    ExecutionReceiptError,
    GatePrerequisiteError,
    InvalidEvidenceError,
    SoDViolationError,
    StageIneligibleError,
    TamperedReceiptError,
)
from scripts.runtime.execution.evidence import (
    hash_evidence_payload,
    hash_file,
    verify_evidence_hash,
)
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService
from scripts.runtime.execution.stage_policy import (
    STAGE_RECEIPT_REQUIREMENTS,
    StageReceiptRequirement,
    get_stage_receipt_requirement,
)
from scripts.runtime.execution.validation import (
    normalize_logical_agent_id,
    validate_execution_receipt,
    validate_qa_receipt,
    validate_review_receipt,
    validate_security_receipt,
    validate_sod,
    validate_test_receipt,
)

__all__ = [
    "ExecutionReceiptError",
    "InvalidEvidenceError",
    "SoDViolationError",
    "GatePrerequisiteError",
    "StageIneligibleError",
    "TamperedReceiptError",
    "hash_evidence_payload",
    "hash_file",
    "verify_evidence_hash",
    "ExecutionReceiptRepository",
    "ExecutionReceiptService",
    "STAGE_RECEIPT_REQUIREMENTS",
    "StageReceiptRequirement",
    "get_stage_receipt_requirement",
    "normalize_logical_agent_id",
    "validate_execution_receipt",
    "validate_review_receipt",
    "validate_security_receipt",
    "validate_test_receipt",
    "validate_qa_receipt",
    "validate_sod",
]
