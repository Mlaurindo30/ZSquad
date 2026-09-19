"""Unit and contract tests for R1 Canonical Domain Contracts.

Covers:
- Canonical IDs accepted
- Legacy aliases (FEAT-, US-, TK-) recognized only as legacy semantics and normalized
- Invalid parent-child relationships rejected (EPIC->EPIC, STORY->EPIC, TASK->EPIC, etc.)
- Mandatory fields enforced across domain entities
- ProjectBinding rejects missing required fields and forbidden product defaults
- Empty identifiers rejected
- Invalid gate/stage transitions rejected
- Handoff/ack state vocabulary (PENDING blocks transition)
- Sync statuses and reconciliation outcomes
- Receipt types and assert_sod_compliance
"""

from datetime import datetime
import pytest

from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import (
    SchemaVersion,
    ValidationError,
)
from scripts.domain.delegation import (
    ActivationPacket,
    AncestorSnapshot,
    AssignmentStatus,
    DelegationEnvelope,
    ExecutionAssignment,
    HostCapabilities,
    WorkContext,
)
from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
    FindingKind,
    FindingSeverity,
    RetryPolicy,
    SchedulePolicy,
    TriggerActionKind,
    TriggerPolicy,
    WatchdogFinding,
)
from scripts.domain.lifecycle import (
    CANONICAL_STAGE_POLICIES,
    Acknowledgement,
    AcknowledgementStatus,
    DeliveryCycle,
    GateDecision,
    GateDecisionStatus,
    GateId,
    Handoff,
    LifecycleStage,
    LifecycleTransition,
    StagePolicy,
)
from scripts.domain.project import (
    AdoBinding,
    DeliveryBackendKind,
    LocalWorkMirror,
    ProjectBinding,
)
from scripts.domain.receipts import (
    BaseReceipt,
    DispatchReceipt,
    ExecutionReceipt,
    GovernanceReceipt,
    QAReceipt,
    ReceiptType,
    ReviewReceipt,
    SecurityReceipt,
    TestReceipt,
    assert_sod_compliance,
)
from scripts.domain.sync import (
    AdoWorkItemBinding,
    ReconciliationAction,
    ReconciliationDecision,
    ReconciliationOutcome,
    SyncState,
    SyncStatus,
)
from scripts.domain.work_items import (
    AcceptanceCriterion,
    RiskTier,
    SyncStateKind,
    WorkHierarchy,
    WorkItem,
    WorkItemId,
    WorkItemKind,
    WorkItemType,
)


# ==============================================================================
# 1. Canonical IDs & Legacy Aliases
# ==============================================================================

def test_canonical_ids_accepted():
    assert WorkItemId.validate("EPIC-001")
    assert WorkItemId.validate("EPIC-12345")
    assert WorkItemId.validate("FEATURE-001")
    assert WorkItemId.validate("STORY-001")
    assert WorkItemId.validate("TASK-0001")
    assert WorkItemId.validate("BUG-001")
    assert WorkItemId.validate("SPIKE-001")
    assert WorkItemId.validate("INCIDENT-001")
    assert WorkItemId.validate("RELEASE-001")
    assert WorkItemId.validate("SETUP-001")


def test_legacy_aliases_normalized_to_canonical():
    assert WorkItemId.normalize("FEAT-100") == "FEATURE-100"
    assert WorkItemId.normalize("US-200") == "STORY-200"
    assert WorkItemId.normalize("TK-3000") == "TASK-3000"
    assert WorkItemId.normalize("  FEAT-100  ") == "FEATURE-100"

    # Non-legacy IDs pass through unchanged
    assert WorkItemId.normalize("EPIC-001") == "EPIC-001"
    assert WorkItemId.normalize("FEATURE-001") == "FEATURE-001"
    assert WorkItemId.normalize("STORY-001") == "STORY-001"
    assert WorkItemId.normalize("TASK-0001") == "TASK-0001"


def test_empty_or_invalid_id_raises_validation_error():
    with pytest.raises(ValidationError, match="WorkItem ID must be a non-empty string"):
        WorkItemId.normalize("")
    with pytest.raises(ValidationError, match="WorkItem ID must be a non-empty string"):
        WorkItemId.normalize(None)  # type: ignore

    assert not WorkItemId.validate("")
    assert not WorkItemId.validate("INVALID-ID")
    assert not WorkItemId.validate("TASK-123")  # task requires 4 digits minimum
    assert not WorkItemId.validate("STORY-12")   # story requires 3 digits minimum


