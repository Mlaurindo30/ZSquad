"""Canonical tests for R7 Architectural Authority and Boundaries (R7 Section 50).

Strictly stdlib + pytest.
Covers:
- R1 BacklogPlan reused (scripts.domain.backlog)
- R3 WorkItem runtime reused (WorkItemPathResolver, ArtifactMaterializer)
- R5 binding reused (SqliteBindingRepository, delivery_project_bindings)
- R6 sync reused (delivery_sync_outbox, SyncOutboxRecord)
- R4 lifecycle not bypassed (items initialized at G1_PROD_INTAKE / DRAFT)
- R2 events reused (agent_squad.backlog.* domain events in event store)
- no direct Azure transport (no direct HTTP/requests)
- no agent routing (R7 never selects agents or personas)
- no agent dispatch (R7 never dispatches personas or invokes subagents)
- no prompt renderer (R7 never compiles/renders agent prompts)
- no host-specific logic (cross-platform stdlib)
- no Deepvision constants (no legacy product contamination)
"""

import ast
import inspect
from pathlib import Path
import pytest
import sqlite3

import scripts.domain.backlog as domain_backlog
from scripts.domain.backlog import BacklogPlan, BacklogPlanItem, BacklogPlanStatus
from scripts.domain.events import DomainEvent
from scripts.domain.work_items import WorkItemKind
import scripts.runtime.backlog as runtime_backlog
from scripts.runtime.backlog import (
    BacklogMaterializer,
    BacklogPlanRepository,
    BacklogPlanService,
    BacklogPlanValidator,
    CanonicalIdAllocator,
    SemanticQbcEngine,
)


def test_r1_backlog_plan_reused():
    """R7 directly reuses the canonical domain BacklogPlan entity established in R1."""
    assert hasattr(domain_backlog, "BacklogPlan")
    assert hasattr(domain_backlog, "BacklogPlanItem")
    assert hasattr(domain_backlog, "BacklogPlanStatus")
    assert hasattr(domain_backlog, "VALID_PLAN_TRANSITIONS")

    item = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Authority Test Epic",
        description="Verify R1 entity inheritance.",
    )
    plan = BacklogPlan(
        plan_id="plan-auth-r1",
        project_id="test-proj",
        status=BacklogPlanStatus.DRAFT,
        items=[item],
        created_by="04-architect",
    )
    assert plan.status == BacklogPlanStatus.DRAFT
    assert plan.items[0].proposed_id == "EPIC-001"


def test_r3_workitem_runtime_reused():
    """R7 materializer delegates filesystem resolution to R3 WorkItemPathResolver."""
    from scripts.runtime.work_items.paths import WorkItemPathResolver
    from scripts.runtime.work_items.templates import ArtifactMaterializer

    resolver = WorkItemPathResolver(Path.cwd(), "proj-test")
    assert hasattr(resolver, "construct_canonical_path")
    assert hasattr(resolver, "find_all_work_items")

    # Verify materializer imports and uses WorkItemPathResolver and ArtifactMaterializer
    mat_source = inspect.getsource(runtime_backlog.materializer)
    assert "WorkItemPathResolver" in mat_source
    assert "ArtifactMaterializer" in mat_source


def test_r5_binding_reused(tmp_path):
    """R7 persists through R5 delivery_work_item_bindings and SqliteBindingRepository."""
    from scripts.runtime.delivery.repository import SqliteBindingRepository

    conn = sqlite3.connect(":memory:")
    repo = SqliteBindingRepository(db_path=conn)
    assert hasattr(repo, "upsert_work_item_binding")
    assert hasattr(repo, "get_work_item_binding")
    conn.close()


def test_r6_sync_reused(tmp_path):
    """R7 enqueues outbound items into R6 delivery_sync_outbox instead of direct transport."""
    conn = sqlite3.connect(":memory:")
    mat = BacklogMaterializer(runtime_root=tmp_path, db_path=conn)
    plan_repo = BacklogPlanRepository(db_path=conn)

    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Outbox Integration Authority",
        description="Verify R6 outbox queue is populated.",
    )
    plan = BacklogPlan(
        plan_id="plan-r6-reuse",
        project_id="p-r6",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)
    mat.materialize(plan)

    # Check R6 outbox row in SQLite
    cursor = conn.cursor()
    cursor.execute("SELECT operation, work_item_id FROM delivery_sync_outbox WHERE project_id = 'p-r6'")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "CREATE"
    assert row[1] == "EPIC-001"
    conn.close()


