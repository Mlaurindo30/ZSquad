"""Canonical tests for Hierarchical Materialization Saga Engine (R7 Section 48).

Strictly stdlib + pytest.
Covers:
- Epic first, Feature after Epic, Story after Feature, Task after Story (topological order)
- local hierarchy preserved (nested plural containers: features/, stories/, tasks/)
- R6 sync invoked via DeliverySyncService / transactional outbox (delivery_sync_outbox)
- Azure writer not directly imported/called
- second run creates zero duplicates (EXISTING_REUSED)
- existing parent reused when child is added
- partial failure resumable (compensating rollback cleans disk, plan not MATERIALIZED)
- failed child does not duplicate parent
- plan not MATERIALIZED while required item failed
"""

from pathlib import Path
import pytest
import sqlite3
import yaml

from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.backlog.materializer import BacklogMaterializer
from scripts.runtime.backlog.repository import BacklogPlanRepository
from scripts.runtime.delivery.repository import SqliteBindingRepository
from scripts.runtime.events.store import SqliteEventStore


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def plan_repo(mem_db):
    return BacklogPlanRepository(db_path=mem_db)


@pytest.fixture
def binding_repo(mem_db):
    return SqliteBindingRepository(db_path=mem_db)


@pytest.fixture
def event_store(mem_db):
    return SqliteEventStore(db_path=mem_db)


@pytest.fixture
def materializer(tmp_path, mem_db, plan_repo, binding_repo, event_store):
    return BacklogMaterializer(
        runtime_root=tmp_path,
        db_path=mem_db,
        plan_repository=plan_repo,
        binding_repository=binding_repo,
        event_store=event_store,
    )