def test_work_item_kind_inference():
    assert WorkItemId.infer_kind("EPIC-001") == WorkItemKind.EPIC
    assert WorkItemId.infer_kind("FEAT-001") == WorkItemKind.FEATURE
    assert WorkItemId.infer_kind("FEATURE-001") == WorkItemKind.FEATURE
    assert WorkItemId.infer_kind("US-001") == WorkItemKind.STORY
    assert WorkItemId.infer_kind("STORY-001") == WorkItemKind.STORY
    assert WorkItemId.infer_kind("TK-0001") == WorkItemKind.TASK
    assert WorkItemId.infer_kind("TASK-0001") == WorkItemKind.TASK
    assert WorkItemId.infer_kind("BUG-001") == WorkItemKind.BUG
    assert WorkItemId.infer_kind("SPIKE-001") == WorkItemKind.SPIKE
    assert WorkItemId.infer_kind("INCIDENT-001") == WorkItemKind.INCIDENT
    assert WorkItemId.infer_kind("RELEASE-001") == WorkItemKind.RELEASE
    assert WorkItemId.infer_kind("SETUP-001") == WorkItemKind.PROJECT_SETUP

    with pytest.raises(ValidationError, match="Cannot infer WorkItemKind"):
        WorkItemId.infer_kind("UNKNOWN-001")


# ==============================================================================
# 2. Hierarchy & Parent-Child Relationships
# ==============================================================================

def test_hierarchy_allowed_parent_child():
    WorkHierarchy.validate_parent_child(None, WorkItemKind.EPIC)
    WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.FEATURE)
    WorkHierarchy.validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.STORY)
    WorkHierarchy.validate_parent_child(WorkItemKind.STORY, WorkItemKind.TASK)
    WorkHierarchy.validate_parent_child(WorkItemKind.STORY, WorkItemKind.BUG)
    WorkHierarchy.validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.BUG)
    WorkHierarchy.validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.SPIKE)
    WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.SPIKE)
    WorkHierarchy.validate_parent_child(None, WorkItemKind.INCIDENT)
    WorkHierarchy.validate_parent_child(None, WorkItemKind.RELEASE)
    WorkHierarchy.validate_parent_child(None, WorkItemKind.PROJECT_SETUP)


def test_hierarchy_invalid_parent_child_rejected():
    with pytest.raises(ValidationError, match="Invalid parentage: EPIC cannot have parent"):
        WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.EPIC)

    with pytest.raises(ValidationError, match="Invalid parentage: STORY cannot have parent EPIC"):
        WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.STORY)

    with pytest.raises(ValidationError, match="Invalid parentage: TASK cannot have parent EPIC"):
        WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.TASK)

    with pytest.raises(ValidationError, match="Invalid parentage: FEATURE cannot have parent STORY"):
        WorkHierarchy.validate_parent_child(WorkItemKind.STORY, WorkItemKind.FEATURE)


# ==============================================================================
# 3. WorkItem Entity & Invariants
# ==============================================================================

def _make_criterion(cid: str = "AC-01") -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=cid,
        scenario="User successfully authenticates with valid credentials",
        given="a registered user with active credentials",
        when="they submit their credentials to the auth endpoint",
        then="a valid token is returned",
        is_verified=True,
    )


def test_work_item_creation_success():
    item = WorkItem(
        work_item_id="STORY-101",
        kind=WorkItemKind.STORY,
        title="Valid Story Title",
        description="This is a comprehensive description of the work item exceeding 20 chars.",
        project_id="PROJ-01",
        stage="IMPLEMENTATION",
        risk_tier=RiskTier.LOW,
        definition_of_done=["Unit tests written", "Code review completed"],
        acceptance_criteria=[_make_criterion()],
        parent_id="FEATURE-001",
        story_points=3,
    )
    assert item.work_item_id == "STORY-101"
    assert item.story_points == 3
    assert item.sync_state == SyncStateKind.PENDING_CREATE


def test_work_item_normalizes_legacy_id_on_init():
    item = WorkItem(
        work_item_id="US-101",
        kind=WorkItemKind.STORY,
        title="Legacy Story Title",
        description="Comprehensive description that is long enough for domain validation.",
        project_id="PROJ-01",
        stage="IMPLEMENTATION",
        risk_tier=RiskTier.LOW,
        definition_of_done=["Unit tests written", "Code review completed"],
        acceptance_criteria=[_make_criterion()],
    )
    assert item.work_item_id == "STORY-101"


