"""Domain models for Lifecycle Stages, Gates, Policies, Handoffs and Transitions.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from .common import BaseDomainModel, ValidationError


class LifecycleStage(str, Enum):
    """Canonical thirteen (13) delivery stages. Strictly non-renamable."""
    INTAKE = "INTAKE"
    DISCOVERY = "DISCOVERY"
    REQUIREMENTS_PRODUCT = "REQUIREMENTS_PRODUCT"
    PLANNING = "PLANNING"
    ARCHITECTURE_DESIGN = "ARCHITECTURE_DESIGN"
    READINESS_SCAFFOLDING = "READINESS_SCAFFOLDING"
    IMPLEMENTATION = "IMPLEMENTATION"
    CODE_REVIEW = "CODE_REVIEW"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    TEST_VALIDATION = "TEST_VALIDATION"
    QA_VALIDATION = "QA_VALIDATION"
    GOVERNANCE_RELEASE = "GOVERNANCE_RELEASE"
    DONE = "DONE"


class GateId(str, Enum):
    """Canonical governance gate checkpoints."""
    G1_PRODUCT = "G1-product"
    G2_DESIGN = "G2-design"
    G3_READINESS = "G3-readiness"
    G4_CODE_SECURITY = "G4-code-security"
    G5_QUALITY = "G5-quality"
    G6_GOVERNANCE_RELEASE = "G6-governance-release"


class GateDecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAIVED = "WAIVED"


class AcknowledgementStatus(str, Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class GateDecision(BaseDomainModel):
    """Formal decision recorded by an authorized evaluator at a gate boundary."""

    decision_id: str
    gate_id: GateId
    work_item_id: str
    stage: LifecycleStage
    evaluator_role: str
    evaluator_agent_id: str
    status: GateDecisionStatus
    rationale: str
    evidence_hashes: List[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.decision_id or not self.decision_id.strip():
            raise ValidationError("decision_id must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.evaluator_role or not self.evaluator_role.strip():
            raise ValidationError("evaluator_role must not be empty")
        if not self.evaluator_agent_id or not self.evaluator_agent_id.strip():
            raise ValidationError("evaluator_agent_id must not be empty")
        if not self.rationale or not self.rationale.strip():
            raise ValidationError("rationale must not be empty")


@dataclass(frozen=True)
class StagePolicy(BaseDomainModel):
    """Declarative execution and exit policy for a canonical stage."""

    stage: LifecycleStage
    owner_role: str
    collaborator_roles: List[str]
    required_receipt_types: List[str]
    required_artifacts: List[str]
    required_evidence: List[str]
    handoff_required: bool
    ack_required: bool
    required_gate: Optional[GateId]
    allowed_next_stages: List[LifecycleStage]
    wip_policy_ref: str
    timebox_policy_ref: str
    trigger_refs: List[str]

    def validate_exit(self, next_stage: LifecycleStage, gate_decision: Optional[GateDecision] = None) -> None:
        """Validates that all prerequisites to advance from this stage to next_stage are satisfied."""
        if next_stage not in self.allowed_next_stages:
            raise ValidationError(
                f"Stage transition illegal: cannot advance from {self.stage.value} to {next_stage.value}. "
                f"Allowed: {[s.value for s in self.allowed_next_stages]}"
            )

        if self.required_gate is not None:
            if gate_decision is None:
                raise ValidationError(
                    f"GatePrerequisiteViolation: Stage {self.stage.value} requires gate '{self.required_gate.value}' "
                    "approval before transition."
                )
            if gate_decision.gate_id != self.required_gate:
                raise ValidationError(
                    f"Gate mismatch: expected {self.required_gate.value}, got {gate_decision.gate_id.value}"
                )
            if gate_decision.status != GateDecisionStatus.APPROVED:
                raise ValidationError(
                    f"Gate approval required: gate {self.required_gate.value} status is '{gate_decision.status.value}'"
                )


@dataclass(frozen=True)
class Acknowledgement(BaseDomainModel):
    """Formal receipt of handoff acknowledgement by the target agent."""

    ack_id: str
    handoff_id: str
    recipient_agent_id: str
    status: AcknowledgementStatus
    reason: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.ack_id or not self.ack_id.strip():
            raise ValidationError("ack_id must not be empty")
        if not self.handoff_id or not self.handoff_id.strip():
            raise ValidationError("handoff_id must not be empty")
        if not self.recipient_agent_id or not self.recipient_agent_id.strip():
            raise ValidationError("recipient_agent_id must not be empty")


@dataclass(frozen=True)
class Handoff(BaseDomainModel):
    """Handoff state machine entity managing stage boundary transitions."""

    handoff_id: str
    work_item_id: str
    from_stage: LifecycleStage
    to_stage: LifecycleStage
    from_agent_id: str
    to_agent_id: str
    status: AcknowledgementStatus = AcknowledgementStatus.PENDING
    summary: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    acknowledged_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.handoff_id or not self.handoff_id.strip():
            raise ValidationError("handoff_id must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.from_agent_id or not self.from_agent_id.strip():
            raise ValidationError("from_agent_id must not be empty")
        if not self.to_agent_id or not self.to_agent_id.strip():
            raise ValidationError("to_agent_id must not be empty")

    def is_transition_allowed(self) -> bool:
        """Enforces R0-LIFE-004: PENDING strictly prohibits transition."""
        return self.status == AcknowledgementStatus.ACKNOWLEDGED


@dataclass(frozen=True)
class LifecycleTransition(BaseDomainModel):
    """Record of an actual state transition in the lifecycle engine."""

    transition_id: str
    work_item_id: str
    from_stage: LifecycleStage
    to_stage: LifecycleStage
    initiated_by: str
    gate_decision_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class DeliveryCycle(BaseDomainModel):
    """Defines an ordered delivery cycle progression."""

    cycle_id: str
    name: str
    stages: List[LifecycleStage]
    default_stage: LifecycleStage = LifecycleStage.INTAKE


CANONICAL_STAGE_POLICIES: Dict[LifecycleStage, StagePolicy] = {
    LifecycleStage.INTAKE: StagePolicy(
        stage=LifecycleStage.INTAKE,
        owner_role="00-delivery-orchestrator",
        collaborator_roles=["01-requirements-analyst"],
        required_receipt_types=[],
        required_artifacts=["intake.md"],
        required_evidence=[],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.DISCOVERY],
        wip_policy_ref="wip.intake.standard",
        timebox_policy_ref="timebox.intake.1d",
        trigger_refs=["trigger.intake.completed"],
    ),
    LifecycleStage.DISCOVERY: StagePolicy(
        stage=LifecycleStage.DISCOVERY,
        owner_role="01-requirements-analyst",
        collaborator_roles=["04-solution-architect", "40-agile-coach"],
        required_receipt_types=["DiscoveryReceipt"],
        required_artifacts=["discovery-notes.md"],
        required_evidence=["stakeholder_interviews"],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.REQUIREMENTS_PRODUCT],
        wip_policy_ref="wip.discovery.standard",
        timebox_policy_ref="timebox.discovery.3d",
        trigger_refs=["trigger.discovery.completed"],
    ),
    LifecycleStage.REQUIREMENTS_PRODUCT: StagePolicy(
        stage=LifecycleStage.REQUIREMENTS_PRODUCT,
        owner_role="01-requirements-analyst",
        collaborator_roles=["40-agile-coach", "14-governance-auditor"],
        required_receipt_types=["RequirementsReceipt"],
        required_artifacts=["user-story.md", "acceptance-criteria.md"],
        required_evidence=["gherkin_scenarios", "dod_statements"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G1_PRODUCT,
        allowed_next_stages=[LifecycleStage.PLANNING],
        wip_policy_ref="wip.requirements.standard",
        timebox_policy_ref="timebox.requirements.2d",
        trigger_refs=["trigger.requirements.g1_ready"],
    ),
    LifecycleStage.PLANNING: StagePolicy(
        stage=LifecycleStage.PLANNING,
        owner_role="40-agile-coach",
        collaborator_roles=["00-delivery-orchestrator"],
        required_receipt_types=["BacklogPlanReceipt"],
        required_artifacts=["backlog-plan.md"],
        required_evidence=["sizing_check", "capacity_check"],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.ARCHITECTURE_DESIGN],
        wip_policy_ref="wip.planning.standard",
        timebox_policy_ref="timebox.planning.1d",
        trigger_refs=["trigger.planning.completed"],
    ),
    LifecycleStage.ARCHITECTURE_DESIGN: StagePolicy(
        stage=LifecycleStage.ARCHITECTURE_DESIGN,
        owner_role="04-solution-architect",
        collaborator_roles=["10-security-specialist", "27-platform-engineer"],
        required_receipt_types=["ArchitectureReceipt"],
        required_artifacts=["architecture-design.md", "threat-model.md"],
        required_evidence=["c4_model", "contract_specifications"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G2_DESIGN,
        allowed_next_stages=[LifecycleStage.READINESS_SCAFFOLDING],
        wip_policy_ref="wip.architecture.standard",
        timebox_policy_ref="timebox.architecture.3d",
        trigger_refs=["trigger.architecture.g2_ready"],
    ),
    LifecycleStage.READINESS_SCAFFOLDING: StagePolicy(
        stage=LifecycleStage.READINESS_SCAFFOLDING,
        owner_role="27-platform-engineer",
        collaborator_roles=["06-software-engineer", "11-test-engineer"],
        required_receipt_types=["ScaffoldingReceipt"],
        required_artifacts=["test-harness-manifest.yaml"],
        required_evidence=["dependency_verification", "pipeline_check"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G3_READINESS,
        allowed_next_stages=[LifecycleStage.IMPLEMENTATION],
        wip_policy_ref="wip.scaffolding.standard",
        timebox_policy_ref="timebox.scaffolding.1d",
        trigger_refs=["trigger.scaffolding.g3_ready"],
    ),
    LifecycleStage.IMPLEMENTATION: StagePolicy(
        stage=LifecycleStage.IMPLEMENTATION,
        owner_role="06-software-engineer",
        collaborator_roles=["07-frontend-engineer", "08-backend-engineer"],
        required_receipt_types=["ExecutionReceipt"],
        required_artifacts=["implementation.diff"],
        required_evidence=["unit_test_results", "diff_stat"],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.CODE_REVIEW],
        wip_policy_ref="wip.implementation.limit3",
        timebox_policy_ref="timebox.implementation.5d",
        trigger_refs=["trigger.implementation.completed"],
    ),
    LifecycleStage.CODE_REVIEW: StagePolicy(
        stage=LifecycleStage.CODE_REVIEW,
        owner_role="09-code-reviewer",
        collaborator_roles=[],  # SoD strictly enforced: isolated reviewer
        required_receipt_types=["ReviewReceipt"],
        required_artifacts=["review-verdict.md"],
        required_evidence=["static_analysis_output", "sod_attestation"],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.SECURITY_REVIEW, LifecycleStage.IMPLEMENTATION],
        wip_policy_ref="wip.review.limit2",
        timebox_policy_ref="timebox.review.1d",
        trigger_refs=["trigger.code_review.completed"],
    ),
    LifecycleStage.SECURITY_REVIEW: StagePolicy(
        stage=LifecycleStage.SECURITY_REVIEW,
        owner_role="10-security-specialist",
        collaborator_roles=["14-governance-auditor"],
        required_receipt_types=["SecurityReceipt"],
        required_artifacts=["security-scan.md"],
        required_evidence=["sast_scan_log", "secret_leak_audit"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G4_CODE_SECURITY,
        allowed_next_stages=[LifecycleStage.TEST_VALIDATION, LifecycleStage.IMPLEMENTATION],
        wip_policy_ref="wip.security.standard",
        timebox_policy_ref="timebox.security.2d",
        trigger_refs=["trigger.security.g4_ready"],
    ),
    LifecycleStage.TEST_VALIDATION: StagePolicy(
        stage=LifecycleStage.TEST_VALIDATION,
        owner_role="11-test-engineer",
        collaborator_roles=[],
        required_receipt_types=["TestReceipt"],
        required_artifacts=["test-results.xml"],
        required_evidence=["integration_test_runs", "coverage_report"],
        handoff_required=True,
        ack_required=True,
        required_gate=None,
        allowed_next_stages=[LifecycleStage.QA_VALIDATION, LifecycleStage.IMPLEMENTATION],
        wip_policy_ref="wip.test.limit2",
        timebox_policy_ref="timebox.test.2d",
        trigger_refs=["trigger.test.completed"],
    ),
    LifecycleStage.QA_VALIDATION: StagePolicy(
        stage=LifecycleStage.QA_VALIDATION,
        owner_role="12-qa-engineer",
        collaborator_roles=["01-requirements-analyst"],
        required_receipt_types=["QAReceipt"],
        required_artifacts=["qa-acceptance-report.md"],
        required_evidence=["bdd_execution_log", "user_journey_evidence"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G5_QUALITY,
        allowed_next_stages=[LifecycleStage.GOVERNANCE_RELEASE, LifecycleStage.IMPLEMENTATION],
        wip_policy_ref="wip.qa.limit2",
        timebox_policy_ref="timebox.qa.2d",
        trigger_refs=["trigger.qa.g5_ready"],
    ),
    LifecycleStage.GOVERNANCE_RELEASE: StagePolicy(
        stage=LifecycleStage.GOVERNANCE_RELEASE,
        owner_role="14-governance-auditor",
        collaborator_roles=["00-delivery-orchestrator"],
        required_receipt_types=["GovernanceReceipt"],
        required_artifacts=["release-manifest.yaml", "audit-ledger.json"],
        required_evidence=["ledger_seal", "compliance_signoff"],
        handoff_required=True,
        ack_required=True,
        required_gate=GateId.G6_GOVERNANCE_RELEASE,
        allowed_next_stages=[LifecycleStage.DONE],
        wip_policy_ref="wip.governance.standard",
        timebox_policy_ref="timebox.governance.1d",
        trigger_refs=["trigger.governance.g6_ready"],
    ),
    LifecycleStage.DONE: StagePolicy(
        stage=LifecycleStage.DONE,
        owner_role="00-delivery-orchestrator",
        collaborator_roles=[],
        required_receipt_types=[],
        required_artifacts=["archive-summary.md"],
        required_evidence=["remote_sync_completed"],
        handoff_required=False,
        ack_required=False,
        required_gate=None,
        allowed_next_stages=[],  # Terminal stage
        wip_policy_ref="wip.done.unlimited",
        timebox_policy_ref="timebox.done.infinite",
        trigger_refs=["trigger.item.closed"],
    ),
}
