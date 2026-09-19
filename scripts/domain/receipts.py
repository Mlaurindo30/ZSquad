"""Domain models for Specialist Receipts, Evidence Signoffs and Segregation of Duties (SoD).

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .common import BaseDomainModel, ValidationError


class ReceiptType(str, Enum):
    DISPATCH = "DISPATCH"
    EXECUTION = "EXECUTION"
    REVIEW = "REVIEW"
    SECURITY = "SECURITY"
    TEST = "TEST"
    QA = "QA"
    GOVERNANCE = "GOVERNANCE"
    DISCOVERY = "DISCOVERY"
    REQUIREMENTS = "REQUIREMENTS"
    SCAFFOLDING = "SCAFFOLDING"


@dataclass(frozen=True)
class BaseReceipt(BaseDomainModel):
    """Immutable evidence record attesting specialist execution."""

    receipt_id: str
    receipt_type: str
    work_item_id: str
    agent_id: str
    instruction_hash: str
    evidence_hash: str
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.receipt_id or not self.receipt_id.strip():
            raise ValidationError("receipt_id must not be empty")
        if not self.receipt_type or not self.receipt_type.strip():
            raise ValidationError("receipt_type must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.agent_id or not self.agent_id.strip():
            raise ValidationError("agent_id must not be empty")
        if not self.instruction_hash or not self.instruction_hash.strip():
            raise ValidationError("instruction_hash must not be empty")
        if not self.evidence_hash or not self.evidence_hash.strip():
            raise ValidationError("evidence_hash must not be empty")


@dataclass(frozen=True)
class DispatchReceipt(BaseReceipt):
    """Receipt proving successful dispatch to a specialist agent."""

    target_agent_id: str = ""
    delegation_id: str = ""
    dispatched_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.target_agent_id:
            raise ValidationError("target_agent_id must not be empty")
        if not self.delegation_id:
            raise ValidationError("delegation_id must not be empty")


@dataclass(frozen=True)
class ExecutionReceipt(BaseReceipt):
    """Receipt proving implementation execution with diffs and test results."""

    files_modified: List[str] = field(default_factory=list)
    tests_executed: List[str] = field(default_factory=list)
    test_exit_code: int = 0
    diff_summary: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.diff_summary:
            raise ValidationError("diff_summary must not be empty")


@dataclass(frozen=True)
class ReviewReceipt(BaseReceipt):
    """Receipt proving independent peer code review and architectural validation."""

    reviewer_role: str = ""
    verdict: str = "APPROVED"  # "APPROVED", "CHANGES_REQUESTED"
    comments: List[str] = field(default_factory=list)
    reviewed_files: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.reviewer_role:
            raise ValidationError("reviewer_role must not be empty")
        if self.verdict not in ("APPROVED", "CHANGES_REQUESTED"):
            raise ValidationError(f"Invalid review verdict: {self.verdict}")


@dataclass(frozen=True)
class SecurityReceipt(BaseReceipt):
    """Receipt proving static security analysis and secret leakage verification."""

    security_role: str = ""
    vulnerabilities_detected: int = 0
    critical_count: int = 0
    sast_tool_output: str = ""
    verdict: str = "APPROVED"  # "APPROVED", "REJECTED"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.security_role:
            raise ValidationError("security_role must not be empty")
        if self.verdict not in ("APPROVED", "REJECTED"):
            raise ValidationError(f"Invalid security verdict: {self.verdict}")
        if self.critical_count > 0 and self.verdict == "APPROVED":
            raise ValidationError("Security review cannot be APPROVED when critical vulnerabilities exist")


@dataclass(frozen=True)
class TestReceipt(BaseReceipt):
    """Receipt proving automated test suite execution and coverage metrics."""

    tester_role: str = ""
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    coverage_percentage: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.tester_role:
            raise ValidationError("tester_role must not be empty")
        if self.failed_tests < 0 or self.passed_tests < 0:
            raise ValidationError("Test counts cannot be negative")


@dataclass(frozen=True)
class QAReceipt(BaseReceipt):
    """Receipt proving BDD scenario verification and quality signoff."""

    qa_role: str = ""
    scenarios_verified: int = 0
    bdd_exit_code: int = 0
    verdict: str = "APPROVED"  # "APPROVED", "REJECTED"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.qa_role:
            raise ValidationError("qa_role must not be empty")
        if self.verdict not in ("APPROVED", "REJECTED"):
            raise ValidationError(f"Invalid QA verdict: {self.verdict}")


@dataclass(frozen=True)
class GovernanceReceipt(BaseReceipt):
    """Receipt proving audit ledger signoff and compliance clearance."""

    auditor_role: str = ""
    gate_approvals: List[str] = field(default_factory=list)
    ledger_entry_id: str = ""
    compliance_verdict: str = "COMPLIANT"  # "COMPLIANT", "NON_COMPLIANT"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.auditor_role:
            raise ValidationError("auditor_role must not be empty")
        if not self.ledger_entry_id:
            raise ValidationError("ledger_entry_id must not be empty")
        if self.compliance_verdict not in ("COMPLIANT", "NON_COMPLIANT"):
            raise ValidationError(f"Invalid compliance verdict: {self.compliance_verdict}")


def assert_sod_compliance(execution_receipt: ExecutionReceipt, validator_receipt: BaseReceipt) -> None:
    """Enforces Segregation of Duties: Author cannot validate own work (R0-LIFE-006, Section 23)."""
    if execution_receipt.agent_id == validator_receipt.agent_id:
        raise ValidationError(
            f"Segregation of Duties (SoD) violation: implementer '{execution_receipt.agent_id}' "
            f"cannot act as validator/reviewer in receipt '{validator_receipt.receipt_id}'"
        )