def test_work_item_epic_cannot_have_parent():
    with pytest.raises(ValidationError, match="EPIC cannot have a parent"):
        WorkItem(
            work_item_id="EPIC-001",
            kind=WorkItemKind.EPIC,
            title="Root Epic Title",
            description="Comprehensive description of the epic exceeding twenty characters.",
            project_id="PROJ-01",
            stage="INTAKE",
            risk_tier=RiskTier.HIGH,
            definition_of_done=["Completed"],
            acceptance_criteria=[],
            parent_id="EPIC-000",
        )


def test_work_item_sizing_limits():
    with pytest.raises(ValidationError, match="Sizing invariant violated"):
        WorkItem(
            work_item_id="STORY-101",
            kind=WorkItemKind.STORY,
            title="Oversized Story",
            description="Comprehensive description of an oversized story exceeding twenty chars.",
            project_id="PROJ-01",
            stage="PLANNING",
            risk_tier=RiskTier.MEDIUM,
            definition_of_done=["Done"],
            acceptance_criteria=[_make_criterion()],
            story_points=13,
        )

    with pytest.raises(ValidationError, match="must belong to Fibonacci set"):
        WorkItem(
            work_item_id="STORY-101",
            kind=WorkItemKind.STORY,
            title="Non-Fib Story",
            description="Comprehensive description of a story with invalid story point estimate.",
            project_id="PROJ-01",
            stage="PLANNING",
            risk_tier=RiskTier.MEDIUM,
            definition_of_done=["Done"],
            acceptance_criteria=[_make_criterion()],
            story_points=4,
        )


def test_work_item_mandatory_fields():
    with pytest.raises(ValidationError, match="title must be at least 5 characters"):
        WorkItem(
            work_item_id="STORY-101",
            kind=WorkItemKind.STORY,
            title="Hi",
            description="Comprehensive description of the work item exceeding 20 chars.",
            project_id="PROJ-01",
            stage="PLANNING",
            risk_tier=RiskTier.LOW,
            definition_of_done=["Done"],
            acceptance_criteria=[],
        )

    with pytest.raises(ValidationError, match="description must be at least 20 characters"):
        WorkItem(
            work_item_id="STORY-101",
            kind=WorkItemKind.STORY,
            title="Valid Title",
            description="Too short",
            project_id="PROJ-01",
            stage="PLANNING",
            risk_tier=RiskTier.LOW,
            definition_of_done=["Done"],
            acceptance_criteria=[],
        )


def test_work_item_exit_requirements_validation():
    story = WorkItem(
        work_item_id="STORY-101",
        kind=WorkItemKind.STORY,
        title="Valid Story Title",
        description="Comprehensive description of the work item exceeding 20 chars.",
        project_id="PROJ-01",
        stage="REQUIREMENTS_PRODUCT",
        risk_tier=RiskTier.LOW,
        definition_of_done=["DoD 1"],
        acceptance_criteria=[_make_criterion()],
    )
    with pytest.raises(ValidationError, match="requires at least 2 Definition of Done statements"):
        story.validate_exit_requirements("REQUIREMENTS_PRODUCT")


# ==============================================================================
# 4. ProjectBinding & AdoBinding Security & Token Validation
# ==============================================================================

def test_project_binding_valid():
    pb = ProjectBinding(
        project_id="ALPHA-CORE",
        project_root="/workspace/alpha-core",
        display_name="Alpha Core Platform",
        delivery_backend_kind=DeliveryBackendKind.AZURE_DEVOPS,
        delivery_binding_ref="ado://org/project",
    )
    assert pb.project_id == "ALPHA-CORE"
    assert pb.is_governed is True


def test_project_binding_rejects_empty_identifiers():
    with pytest.raises(ValidationError, match="project_id must not be empty"):
        ProjectBinding(
            project_id="",
            project_root="/workspace/alpha",
            display_name="Alpha",
            delivery_backend_kind=DeliveryBackendKind.LOCAL_ONLY,
            delivery_binding_ref="local://mirror",
        )


def test_project_binding_rejects_forbidden_legacy_tokens():
    for token in ("cbvgas", "Arthemis", "Deepvision", "test_root", "test_item"):
        with pytest.raises(ValidationError, match="contains forbidden legacy/product-specific token"):
            ProjectBinding(
                project_id=f"proj-{token}",
                project_root="/workspace/test",
                display_name="Platform",
                delivery_backend_kind=DeliveryBackendKind.LOCAL_ONLY,
                delivery_binding_ref="local://mirror",
            )


