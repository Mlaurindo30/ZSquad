"""Validation and Segregation of Duties (SoD) enforcement engine.

Strictly stdlib-only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from scripts.domain.receipts import (
    BaseReceipt,
    ExecutionReceipt,
    GovernanceReceipt,
    QAReceipt,
    ReceiptType,
    ReviewReceipt,
    SecurityReceipt,
    TestReceipt,
    assert_sod_compliance,
)
from scripts.runtime.execution.errors import (
    InvalidEvidenceError,
    SoDViolationError,
    StageIneligibleError,
)


def normalize_logical_agent_id(agent_id: str) -> str:
    """Normalizes agent identifier to canonical logical ID, stripping dispatch suffixes, instances, or accounts."""
    if not agent_id:
        return ""
    # E.g. "06-software-engineer#dispatch-123" -> "06-software-engineer"
    # "06-software-engineer:attempt-1" -> "06-software-engineer"
    cleaned = agent_id.split("#")[0].split(":")[0].strip()
    return cleaned


def validate_sod(
    execution_receipt: ExecutionReceipt,
    validator_receipt: BaseReceipt,
) -> None:
    """Enforces Segregation of Duties (SoD) between author and validator."""
    exec_logical = normalize_logical_agent_id(execution_receipt.agent_id)
    val_logical = normalize_logical_agent_id(validator_receipt.agent_id)
    if exec_logical == val_logical:
        raise SoDViolationError(
            f"Segregation of Duties (SoD) violation: implementer '{execution_receipt.agent_id}' "
            f"(logical: '{exec_logical}') cannot act as validator/reviewer in receipt '{validator_receipt.receipt_id}'"
        )
    try:
        assert_sod_compliance(execution_receipt, validator_receipt)
    except Exception as exc:
        raise SoDViolationError(str(exc)) from exc


def validate_execution_receipt(receipt: ExecutionReceipt) -> None:
    """Validates that execution receipt represents a valid, complete implementation."""
    if not receipt.diff_summary or not receipt.diff_summary.strip():
        raise InvalidEvidenceError("diff_summary must not be empty")
    if receipt.test_exit_code != 0:
        raise InvalidEvidenceError(
            f"Execution receipt has failing test exit code: {receipt.test_exit_code}"
        )


def validate_review_receipt(
    receipt: ReviewReceipt,
    execution_receipt: Optional[ExecutionReceipt] = None,
) -> None:
    """Validates peer review receipt and enforces SoD."""
    if receipt.verdict != "APPROVED":
        raise InvalidEvidenceError(
            f"Review receipt verdict is not APPROVED (found '{receipt.verdict}')"
        )
    if execution_receipt:
        validate_sod(execution_receipt, receipt)


def validate_security_receipt(
    receipt: SecurityReceipt,
    execution_receipt: Optional[ExecutionReceipt] = None,
) -> None:
    """Validates security analysis receipt and enforces zero criticals and SoD."""
    if receipt.critical_count > 0:
        raise InvalidEvidenceError(
            f"Security receipt contains {receipt.critical_count} critical vulnerabilities"
        )
    if receipt.verdict not in ("APPROVED", "NOT_REQUIRED"):
        raise InvalidEvidenceError(
            f"Security receipt verdict is neither APPROVED nor NOT_REQUIRED (found '{receipt.verdict}')"
        )
    if execution_receipt:
        validate_sod(execution_receipt, receipt)


def validate_test_receipt(
    receipt: TestReceipt,
    execution_receipt: Optional[ExecutionReceipt] = None,
) -> None:
    """Validates automated testing receipt and enforces zero test failures."""
    if receipt.failed_tests > 0:
        raise InvalidEvidenceError(
            f"Test receipt has {receipt.failed_tests} failed tests"
        )
    if receipt.total_tests <= 0:
        raise InvalidEvidenceError("Test receipt has 0 total tests executed")
    if execution_receipt:
        validate_sod(execution_receipt, receipt)


def validate_qa_receipt(
    receipt: QAReceipt,
    execution_receipt: Optional[ExecutionReceipt] = None,
) -> None:
    """Validates QA/BDD verification receipt."""
    if receipt.bdd_exit_code != 0:
        raise InvalidEvidenceError(
            f"QA receipt has failing BDD exit code: {receipt.bdd_exit_code}"
        )
    if receipt.verdict != "APPROVED":
        raise InvalidEvidenceError(
            f"QA receipt verdict is not APPROVED (found '{receipt.verdict}')"
        )
    if execution_receipt:
        validate_sod(execution_receipt, receipt)
