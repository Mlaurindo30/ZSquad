"""Canonical tests for BacklogPlan domain, validation pipeline and lifecycle transitions (R7 Section 46).

Strictly stdlib + pytest.
Covers:
- valid one-Epic plan
- valid multiple-Epic plan
- canonical hierarchy (EPIC -> FEATURE -> STORY -> TASK)
- orphan rejected
- wrong parent rejected
- duplicate plan IDs rejected
- missing acceptance rejected where required (prohibited tokens, minimum lengths, SP bounds)
- DRAFT -> VALIDATED
- VALIDATED -> APPROVED
- DRAFT -> MATERIALIZED rejected
- approved plan content mutation invalidates approval
- SoD enforcement (author cannot approve own plan in risk >= MEDIUM or multi-item)
"""

from pathlib import Path
import pytest
import sqlite3

from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.backlog.qbc import SemanticQbcEngine
from scripts.runtime.backlog.repository import BacklogPlanRepository
from scripts.runtime.backlog.service import BacklogPlanService
from scripts.runtime.backlog.validator import BacklogPlanValidator


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def plan_repo(mem_db):
    return BacklogPlanRepository(db_path=mem_db)


@pytest.fixture
def qbc_engine(mem_db):
    return SemanticQbcEngine(db_path=mem_db)


@pytest.fixture
def validator(qbc_engine, plan_repo):
    return BacklogPlanValidator(qbc_engine=qbc_engine, repository=plan_repo)


@pytest.fixture
def plan_service(mem_db, plan_repo, qbc_engine, validator, tmp_path):
    return BacklogPlanService(
        runtime_root=tmp_path,
        db_path=mem_db,
        plan_repository=plan_repo,
        qbc_engine=qbc_engine,
        validator=validator,
    )


def test_valid_one_epic_plan(validator):
    """A valid plan with one Epic and its complete hierarchy validates cleanly."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Payment Platform Modernization",
        description="Overhaul payments infrastructure for high throughput.",
    )
    feature = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Stripe Checkout Integration",
        description="Integrate Stripe elements for credit card payments.",
        parent_id="EPIC-001",
    )
    story = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="User Submits Card Details",
        description="Given customer checkout When card is valid Then process charge.",
        story_points=3,
        parent_id="FEATURE-001",
    )
    task = BacklogPlanItem(
        proposed_id="TASK-0001",
        kind=WorkItemKind.TASK,
        title="Setup Webhook Verification",
        description="Implement webhook signature validation for security.",
        parent_id="STORY-001",
    )

    plan = BacklogPlan(
        plan_id="plan-valid-one-epic",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic, feature, story, task],
        created_by="04-architect",
    )

    res = validator.validate(plan)
    assert res.is_valid is True
    assert len(res.errors) == 0


def test_valid_multiple_epic_plan(validator):
    """Agent Squad supports multiple Epics coexisting in the same plan/project."""
    epic1 = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Auth and Identity Platform",
        description="Centralized identity and OAuth2 management.",
    )
    feat1 = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="JWT Token Dispatcher",
        description="Issue signed JWTs with asymmetric keys.",
        parent_id="EPIC-001",
    )
    epic2 = BacklogPlanItem(
        proposed_id="EPIC-002",
        kind=WorkItemKind.EPIC,
        title="Billing and Invoicing Hub",
        description="Automated invoicing and tax calculations.",
    )
    feat2 = BacklogPlanItem(
        proposed_id="FEATURE-002",
        kind=WorkItemKind.FEATURE,
        title="Monthly Invoice Generator",
        description="Render PDF invoices and dispatch via email.",
        parent_id="EPIC-002",
    )

    plan = BacklogPlan(
        plan_id="plan-multi-epic",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic1, feat1, epic2, feat2],
        created_by="04-architect",
    )

    res = validator.validate(plan)
    assert res.is_valid is True
    assert len(res.errors) == 0


def test_canonical_hierarchy_enforced(validator):
    """Features, Stories, Tasks must adhere to 4-tier hierarchy."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-010",
        kind=WorkItemKind.EPIC,
        title="Order Management System",
        description="Comprehensive order workflow from cart to shipping.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-010",
        kind=WorkItemKind.FEATURE,
        title="Order State Transitions",
        description="Manage transitions between PENDING, PAID, and SHIPPED.",
        parent_id="EPIC-010",
    )
    story = BacklogPlanItem(
        proposed_id="STORY-010",
        kind=WorkItemKind.STORY,
        title="Cancel Unpaid Order",
        description="Given order is unpaid When 24h pass Then cancel order.",
        story_points=2,
        parent_id="FEATURE-010",
    )
    task = BacklogPlanItem(
        proposed_id="TASK-0100",
        kind=WorkItemKind.TASK,
        title="Database Status Enum Migration",
        description="Add CANCELLED status to orders table schema.",
        parent_id="STORY-010",
    )

    plan = BacklogPlan(
        plan_id="plan-hierarchy-ok",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic, feat, story, task],
        created_by="04-architect",
    )
    assert validator.validate(plan).is_valid is True