def test_ado_binding_valid():
    ado = AdoBinding(
        organization_url="https://dev.azure.com/my-org",
        team_project="EnterpriseDelivery",
        area_path="EnterpriseDelivery\\SquadAlpha",
        iteration_path="EnterpriseDelivery\\Sprint-01",
        assigned_team="Alpha Devs",
        repository_name="alpha-core-repo",
        service_hook_secret_ref="vault://ado-service-hook-token",
    )
    assert ado.organization_url.startswith("https://")


def test_ado_binding_rejects_insecure_http():
    with pytest.raises(ValidationError, match="must use secure HTTPS"):
        AdoBinding(
            organization_url="http://dev.azure.com/my-org",
            team_project="EnterpriseDelivery",
            area_path="EnterpriseDelivery\\SquadAlpha",
            iteration_path="EnterpriseDelivery\\Sprint-01",
            assigned_team="Alpha Devs",
        )


def test_ado_binding_rejects_empty_required_fields():
    with pytest.raises(ValidationError, match="team_project must not be empty"):
        AdoBinding(
            organization_url="https://dev.azure.com/my-org",
            team_project="",
            area_path="Area",
            iteration_path="Iteration",
            assigned_team="Team",
        )


def test_local_work_mirror_paths():
    mirror = LocalWorkMirror(
        project_id="PROJ-01",
        root_path="C:/Runtime",
        work_item_id="STORY-101",
    )
    path = mirror.get_work_item_dir()
    assert "PROJ-01" in str(path)
    assert "STORY-101" in str(path)


# ==============================================================================
# 5. Lifecycle Stages, Gates & Handoff State Machine
# ==============================================================================

def test_canonical_13_stages_present():
    assert len(LifecycleStage) == 13
    assert LifecycleStage.DISCOVERY == "DISCOVERY"
    assert LifecycleStage.ARCHITECTURE_DESIGN == "ARCHITECTURE_DESIGN"
    assert LifecycleStage.DONE == "DONE"


def test_canonical_gate_ids():
    assert len(GateId) == 6
    assert GateId.G1_PRODUCT == "G1-product"
    assert GateId.G2_DESIGN == "G2-design"
    assert GateId.G3_READINESS == "G3-readiness"
    assert GateId.G4_CODE_SECURITY == "G4-code-security"
    assert GateId.G5_QUALITY == "G5-quality"
    assert GateId.G6_GOVERNANCE_RELEASE == "G6-governance-release"


def test_stage_policy_gate_prerequisite_enforcement():
    design_policy = CANONICAL_STAGE_POLICIES[LifecycleStage.ARCHITECTURE_DESIGN]
    assert design_policy.required_gate == GateId.G2_DESIGN

    with pytest.raises(ValidationError, match="requires gate 'G2-design' approval"):
        design_policy.validate_exit(LifecycleStage.READINESS_SCAFFOLDING, gate_decision=None)

    rejected_decision = GateDecision(
        decision_id="DEC-01",
        gate_id=GateId.G2_DESIGN,
        work_item_id="STORY-101",
        stage=LifecycleStage.ARCHITECTURE_DESIGN,
        evaluator_role="04-solution-architect",
        evaluator_agent_id="agent-04",
        status=GateDecisionStatus.REJECTED,
        rationale="Architecture does not conform to hexagonal standards",
        evidence_hashes=["hash1"],
    )
    with pytest.raises(ValidationError, match="Gate approval required"):
        design_policy.validate_exit(LifecycleStage.READINESS_SCAFFOLDING, gate_decision=rejected_decision)

    approved_decision = GateDecision(
        decision_id="DEC-01",
        gate_id=GateId.G2_DESIGN,
        work_item_id="STORY-101",
        stage=LifecycleStage.ARCHITECTURE_DESIGN,
        evaluator_role="04-solution-architect",
        evaluator_agent_id="agent-04",
        status=GateDecisionStatus.APPROVED,
        rationale="Architecture conforms to all standards",
        evidence_hashes=["hash1"],
    )
    design_policy.validate_exit(LifecycleStage.READINESS_SCAFFOLDING, gate_decision=approved_decision)


