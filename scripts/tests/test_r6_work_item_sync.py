"""Canonical Tests for R6 Work Item Outbound Synchronization (Section 48).

Covers:
- Epic create
- Feature create with parent
- Story create with parent
- Task create with parent
- process-aware Story mapping (Agile, Scrum, Basic, CMMI)
- repeat create does not duplicate
- lost-response reconciliation prevents duplicate
- external ID persisted
- revision persisted
- field update with optimistic concurrency
- parent update
- AreaPath, IterationPath, tags
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pytest

from scripts.domain.lifecycle import LifecycleStage
from scripts.domain.sync import SyncStatus
from scripts.runtime.delivery.azure_writer import AzureWriterPort
from scripts.runtime.delivery.errors import (
    OptimisticConcurrencyError,
    OrphanWorkItemViolationError,
    SyncError,
)
from scripts.runtime.delivery.repository import (
    ProjectBindingRecord,
    SqliteBindingRepository,
    WorkItemBindingRecord,
)
from scripts.runtime.delivery.sync import DeliverySyncService


class MockWriter(AzureWriterPort):
    """Mock writer capturing work item mutation payloads."""

    def __init__(self) -> None:
        self.created_items: List[Dict[str, Any]] = []
        self.updated_items: List[Dict[str, Any]] = []
        self.next_id = 2000

    def create_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        self.created_items.append(kwargs)
        self.next_id += 1
        return {
            "id": self.next_id,
            "rev": 1,
            "url": f"https://dev.azure.com/enterprise/_apis/wit/workItems/{self.next_id}",
        }

    def update_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        self.updated_items.append(kwargs)
        expected_rev = kwargs.get("expected_rev") or 1
        ado_id = kwargs.get("ado_id", 2000)
        return {"id": ado_id, "rev": expected_rev + 1}

    def create_repository(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_team(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_area(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_iteration(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_service_hook(self, **kwargs: Any) -> Dict[str, Any]:
        return {}


@pytest.fixture
def repo() -> SqliteBindingRepository:
    repository = SqliteBindingRepository(":memory:")
    # Seed default Agile project
    proj = ProjectBindingRecord(
        project_id="PROJ-AGILE",
        project_root="/workspace/proj",
        display_name="Agile Project",
        delivery_backend_kind="AZURE_DEVOPS",
        delivery_binding_ref="https://dev.azure.com/enterprise/CoreProject",
        binding_status="COMPLETE",
        organization_url="https://dev.azure.com/enterprise",
        team_project_name="CoreProject",
        area_path="CoreProject\\DeliveryTeam",
        iteration_path="CoreProject\\Sprint 1",
        process_template="Agile",
    )
    repository.upsert_binding(proj, changed_by="test-setup", action="INIT")
    return repository


def test_outbound_epic_create(repo: SqliteBindingRepository):
    """Epic creation enqueues outbox and provisions card with Epic type and no parent."""
    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    outbox_rec = sync_svc.enqueue_outbound_create(
        work_item_id="EPIC-001",
        project_id="PROJ-AGILE",
        title="Payment Modernization",
        kind="EPIC",
        stage=LifecycleStage.INTAKE,
        description="High level initiative",
    )

    assert outbox_rec.operation == "CREATE"
    assert outbox_rec.work_item_id == "EPIC-001"

    drain_res = sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert drain_res.succeeded_count == 1
    assert len(writer.created_items) == 1

    call = writer.created_items[0]
    assert call["work_item_type"] == "Epic"
    assert call["title"] == "Payment Modernization"
    assert call["parent_ado_id"] is None
    assert "canonical_id:EPIC-001" in call["tags"]

    binding = repo.get_work_item_binding("EPIC-001")
    assert binding is not None
    assert binding.ado_id == 2001
    assert binding.remote_rev == 1
    assert binding.sync_status == SyncStatus.SYNCED.value


def test_outbound_feature_create_with_parent(repo: SqliteBindingRepository):
    """Feature creation links to parent Epic via parent_ado_id."""
    # Seed parent Epic binding
    epic_bound = WorkItemBindingRecord(
        work_item_id="EPIC-001",
        project_id="PROJ-AGILE",
        ado_id=1001,
        remote_url="https://dev.azure.com/enterprise/CoreProject/_apis/wit/workItems/1001",
        remote_rev=2,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(epic_bound)

    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    sync_svc.enqueue_outbound_create(
        work_item_id="FEAT-001",
        project_id="PROJ-AGILE",
        title="Credit Card Tokenization",
        kind="FEATURE",
        stage=LifecycleStage.DISCOVERY,
        parent_id="EPIC-001",
    )

    drain_res = sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert drain_res.succeeded_count == 1

    call = writer.created_items[0]
    assert call["work_item_type"] == "Feature"
    assert call["parent_ado_id"] == 1001
    assert call["parent_comment"] == "Canonical parent link EPIC-001"


def test_outbound_orphan_prevented_when_parent_unbound(repo: SqliteBindingRepository):
    """Dispatching child with missing/unbound parent raises OrphanWorkItemViolationError."""
    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    with pytest.raises(OrphanWorkItemViolationError) as exc_info:
        sync_svc.enqueue_outbound_create(
            work_item_id="STORY-001",
            project_id="PROJ-AGILE",
            title="CVV Validation",
            kind="STORY",
            stage=LifecycleStage.IMPLEMENTATION,
            parent_id="FEAT-NON-EXISTENT",
        )

    assert "FEAT-NON-EXISTENT" in str(exc_info.value)
    assert len(writer.created_items) == 0


def test_outbound_story_and_task_hierarchy(repo: SqliteBindingRepository):
    """Story links to Feature, Task links to Story in topological order."""
    # Seed Feature
    feat_bound = WorkItemBindingRecord(
        work_item_id="FEAT-010",
        project_id="PROJ-AGILE",
        ado_id=1010,
        remote_url="",
        remote_rev=1,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(feat_bound)

    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    # 1. Create Story
    sync_svc.enqueue_outbound_create(
        work_item_id="STORY-020",
        project_id="PROJ-AGILE",
        title="Tokenize Card Number",
        kind="STORY",
        stage=LifecycleStage.IMPLEMENTATION,
        parent_id="FEAT-010",
    )
    drain_story = sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert drain_story.succeeded_count == 1
    story_binding = repo.get_work_item_binding("STORY-020")
    assert story_binding.ado_id == 2001

    # 2. Create Task under Story
    sync_svc.enqueue_outbound_create(
        work_item_id="TASK-030",
        project_id="PROJ-AGILE",
        title="Write RSA Encryptor",
        kind="TASK",
        stage=LifecycleStage.IMPLEMENTATION,
        parent_id="STORY-020",
    )
    drain_task = sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert drain_task.succeeded_count == 1
    task_call = writer.created_items[1]
    assert task_call["work_item_type"] == "Task"
    assert task_call["parent_ado_id"] == 2001


def test_process_aware_story_mapping(repo: SqliteBindingRepository):
    """Story type maps correctly across Agile, Scrum, Basic, and CMMI process templates."""
    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    # Seed projects for distinct process templates
    templates = [
        ("PROJ-AGILE", "User Story"),
        ("PROJ-SCRUM", "Product Backlog Item"),
        ("PROJ-BASIC", "Issue"),
        ("PROJ-CMMI", "Requirement"),
    ]

    for proj_id, expected_type in templates:
        p = ProjectBindingRecord(
            project_id=proj_id,
            project_root=f"/workspace/{proj_id}",
            display_name=proj_id,
            delivery_backend_kind="AZURE_DEVOPS",
            delivery_binding_ref=f"https://dev.azure.com/enterprise/{proj_id}",
            binding_status="COMPLETE",
            organization_url="https://dev.azure.com/enterprise",
            team_project_name="CoreProject",
            process_template=proj_id.split("-")[1],
        )
        repo.upsert_binding(p, changed_by="test", action="INIT")

        writer.created_items.clear()
        sync_svc.enqueue_outbound_create(
            work_item_id=f"STORY-{proj_id}",
            project_id=proj_id,
            title=f"Sample for {proj_id}",
            kind="STORY",
            stage=LifecycleStage.REQUIREMENTS_PRODUCT,
        )
        sync_svc.drain_outbox(project_id=proj_id)

        assert len(writer.created_items) == 1
        assert writer.created_items[0]["work_item_type"] == expected_type


def test_repeat_create_does_not_duplicate(repo: SqliteBindingRepository):
    """Calling enqueue_outbound_create on an already bound item reroutes to update."""
    bound = WorkItemBindingRecord(
        work_item_id="STORY-DUP-1",
        project_id="PROJ-AGILE",
        ado_id=5555,
        remote_url="https://dev.azure.com/enterprise/CoreProject/_apis/wit/workItems/5555",
        remote_rev=3,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(bound)

    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    outbox = sync_svc.enqueue_outbound_create(
        work_item_id="STORY-DUP-1",
        project_id="PROJ-AGILE",
        title="Updated Title",
        kind="STORY",
        stage=LifecycleStage.IMPLEMENTATION,
    )

    # Rerouted to UPDATE operation
    assert outbox.operation == "UPDATE"

    sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert len(writer.created_items) == 0
    assert len(writer.updated_items) == 1
    assert writer.updated_items[0]["ado_id"] == 5555


def test_lost_response_reconciliation_prevents_duplicate(repo: SqliteBindingRepository):
    """If card already bound locally, repeat sync preserves single authoritative remote ID."""
    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    # First create
    sync_svc.enqueue_outbound_create(
        work_item_id="STORY-LOST-1",
        project_id="PROJ-AGILE",
        title="Resilient Story",
        kind="STORY",
        stage=LifecycleStage.INTAKE,
    )
    sync_svc.drain_outbox(project_id="PROJ-AGILE")

    binding_first = repo.get_work_item_binding("STORY-LOST-1")
    assert binding_first.ado_id == 2001

    # Second creation attempt
    sync_svc.enqueue_outbound_create(
        work_item_id="STORY-LOST-1",
        project_id="PROJ-AGILE",
        title="Resilient Story Replay",
        kind="STORY",
        stage=LifecycleStage.INTAKE,
    )
    sync_svc.drain_outbox(project_id="PROJ-AGILE")

    # Authoritative remote ID remains 2001 (no second card created)
    binding_second = repo.get_work_item_binding("STORY-LOST-1")
    assert binding_second.ado_id == 2001
    assert len(writer.created_items) == 1


def test_field_update_with_optimistic_concurrency(repo: SqliteBindingRepository):
    """Work item update transmits expected_rev for atomic revision check."""
    bound = WorkItemBindingRecord(
        work_item_id="STORY-UPD-1",
        project_id="PROJ-AGILE",
        ado_id=8888,
        remote_url="https://dev.azure.com/enterprise/CoreProject/_apis/wit/workItems/8888",
        remote_rev=5,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(bound)

    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    sync_svc.enqueue_outbound_update(
        work_item_id="STORY-UPD-1",
        project_id="PROJ-AGILE",
        new_stage=LifecycleStage.CODE_REVIEW,
        history_comment="Ready for peer review",
        fields={"System.Title": "Advanced Story Title"},
    )

    drain_res = sync_svc.drain_outbox(project_id="PROJ-AGILE")
    assert drain_res.succeeded_count == 1
    assert len(writer.updated_items) == 1

    call = writer.updated_items[0]
    assert call["ado_id"] == 8888
    assert call["expected_rev"] == 5
    assert call["board_column"] == "Code Review"
    assert call["history_comment"] == "Ready for peer review"
    assert call["fields"]["System.Title"] == "Advanced Story Title"

    updated_bound = repo.get_work_item_binding("STORY-UPD-1")
    assert updated_bound.remote_rev == 6
    assert updated_bound.sync_status == SyncStatus.SYNCED.value


def test_area_iteration_and_tags_persisted(repo: SqliteBindingRepository):
    """AreaPath, IterationPath, and governed tags are included in create payload."""
    writer = MockWriter()
    sync_svc = DeliverySyncService(repository=repo, writer=writer)

    sync_svc.enqueue_outbound_create(
        work_item_id="STORY-META-1",
        project_id="PROJ-AGILE",
        title="Metadata Rich Story",
        kind="STORY",
        stage=LifecycleStage.READINESS_SCAFFOLDING,
        area_path="CoreProject\\DeliveryTeam\\SubDomain",
        iteration_path="CoreProject\\Sprint 2",
        story_points=5,
        acceptance_criteria="Given/When/Then",
        tags=["risk:medium", "priority:high"],
    )

    sync_svc.drain_outbox(project_id="PROJ-AGILE")
    call = writer.created_items[0]

    assert call["area_path"] == "CoreProject\\DeliveryTeam\\SubDomain"
    assert call["iteration_path"] == "CoreProject\\Sprint 2"
    assert call["story_points"] == 5
    assert call["acceptance_criteria"] == "Given/When/Then"
    assert "risk:medium" in call["tags"]
    assert "priority:high" in call["tags"]
    assert "stage:READINESS_SCAFFOLDING" in call["tags"]
    assert "agent-squad" in call["tags"]
    assert "canonical_id:STORY-META-1" in call["tags"]