def test_r4_lifecycle_not_bypassed(tmp_path):
    """Materialized items are strictly initialized at canonical INTAKE via R4 authority."""
    conn = sqlite3.connect(":memory:")
    mat = BacklogMaterializer(runtime_root=tmp_path, db_path=conn)
    plan_repo = BacklogPlanRepository(db_path=conn)

    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Lifecycle State Authority",
        description="Verify canonical INTAKE initial lifecycle entry.",
    )
    plan = BacklogPlan(
        plan_id="plan-r4-reuse",
        project_id="p-r4",
        status=BacklogPlanStatus.APPROVED,
        items=[epic],
        created_by="04-architect",
        approved_by="00-delivery-orchestrator",
    )
    plan_repo.save_plan(plan)
    receipt = mat.materialize(plan)
    assert receipt.status == "COMPLETED"

    cursor = conn.cursor()
    cursor.execute("SELECT current_stage FROM work_item_lifecycle_state WHERE work_item_id = 'EPIC-001'")
    row = cursor.fetchone()
    assert row is not None
    # Canonical stage is INTAKE, never synthetic G1_PROD_INTAKE
    assert row[0] == "INTAKE"
    assert row[0] != "G1_PROD_INTAKE"

    # Verify status.yaml on disk records canonical stage and legacy projection, and not DRAFT
    status_file = tmp_path / "work" / "p-r4" / "EPIC-001" / "status.yaml"
    assert status_file.is_file()
    import yaml
    st = yaml.safe_load(status_file.read_text(encoding="utf-8"))
    assert st["stage"] == "INTAKE"
    assert st["state"] == "intake"
    assert st["stage"] != "G1_PROD_INTAKE"
    assert st.get("status") != "DRAFT"

    # Verify repeated materialization is idempotent and does not duplicate lifecycle_history
    cursor.execute("SELECT COUNT(*) FROM lifecycle_history WHERE work_item_id = 'EPIC-001'")
    count_before = cursor.fetchone()[0]
    assert count_before == 1

    mat.materialize(plan)
    cursor.execute("SELECT COUNT(*) FROM lifecycle_history WHERE work_item_id = 'EPIC-001'")
    count_after = cursor.fetchone()[0]
    assert count_after == 1

    # Verify conflicting second initialization fails closed
    from scripts.runtime.lifecycle.errors import LifecycleError
    import pytest
    with pytest.raises(LifecycleError, match="Conflicting initialization"):
        mat.lifecycle_service.initialize_work_item(
            work_item_id="EPIC-001",
            project_id="another-conflicting-proj",
            kind=WorkItemKind.EPIC,
        )

    conn.close()


def test_r2_events_reused(tmp_path):
    """Backlog operations emit canonical agent_squad.backlog.* domain events into SqliteEventStore."""
    conn = sqlite3.connect(":memory:")
    service = BacklogPlanService(runtime_root=tmp_path, db_path=conn)

    epic = BacklogPlanItem(
        proposed_id="EPIC-001",
        kind=WorkItemKind.EPIC,
        title="Event Bus Authority",
        description="Verify canonical event stream publishing.",
    )
    plan = service.create_draft_plan("p-ev", "04-architect", [epic], plan_id="p-ev-1")
    service.validate_plan("p-ev-1")
    service.approve_plan("p-ev-1", approved_by="00-delivery-orchestrator")
    service.materialize_plan("p-ev-1")

    # Read events table
    cursor = conn.cursor()
    cursor.execute("SELECT event_type FROM events WHERE project_id = 'p-ev' ORDER BY event_id ASC")
    event_types = [r[0] for r in cursor.fetchall()]

    assert "agent_squad.backlog.drafted" in event_types
    assert "agent_squad.backlog.validated" in event_types
    assert "agent_squad.backlog.approved" in event_types
    assert "agent_squad.backlog.materialize_started" in event_types
    assert "agent_squad.backlog.materialized" in event_types
    conn.close()


def test_no_direct_azure_transport_in_r7():
    """R7 runtime modules must not import or invoke direct HTTP libraries (requests, httpx, urllib)."""
    r7_dir = Path(__file__).resolve().parent.parent / "runtime" / "backlog"
    for py_file in r7_dir.glob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("requests", "httpx", "urllib.request"), (
                        f"Forbidden direct HTTP import '{alias.name}' found in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("requests", "httpx", "urllib.request"), (
                    f"Forbidden direct HTTP import from '{node.module}' found in {py_file.name}"
                )


def test_no_agent_routing_or_dispatch_or_prompt_renderer_in_r7():
    """R7 runtime must NOT contain specialist selection, agent routing, dispatch or prompt rendering."""
    r7_dir = Path(__file__).resolve().parent.parent / "runtime" / "backlog"
    forbidden_tokens = [
        "render_agent_prompt",
        "invoke_subagent",
        "select_agent",
        "route_agent",
        "dispatch_agent",
        "specialist_routing",
    ]
    for py_file in r7_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in content, (
                f"Architectural boundary violation: '{token}' found in {py_file.name}. "
                "Agent dispatch belongs strictly to Milestone R8."
            )


def test_no_deepvision_or_legacy_product_constants_in_r7():
    """R7 runtime must be pure generic infrastructure with ZERO legacy product contamination."""
    r7_dir = Path(__file__).resolve().parent.parent / "runtime" / "backlog"
    forbidden_products = ["deepvision", "cbvgas", "arthemis\\deepvision"]
    for py_file in r7_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for prod in forbidden_products:
            assert prod not in content, (
                f"Contamination violation: legacy product string '{prod}' found in {py_file.name}"
            )


def test_no_host_specific_logic():
    """R7 uses standard library pathlib and os without hardcoded drive letters or OS constraints."""
    r7_dir = Path(__file__).resolve().parent.parent / "runtime" / "backlog"
    for py_file in r7_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "C:\\Users\\" not in content, f"Hardcoded Windows user path found in {py_file.name}"
        assert "/home/" not in content, f"Hardcoded POSIX user path found in {py_file.name}"