def test_handoff_pending_blocks_transition():
    handoff_pending = Handoff(
        handoff_id="HO-001",
        work_item_id="STORY-101",
        from_stage=LifecycleStage.IMPLEMENTATION,
        to_stage=LifecycleStage.CODE_REVIEW,
        from_agent_id="06-software-engineer",
        to_agent_id="09-code-reviewer",
        status=AcknowledgementStatus.PENDING,
    )
    assert not handoff_pending.is_transition_allowed()

    handoff_ack = Handoff(
        handoff_id="HO-002",
        work_item_id="STORY-101",
        from_stage=LifecycleStage.IMPLEMENTATION,
        to_stage=LifecycleStage.CODE_REVIEW,
        from_agent_id="06-software-engineer",
        to_agent_id="09-code-reviewer",
        status=AcknowledgementStatus.ACKNOWLEDGED,
    )
    assert handoff_ack.is_transition_allowed()


def test_acknowledgement_entity():
    ack = Acknowledgement(
        ack_id="ACK-001",
        handoff_id="HO-001",
        recipient_agent_id="09-code-reviewer",
        status=AcknowledgementStatus.ACKNOWLEDGED,
        reason="Context loaded and ready to review",
    )
    assert ack.status == AcknowledgementStatus.ACKNOWLEDGED


# ==============================================================================
# 6. Receipts & Segregation of Duties (SoD)
# ==============================================================================

def test_receipt_types_and_creation():
    exec_receipt = ExecutionReceipt(
        receipt_id="RCPT-EXEC-01",
        receipt_type=ReceiptType.EXECUTION.value,
        work_item_id="STORY-101",
        agent_id="06-software-engineer",
        instruction_hash="inst_hash_123",
        evidence_hash="evid_hash_456",
        files_modified=["scripts/domain/work_items.py"],
        tests_executed=["test_canonical_ids_accepted"],
        test_exit_code=0,
        diff_summary="Implemented canonical work item IDs",
    )
    assert exec_receipt.agent_id == "06-software-engineer"
    assert exec_receipt.test_exit_code == 0


def test_segregation_of_duties_enforcement():
    exec_receipt = ExecutionReceipt(
        receipt_id="RCPT-EXEC-01",
        receipt_type=ReceiptType.EXECUTION.value,
        work_item_id="STORY-101",
        agent_id="06-software-engineer",
        instruction_hash="inst_hash_123",
        evidence_hash="evid_hash_456",
        diff_summary="Changes implemented",
    )

    same_agent_review = ReviewReceipt(
        receipt_id="RCPT-REV-01",
        receipt_type=ReceiptType.REVIEW.value,
        work_item_id="STORY-101",
        agent_id="06-software-engineer",
        instruction_hash="inst_hash_123",
        evidence_hash="evid_hash_789",
        reviewer_role="09-code-reviewer",
        verdict="APPROVED",
    )

    with pytest.raises(ValidationError, match="Segregation of Duties \\(SoD\\) violation"):
        assert_sod_compliance(exec_receipt, same_agent_review)

    independent_review = ReviewReceipt(
        receipt_id="RCPT-REV-02",
        receipt_type=ReceiptType.REVIEW.value,
        work_item_id="STORY-101",
        agent_id="09-code-reviewer",
        instruction_hash="inst_hash_123",
        evidence_hash="evid_hash_789",
        reviewer_role="09-code-reviewer",
        verdict="APPROVED",
    )
    assert_sod_compliance(exec_receipt, independent_review)


def test_security_receipt_blocks_approved_with_critical_vulns():
    with pytest.raises(ValidationError, match="Security review cannot be APPROVED when critical vulnerabilities exist"):
        SecurityReceipt(
            receipt_id="RCPT-SEC-01",
            receipt_type=ReceiptType.SECURITY.value,
            work_item_id="STORY-101",
            agent_id="10-security-specialist",
            instruction_hash="inst_1",
            evidence_hash="evid_1",
            security_role="10-security-specialist",
            critical_count=2,
            verdict="APPROVED",
        )


# ==============================================================================
# 7. Sync & Reconciliation Outcomes
# ==============================================================================

def test_sync_status_and_reconciliation_action():
    state = SyncState(
        work_item_id="STORY-101",
        status=SyncStatus.SYNCED,
        backend_kind="AZURE_DEVOPS",
        remote_id="12345",
    )
    assert state.status == SyncStatus.SYNCED

    decision = ReconciliationDecision(
        work_item_id="STORY-101",
        action=ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION,
        reason="Remote transition attempted to bypass Gate G6",
        local_state="IMPLEMENTATION",
        remote_state="Closed",
    )
    assert decision.action == ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION

    outcome = ReconciliationOutcome(
        decision_id="DEC-REC-01",
        work_item_id="STORY-101",
        action_taken=ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION,
        success=True,
        details="Reverted remote ADO card back to Active",
    )
    assert outcome.success is True