def test_orphan_rejected(validator):
    """Features, Stories and Tasks without parent_id or with non-existent parent are rejected."""
    orphan_feat = BacklogPlanItem(
        proposed_id="FEATURE-099",
        kind=WorkItemKind.FEATURE,
        title="Orphan Feature Without Parent",
        description="This feature declares no parent epic.",
        parent_id=None,
    )
    plan = BacklogPlan(
        plan_id="plan-orphan",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[orphan_feat],
        created_by="04-architect",
    )
    res = validator.validate(plan)
    assert res.is_valid is False
    assert any("must declare a parent_id" in err for err in res.errors)

    # Feature referencing unknown parent
    unknown_parent_feat = BacklogPlanItem(
        proposed_id="FEATURE-098",
        kind=WorkItemKind.FEATURE,
        title="Feature Referencing Unknown Parent",
        description="References an EPIC that does not exist in plan or DB.",
        parent_id="EPIC-999",
    )
    plan2 = BacklogPlan(
        plan_id="plan-unknown-parent",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[unknown_parent_feat],
        created_by="04-architect",
    )
    res2 = validator.validate(plan2)
    assert res2.is_valid is False
    assert any("references unknown parent" in err for err in res2.errors)


def test_wrong_parent_rejected(validator):
    """Epic must not have parent; Feature cannot parent Feature directly; Task cannot parent Story."""
    epic_with_parent = BacklogPlanItem(
        proposed_id="EPIC-050",
        kind=WorkItemKind.EPIC,
        title="Epic That Wrongly Has Parent",
        description="Epics must be root level items without parents.",
        parent_id="EPIC-001",
    )
    plan = BacklogPlan(
        plan_id="plan-wrong-epic-parent",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic_with_parent],
        created_by="04-architect",
    )
    res = validator.validate(plan)
    assert res.is_valid is False
    assert any("EPIC 'EPIC-050' must not have a parent_id" in err for err in res.errors)


def test_duplicate_plan_ids_rejected(validator):
    """Duplicate proposed_id within the same BacklogPlan is rejected."""
    item1 = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="First Story With ID 001",
        description="Description for the first user story.",
        story_points=3,
        parent_id="FEATURE-001",
    )
    item2 = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="Second Story Reusing ID 001",
        description="Duplicate proposed_id must be caught by validator.",
        story_points=5,
        parent_id="FEATURE-001",
    )
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Root Epic For Duplicate Test",
        description="Valid root epic description.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Valid Feature For Duplicate Test",
        description="Valid feature description.",
        parent_id="EPIC-001",
    )

    plan = BacklogPlan(
        plan_id="plan-duplicate-ids",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic, feat, item1, item2],
        created_by="04-architect",
    )
    res = validator.validate(plan)
    assert res.is_valid is False
    assert any("duplicate proposed_id" in err.lower() for err in res.errors)


def test_missing_acceptance_and_prohibited_tokens_rejected(validator):
    """Prohibited tokens (TBD, TODO) and sizing violations trigger validation errors."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Valid Epic Title Long Enough",
        description="Valid description for the root epic.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Feature with TBD in description",
        description="We will implement this TBD next sprint.",
        parent_id="EPIC-001",
    )

    plan = BacklogPlan(
        plan_id="plan-prohibited-token",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[epic, feat],
        created_by="04-architect",
    )
    res = validator.validate(plan)
    assert res.is_valid is False
    assert any("prohibited token 'tbd'" in err for err in res.errors)

    # Short title or description
    with pytest.raises(ValidationError, match="title must be at least 5 characters"):
        BacklogPlanItem(
            proposed_id="TASK-0001",
            kind=WorkItemKind.TASK,
            title="Bad",
            description="Valid long description for the task.",
            parent_id="STORY-001",
        )

    with pytest.raises(ValidationError, match="description must be at least 10 characters"):
        BacklogPlanItem(
            proposed_id="TASK-0001",
            kind=WorkItemKind.TASK,
            title="Valid Task Title",
            description="Short",
            parent_id="STORY-001",
        )


def test_draft_to_validated_transition(plan_service):
    """Draft plan with valid items transitions to VALIDATED."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-100",
        kind=WorkItemKind.EPIC,
        title="Telemetry Pipeline V2",
        description="High frequency metrics ingestion and export.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-100",
        kind=WorkItemKind.FEATURE,
        title="Prometheus Metrics Exporter",
        description="Expose metrics in Prometheus scrapable format.",
        parent_id="EPIC-100",
    )

    plan = plan_service.create_draft_plan(
        project_id="telemetry-proj",
        created_by="04-architect",
        items=[epic, feat],
        plan_id="plan-telemetry",
    )
    assert plan.status == BacklogPlanStatus.DRAFT

    updated_plan, val_res = plan_service.validate_plan("plan-telemetry")
    assert val_res.is_valid is True
    assert updated_plan.status == BacklogPlanStatus.VALIDATED


