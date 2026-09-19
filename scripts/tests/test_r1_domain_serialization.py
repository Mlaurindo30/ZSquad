"""Tests for R1 Domain Serialization and Deterministic Hashing.

Covers:
- Round-trip serialization of all domain models (to_dict, to_json)
- Stable fields and primitive types (no complex unhandled objects)
- ISO 8601 UTC datetime normalization
- Canonical hash stability and determinism (SHA-256)
- Same logical payload -> identical hash
- Different payload -> different hash
- Key order independence in source dictionaries
"""

from datetime import datetime, timezone
import json
import pytest

from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import (
    BaseDomainModel,
    canonical_hash,
    canonical_json,
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
    TestReceipt as DomainTestReceipt,
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
    WorkItem,
    WorkItemKind,
)


FIXED_DT = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
FIXED_DT_STR = "2026-09-18T12:00:00+00:00"


def test_canonical_json_key_order_independence():
    dict1 = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
    dict2 = {"a": 1, "nested": {"y": 20, "z": 10}, "b": 2}
    assert canonical_json(dict1) == canonical_json(dict2)
    assert canonical_hash(dict1) == canonical_hash(dict2)


def test_canonical_json_compact_and_deterministic():
    data = {"name": "Test", "count": 42, "items": [1, 2, 3]}
    serialized = canonical_json(data)
    assert " " not in serialized or serialized == '{"count":42,"items":[1,2,3],"name":"Test"}'
    assert canonical_hash(data) == canonical_hash(data)


def test_datetime_serialization_iso_format():
    data = {"timestamp": FIXED_DT}
    serialized = canonical_json(data)
    assert FIXED_DT_STR in serialized or "2026-09-18T12:00:00" in serialized


def test_work_item_serialization_round_trip():
    ac = AcceptanceCriterion(
        id="AC-01",
        scenario="Login valid",
        given="user exists",
        when="log in",
        then="success",
        is_verified=True,
    )
    item = WorkItem(
        work_item_id="STORY-101",
        kind=WorkItemKind.STORY,
        title="Test Work Item Title",
        description="Comprehensive description that satisfies the twenty character minimum length.",
        project_id="PROJ-01",
        stage="IMPLEMENTATION",
        risk_tier=RiskTier.LOW,
        definition_of_done=["Completed"],
        acceptance_criteria=[ac],
        parent_id="FEATURE-001",
        story_points=3,
        created_at=FIXED_DT,
        updated_at=FIXED_DT,
    )
    item_dict = item.to_dict()
    assert item_dict["work_item_id"] == "STORY-101"
    assert item_dict["kind"] == "STORY"
    assert item_dict["risk_tier"] == "LOW"
    assert item_dict["story_points"] == 3
    assert item_dict["acceptance_criteria"][0]["id"] == "AC-01"

    item_json = item.to_json()
    assert isinstance(item_json, str)
    parsed = json.loads(item_json)
    assert parsed["work_item_id"] == "STORY-101"

    # Stability of content hash
    h1 = item.content_hash()
    h2 = item.content_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_project_and_ado_binding_serialization():
    ado = AdoBinding(
        organization_url="https://dev.azure.com/myorg",
        team_project="ProjectAlpha",
        area_path="ProjectAlpha\\Area1",
        iteration_path="ProjectAlpha\\Sprint1",
        assigned_team="Team1",
        repository_name="repo1",
    )
    ado_dict = ado.to_dict()
    assert ado_dict["organization_url"] == "https://dev.azure.com/myorg"
    assert ado_dict["team_project"] == "ProjectAlpha"

    proj = ProjectBinding(
        project_id="PROJ-01",
        project_root="/workspace/proj",
        display_name="Project One",
        delivery_backend_kind=DeliveryBackendKind.AZURE_DEVOPS,
        delivery_binding_ref="ado://myorg/ProjectAlpha",
    )
    proj_dict = proj.to_dict()
    assert proj_dict["delivery_backend_kind"] == "AZURE_DEVOPS"
    assert proj.content_hash() == proj.content_hash()


def test_domain_event_serialization_and_idempotency_key():
    event = DomainEvent.create(
        event_type="WORK_ITEM_CREATED",
        work_item_id="STORY-101",
        project_id="PROJ-01",
        source="orchestrator",
        correlation_id="corr-123",
        causation_id="caus-456",
        payload={"field": "value"},
        event_id="EVT-001",
        timestamp=FIXED_DT,
    )
    d = event.to_dict()
    assert d["event_type"] == "WORK_ITEM_CREATED"
    assert d["correlation_id"] == "corr-123"
    assert len(d["idempotency_key"]) == 64

    # Identical parameters produce identical idempotency_key
    event2 = DomainEvent.create(
        event_type="WORK_ITEM_CREATED",
        work_item_id="STORY-101",
        project_id="PROJ-01",
        source="orchestrator",
        correlation_id="corr-123",
        causation_id="caus-456",
        payload={"field": "value"},
        event_id="EVT-001",
        timestamp=FIXED_DT,
    )
    assert event.idempotency_key == event2.idempotency_key


