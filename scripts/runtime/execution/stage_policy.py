"""Stage execution and validation policy definitions.

Strictly stdlib-only.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.domain.receipts import ReceiptType


class StageReceiptRequirement:
    """Defines the receipts required for a stage to be considered complete."""

    def __init__(
        self,
        stage: LifecycleStage,
        required_receipt_types: List[ReceiptType],
        associated_gate: Optional[GateId] = None,
        allowed_roles: Optional[Set[str]] = None,
    ) -> None:
        self.stage = stage
        self.required_receipt_types = required_receipt_types
        self.associated_gate = associated_gate
        self.allowed_roles = allowed_roles or set()


STAGE_RECEIPT_REQUIREMENTS: Dict[LifecycleStage, StageReceiptRequirement] = {
    LifecycleStage.IMPLEMENTATION: StageReceiptRequirement(
        stage=LifecycleStage.IMPLEMENTATION,
        required_receipt_types=[ReceiptType.EXECUTION],
        associated_gate=None,
    ),
    LifecycleStage.CODE_REVIEW: StageReceiptRequirement(
        stage=LifecycleStage.CODE_REVIEW,
        required_receipt_types=[ReceiptType.REVIEW],
        associated_gate=None,
        allowed_roles={"09-code-reviewer", "04-solution-architect", "arthemis@"},
    ),
    LifecycleStage.SECURITY_REVIEW: StageReceiptRequirement(
        stage=LifecycleStage.SECURITY_REVIEW,
        required_receipt_types=[ReceiptType.SECURITY],
        associated_gate=GateId.G4_CODE_SECURITY,
        allowed_roles={"10-security-reviewer", "34-offensive-cyber-operator", "arthemis@"},
    ),
    LifecycleStage.TEST_VALIDATION: StageReceiptRequirement(
        stage=LifecycleStage.TEST_VALIDATION,
        required_receipt_types=[ReceiptType.TEST],
        associated_gate=None,
        allowed_roles={"11-test-engineer", "arthemis@"},
    ),
    LifecycleStage.QA_VALIDATION: StageReceiptRequirement(
        stage=LifecycleStage.QA_VALIDATION,
        required_receipt_types=[ReceiptType.QA],
        associated_gate=GateId.G5_QUALITY,
        allowed_roles={"12-qa-engineer", "arthemis@"},
    ),
    LifecycleStage.GOVERNANCE_RELEASE: StageReceiptRequirement(
        stage=LifecycleStage.GOVERNANCE_RELEASE,
        required_receipt_types=[ReceiptType.GOVERNANCE],
        associated_gate=GateId.G6_GOVERNANCE_RELEASE,
        allowed_roles={"14-governance-auditor", "arthemis@", "human_master"},
    ),
}


def get_stage_receipt_requirement(stage: LifecycleStage) -> Optional[StageReceiptRequirement]:
    return STAGE_RECEIPT_REQUIREMENTS.get(stage)