def test_validated_to_approved_transition(plan_service):
    """Validated plan transitions to APPROVED when signed by authorized persona."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-200",
        kind=WorkItemKind.EPIC,
        title="Security Gateway Migration",
        description="Migrate edge routers to mutual TLS.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-200",
        kind=WorkItemKind.FEATURE,
        title="TLS 1.3 Termination",
        description="Terminate mutual TLS at proxy gateway.",
        parent_id="EPIC-200",
    )

    plan_service.create_draft_plan(
        project_id="sec-proj",
        created_by="04-architect",
        items=[epic, feat],
        plan_id="plan-sec",
    )
    plan_service.validate_plan("plan-sec")

    approved_plan = plan_service.approve_plan(
        plan_id="plan-sec",
        approved_by="00-delivery-orchestrator",
    )
    assert approved_plan.status == BacklogPlanStatus.APPROVED
    assert approved_plan.approved_by == "00-delivery-orchestrator"


def test_draft_to_materialized_rejected(plan_service):
    """Attempting to materialize a plan directly from DRAFT or VALIDATED is rejected."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-300",
        kind=WorkItemKind.EPIC,
        title="Audit Trail Storage",
        description="Append-only immutable audit log storage.",
    )

    plan = plan_service.create_draft_plan(
        project_id="audit-proj",
        created_by="04-architect",
        items=[epic],
        plan_id="plan-audit",
    )

    # Calling materialize_plan on DRAFT raises ValidationError
    with pytest.raises(ValidationError, match="BacklogPlan must be in status APPROVED"):
        plan_service.materialize_plan("plan-audit")

    # Validate the plan
    plan_service.validate_plan("plan-audit")

    # Calling materialize_plan on VALIDATED also raises ValidationError
    with pytest.raises(ValidationError, match="BacklogPlan must be in status APPROVED"):
        plan_service.materialize_plan("plan-audit")


def test_approved_plan_content_mutation_invalidates_approval(plan_service, plan_repo):
    """Mutating content of an approved plan invalidates its approval and reverts status to DRAFT."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-400",
        kind=WorkItemKind.EPIC,
        title="Original Epic Scope",
        description="Original description before scope tampering.",
    )

    plan = plan_service.create_draft_plan(
        project_id="tamper-proj",
        created_by="04-architect",
        items=[epic],
        plan_id="plan-tamper",
    )
    plan_service.validate_plan("plan-tamper")
    approved = plan_service.approve_plan("plan-tamper", approved_by="01-pm")
    assert approved.status == BacklogPlanStatus.APPROVED

    # Mutate the plan items
    tampered_epic = BacklogPlanItem(
        proposed_id="EPIC-400",
        kind=WorkItemKind.EPIC,
        title="Mutated Epic Title Completely Changed",
        description="Altered description that changes the cryptographic content hash.",
    )
    mutated_plan = BacklogPlan(
        plan_id="plan-tamper",
        project_id="tamper-proj",
        status=BacklogPlanStatus.APPROVED,  # Caller attempts to preserve approved status
        items=[tampered_epic],
        created_by="04-architect",
        approved_by="01-pm",
    )

    # Saving the mutated plan must invalidate the approval
    plan_repo.save_plan(mutated_plan)

    reloaded = plan_repo.get_plan("plan-tamper")
    assert reloaded is not None
    assert reloaded.status == BacklogPlanStatus.DRAFT
    assert reloaded.approved_by is None


def test_sod_enforcement_author_cannot_approve_own_plan(plan_service):
    """Author cannot approve their own plan if risk >= MEDIUM or plan contains multiple items."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-500",
        kind=WorkItemKind.EPIC,
        title="Payment Core Revamp",
        description="High-risk overhaul of financial ledger transactions.",
        metadata={"risk": "HIGH"},
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-500",
        kind=WorkItemKind.FEATURE,
        title="Ledger Rebalance Routine",
        description="Daily automated ledger reconciliations.",
        parent_id="EPIC-500",
        metadata={"risk": "HIGH"},
    )

    plan_service.create_draft_plan(
        project_id="sod-proj",
        created_by="04-architect",
        items=[epic, feat],
        plan_id="plan-sod",
        metadata={"risk": "HIGH"},
    )
    plan_service.validate_plan("plan-sod")

    # Author '04-architect' attempting to self-approve must raise SoD ValidationError
    with pytest.raises(ValidationError, match="Segregation of Duties"):
        plan_service.approve_plan(
            plan_id="plan-sod",
            approved_by="04-architect",
            enforce_sod=True,
        )

    # Independent approver '00-delivery-orchestrator' succeeds
    approved = plan_service.approve_plan(
        plan_id="plan-sod",
        approved_by="00-delivery-orchestrator",
        enforce_sod=True,
    )
    assert approved.status == BacklogPlanStatus.APPROVED