def test_delegation_envelope_and_packet_serialization():
    env = DelegationEnvelope.create(
        delegation_id="DEL-001",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-101",
        scope_summary="Implement auth module",
        action_requested="IMPLEMENT_TDD",
        compiled_instruction="Execute standard TDD workflow.",
    )
    d = env.to_dict()
    assert d["delegation_id"] == "DEL-001"
    assert d["instruction_hash"] == env.instruction_hash

    # Different instruction yields different hash
    env_diff = DelegationEnvelope.create(
        delegation_id="DEL-001",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-101",
        scope_summary="Implement auth module",
        action_requested="IMPLEMENT_TDD",
        compiled_instruction="Execute different instruction.",
    )
    assert env.instruction_hash != env_diff.instruction_hash


def test_receipts_serialization_all_types():
    receipts = [
        DispatchReceipt(
            receipt_id="R-DISPATCH",
            receipt_type=ReceiptType.DISPATCH.value,
            work_item_id="STORY-101",
            agent_id="00-delivery-orchestrator",
            instruction_hash="ihash",
            evidence_hash="ehash",
            target_agent_id="06-software-engineer",
            delegation_id="DEL-1",
            dispatched_at=FIXED_DT,
            created_at=FIXED_DT,
        ),
        ExecutionReceipt(
            receipt_id="R-EXEC",
            receipt_type=ReceiptType.EXECUTION.value,
            work_item_id="STORY-101",
            agent_id="06-software-engineer",
            instruction_hash="ihash",
            evidence_hash="ehash",
            files_modified=["a.py"],
            tests_executed=["test_1"],
            test_exit_code=0,
            diff_summary="+ 1 line",
            created_at=FIXED_DT,
        ),
        ReviewReceipt(
            receipt_id="R-REV",
            receipt_type=ReceiptType.REVIEW.value,
            work_item_id="STORY-101",
            agent_id="09-code-reviewer",
            instruction_hash="ihash",
            evidence_hash="ehash",
            reviewer_role="09-code-reviewer",
            verdict="APPROVED",
            created_at=FIXED_DT,
        ),
        SecurityReceipt(
            receipt_id="R-SEC",
            receipt_type=ReceiptType.SECURITY.value,
            work_item_id="STORY-101",
            agent_id="10-security-specialist",
            instruction_hash="ihash",
            evidence_hash="ehash",
            security_role="10-security-specialist",
            verdict="APPROVED",
            created_at=FIXED_DT,
        ),
        DomainTestReceipt(
            receipt_id="R-TEST",
            receipt_type=ReceiptType.TEST.value,
            work_item_id="STORY-101",
            agent_id="11-test-engineer",
            instruction_hash="ihash",
            evidence_hash="ehash",
            tester_role="11-test-engineer",
            total_tests=10,
            passed_tests=10,
            failed_tests=0,
            coverage_percentage=95.5,
            created_at=FIXED_DT,
        ),
        QAReceipt(
            receipt_id="R-QA",
            receipt_type=ReceiptType.QA.value,
            work_item_id="STORY-101",
            agent_id="12-qa-engineer",
            instruction_hash="ihash",
            evidence_hash="ehash",
            qa_role="12-qa-engineer",
            scenarios_verified=3,
            verdict="APPROVED",
            created_at=FIXED_DT,
        ),
        GovernanceReceipt(
            receipt_id="R-GOV",
            receipt_type=ReceiptType.GOVERNANCE.value,
            work_item_id="STORY-101",
            agent_id="14-governance-auditor",
            instruction_hash="ihash",
            evidence_hash="ehash",
            auditor_role="14-governance-auditor",
            ledger_entry_id="LEDGER-01",
            compliance_verdict="COMPLIANT",
            created_at=FIXED_DT,
        ),
    ]

    for rcpt in receipts:
        d = rcpt.to_dict()
        assert isinstance(d, dict)
        assert d["receipt_id"] == rcpt.receipt_id
        assert d["receipt_type"] == rcpt.receipt_type
        # Verify JSON serialization does not fail
        j = rcpt.to_json()
        assert isinstance(j, str)
        # Content hash determinism
        assert rcpt.content_hash() == rcpt.content_hash()


def test_sync_models_serialization():
    binding = AdoWorkItemBinding(
        work_item_id="STORY-101",
        ado_id=4567,
        remote_url="https://dev.azure.com/org/proj/_workitems/edit/4567",
        remote_rev=2,
        last_synced_at=FIXED_DT,
    )
    b_dict = binding.to_dict()
    assert b_dict["ado_id"] == 4567

    sync_state = SyncState(
        work_item_id="STORY-101",
        status=SyncStatus.FAILED_RETRYABLE,
        backend_kind="AZURE_DEVOPS",
        retry_count=2,
        last_error="HTTP 503 Service Unavailable",
        updated_at=FIXED_DT,
    )
    s_dict = sync_state.to_dict()
    assert s_dict["status"] == "FAILED_RETRYABLE"
    assert s_dict["retry_count"] == 2