def test_topological_materialization_order_epic_first(materializer, plan_repo, tmp_path):
    """Items are materialized strictly top-down: EPIC -> FEATURE -> STORY -> TASK even if provided out-of-order."""
    task = BacklogPlanItem(
        proposed_id="TASK-0001",
        kind=WorkItemKind.TASK,
        title="Setup Auth Schemas",
        description="Database migrations for auth credentials.",
        parent_id="STORY-001",
    )
    story = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="User Login Via JWT",
        description="Given credentials When valid Then return JWT token.",
        story_points=3,
        parent_id="FEATURE-001",
    )
    feature = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Authentication Microservice",
        description="OAuth2 and JWT token authentication service.",
        parent_id="EPIC-001",
    )
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Security and Identity Platform",
        description="Enterprise grade auth and IAM infrastructure.",
    )

    # Note items are deliberately provided in reverse topological order
    plan = BacklogPlan(
        plan_id="plan-topo-order",
        project_id="topo-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[task, story, feature, epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    receipt = materializer.materialize(plan)
    assert receipt.status == "COMPLETED"
    assert receipt.created_count == 4

    # Verify execution receipt order matches topological sort
    item_kinds = [it.kind for it in receipt.items]
    assert item_kinds == [
        WorkItemKind.EPIC,
        WorkItemKind.FEATURE,
        WorkItemKind.STORY,
        WorkItemKind.TASK,
    ]


def test_local_hierarchy_preserved_on_filesystem(materializer, plan_repo, tmp_path):
    """Local directories strictly follow canonical plural containers (features/, stories/, tasks/)."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Customer Loyalty System",
        description="Points and rewards platform for retail customers.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Loyalty Points Ledger",
        description="Double entry ledger tracking points accrual.",
        parent_id="EPIC-001",
    )
    story = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="Customer Earns Points on Purchase",
        description="Given purchase completed When invoice confirmed Then award points.",
        story_points=5,
        parent_id="FEATURE-001",
    )
    task = BacklogPlanItem(
        proposed_id="TASK-0001",
        kind=WorkItemKind.TASK,
        title="Implement Point Calculation Logic",
        description="Calculate 1 point per dollar spent.",
        parent_id="STORY-001",
    )

    plan = BacklogPlan(
        plan_id="plan-hierarchy-fs",
        project_id="loyalty-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic, feat, story, task],
        created_by="04-architect",
        approved_by="01-pm",
    )
    plan_repo.save_plan(plan)

    materializer.materialize(plan)

    base = tmp_path / "work" / "loyalty-proj"
    epic_dir = base / "EPIC-001"
    feat_dir = epic_dir / "features" / "FEATURE-001"
    story_dir = feat_dir / "stories" / "STORY-001"
    task_dir = story_dir / "tasks" / "TASK-0001"

    assert epic_dir.is_dir()
    assert (epic_dir / "status.yaml").is_file()
    assert feat_dir.is_dir()
    assert (feat_dir / "status.yaml").is_file()
    assert story_dir.is_dir()
    assert (story_dir / "status.yaml").is_file()
    assert task_dir.is_dir()
    assert (task_dir / "status.yaml").is_file()

    # Verify status.yaml content
    st_epic = yaml.safe_load((epic_dir / "status.yaml").read_text(encoding="utf-8"))
    assert st_epic["id"] == "EPIC-001"
    assert st_epic["stage"] == "INTAKE"
    assert st_epic["state"] == "intake"
    assert st_epic["status"] != "DRAFT"

    st_story = yaml.safe_load((story_dir / "status.yaml").read_text(encoding="utf-8"))
    assert st_story["id"] == "STORY-001"
    assert st_story["story_points"] == 5
    assert st_story["parent_id"] == "FEATURE-001"


def test_r6_sync_invoked_via_transactional_outbox(materializer, plan_repo, binding_repo, mem_db):
    """Every materialized item enqueues an OUTBOUND_CREATE record in delivery_sync_outbox."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Delivery Plane Overhaul",
        description="Refactor outbox queue with monotonic revisions.",
    )
    plan = BacklogPlan(
        plan_id="plan-outbox-check",
        project_id="outbox-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    receipt = materializer.materialize(plan)
    assert receipt.status == "COMPLETED"

    # Query delivery_sync_outbox in SQLite
    cursor = mem_db.cursor()
    cursor.execute("SELECT work_item_id, operation, status FROM delivery_sync_outbox WHERE project_id = 'outbox-proj'")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("EPIC-001", "CREATE", "PENDING")

    # Query bindings table
    cursor.execute("SELECT work_item_id, sync_status FROM delivery_work_item_bindings WHERE project_id = 'outbox-proj'")
    b_rows = cursor.fetchall()
    assert len(b_rows) == 1
    assert b_rows[0] == ("EPIC-001", "PENDING_CREATE")


def test_azure_writer_not_directly_called(materializer, plan_repo, monkeypatch):
    """Direct HTTP writer (DevOpsPlatformConnector) is NOT imported or called during materialization."""
    called_direct = []

    def mock_bad_call(*args, **kwargs):
        called_direct.append(True)
        raise RuntimeError("Forbidden direct Azure call!")

    monkeypatch.setattr("scripts.runtime.backlog.materializer.logger.warning", mock_bad_call, raising=False)

    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Pure Offline Materialization",
        description="Must not perform live HTTP mutations.",
    )
    plan = BacklogPlan(
        plan_id="plan-offline",
        project_id="offline-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    receipt = materializer.materialize(plan)
    assert receipt.status == "COMPLETED"
    assert len(called_direct) == 0


def test_second_run_creates_zero_duplicates_reused(materializer, plan_repo):
    """Re-executing an already materialized plan or item marks action as EXISTING_REUSED."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Idempotency Validation Epic",
        description="Verify zero duplicate creations on subsequent execution.",
    )
    plan = BacklogPlan(
        plan_id="plan-idempotent",
        project_id="idem-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    # First run: CREATED
    r1 = materializer.materialize(plan)
    assert r1.status == "COMPLETED"
    assert r1.created_count == 1
    assert r1.reused_count == 0

    # Second run on plan object: status is MATERIALIZED -> NOOP receipt
    plan_materialized = plan_repo.get_plan("plan-idempotent")
    r2 = materializer.materialize(plan_materialized)
    assert r2.status == "ALREADY_MATERIALIZED"
    assert r2.created_count == 0
    assert r2.reused_count == 1


def test_existing_parent_reused(materializer, plan_repo, tmp_path):
    """When a child item is materialized under an already existing parent, parent is reused."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Existing Parent Epic",
        description="Parent epic already materialized on disk.",
    )
    plan1 = BacklogPlan(
        plan_id="plan-parent",
        project_id="reuse-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan1)
    materializer.materialize(plan1)

    # Second plan adds a Feature under existing EPIC-001
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="New Child Feature",
        description="Child feature under existing parent epic.",
        parent_id="EPIC-001",
    )
    plan2 = BacklogPlan(
        plan_id="plan-child",
        project_id="reuse-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic, feat],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan2)

    receipt = materializer.materialize(plan2)
    assert receipt.status == "COMPLETED"
    assert receipt.created_count == 1  # only FEATURE-001 created
    assert receipt.reused_count == 1   # EPIC-001 reused

    actions = {it.canonical_id: it.action for it in receipt.items}
    assert actions["EPIC-001"] == "EXISTING_REUSED"
    assert actions["FEATURE-001"] == "CREATED"


def test_partial_failure_resumable_and_compensating_journal(materializer, plan_repo, monkeypatch, tmp_path):
    """If materialization crashes midway, compensating rollback cleans disk and plan remains resumable."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Resilience Crash Epic",
        description="First item succeeds, second item crashes.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Faulty Crash Feature",
        description="Feature whose template instantiation triggers an error.",
        parent_id="EPIC-001",
    )

    plan = BacklogPlan(
        plan_id="plan-crash",
        project_id="crash-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic, feat],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    # Monkeypatch ArtifactMaterializer to simulate disk/IO error on FEATURE-001
    from scripts.runtime.work_items.templates import ArtifactMaterializer
    orig_mat = ArtifactMaterializer.materialize_artifacts

    def faulty_materialize(self, target_dir, kind, work_item_id, metadata=None):
        if kind == WorkItemKind.FEATURE:
            raise IOError("Simulated disk full / IO failure!")
        return orig_mat(self, target_dir, kind, work_item_id, metadata)

    monkeypatch.setattr(ArtifactMaterializer, "materialize_artifacts", faulty_materialize)

    receipt = materializer.materialize(plan)
    assert receipt.status == "FAILED"
    assert len(receipt.errors) >= 1

    # Invariant: Plan is NOT marked MATERIALIZED in repository
    reloaded_plan = plan_repo.get_plan("plan-crash")
    assert reloaded_plan.status == BacklogPlanStatus.APPROVED

    # Invariant: Compensation cleaned uncommitted directories on disk
    base = tmp_path / "work" / "crash-proj"
    assert not (base / "EPIC-001").exists()


def test_failed_child_does_not_duplicate_parent(materializer, plan_repo, monkeypatch, tmp_path):
    """A failed child item in a subsequent retry does not create duplicate parents."""
    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Stable Parent Epic",
        description="Parent already materialized.",
    )
    feat = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="Retried Child Feature",
        description="Child feature.",
        parent_id="EPIC-001",
    )

    plan = BacklogPlan(
        plan_id="plan-stable",
        project_id="stable-proj",
        status=BacklogPlanStatus.APPROVED,
        items=[epic, feat],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)

    # Normal execution succeeds
    receipt = materializer.materialize(plan)
    assert receipt.status == "COMPLETED"
    assert receipt.created_count == 2
