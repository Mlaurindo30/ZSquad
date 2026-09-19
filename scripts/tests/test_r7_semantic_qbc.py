"""Canonical tests for Semantic Query-Before-Create (QBC) Engine (R7 Section 47).

Strictly stdlib + pytest.
Covers:
- exact ID duplicate
- exact external binding duplicate
- same title different parent not automatically duplicate (parent-aware)
- same semantic objective same parent detected
- possible match returns REVIEW_REQUIRED (in [0.70, 0.85))
- distinct item remains distinct (PASSED / Score < 0.70)
- SQLite candidate source
- work/ candidate source (Filesystem)
- Azure candidate source (AzureBoardsQueryPort)
- source unavailable explicit (graceful fallback)
- no prefix collision (historical bug of split("-")[0] rejected)
- no "first result wins" (evaluates full candidate corpus for best match)
"""

import json
from pathlib import Path
import pytest
import sqlite3
import yaml

from scripts.domain.backlog import BacklogPlanItem
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.backlog.qbc import (
    AzureBoardsQueryPort,
    ExistingItemContext,
    QbcMatchResult,
    SemanticQbcEngine,
    compute_composite_similarity,
)
from scripts.runtime.delivery.repository import (
    ProjectBindingRecord,
    SqliteBindingRepository,
    WorkItemBindingRecord,
)


class MockAzurePort(AzureBoardsQueryPort):
    def __init__(self, items=None):
        self.items = items or []

    def query_work_items(self, project_name: str, area_path: str = None):
        return self.items


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    # Ensure tables exist
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_work_item_bindings (
            work_item_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            ado_id INTEGER,
            remote_url TEXT NOT NULL,
            remote_rev INTEGER NOT NULL DEFAULT 0,
            sync_status TEXT NOT NULL,
            sync_hash TEXT NOT NULL,
            last_synced_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backlog_plans (
            plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_by TEXT NOT NULL,
            approved_by TEXT,
            content_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backlog_plan_items (
            item_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            proposed_id TEXT NOT NULL,
            canonical_id TEXT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            story_points INTEGER,
            parent_id TEXT,
            sequence_order INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_exact_id_duplicate(mem_db, tmp_path):
    """QBC rejects item if canonical ID already exists in SQLite or in-flight."""
    # Insert existing item into SQLite bindings
    meta = json.dumps({"kind": "STORY", "title": "Existing Story"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('STORY-001', 'p1', 101, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    proposed = BacklogPlanItem(
        proposed_id="STORY-001",
        kind=WorkItemKind.STORY,
        title="Some New Title",
        description="Valid description for the story.",
        parent_id="FEATURE-001",
    )

    res = engine.evaluate_item("p1", proposed)
    assert res.decision_status == "DUPLICATE_REJECTED"
    assert res.similarity_score == 1.0
    assert res.matched_id == "STORY-001"
    assert res.matched_source == "SQLITE"


def test_exact_external_binding_duplicate(mem_db, tmp_path):
    """QBC detects exact scope fingerprint duplicate across external bindings or items."""
    meta = json.dumps({"kind": "FEATURE", "title": "Stripe Gateway Integration", "parent_id": "EPIC-001"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('FEATURE-005', 'p1', 505, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    # Proposed item with different ID but exact scope fingerprint (same kind, same parent, same title)
    proposed = BacklogPlanItem(
        proposed_id="FEATURE-099",
        kind=WorkItemKind.FEATURE,
        title="Stripe Gateway Integration",
        description="Duplicate proposed feature with different ID.",
        parent_id="EPIC-001",
    )

    res = engine.evaluate_item("p1", proposed)
    assert res.decision_status == "DUPLICATE_REJECTED"
    assert res.similarity_score == 1.0
    assert res.matched_id == "FEATURE-005"
    assert res.matched_source == "SQLITE"


def test_same_title_different_parent_not_duplicate(mem_db, tmp_path):
    """Parent-Context Scoped Disambiguation: same task title under different parent stories is NOT duplicate."""
    # Insert existing TASK-0001 under STORY-001 in SQLite
    meta = json.dumps({"kind": "TASK", "title": "Setup Unit Tests", "parent_id": "STORY-001"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('TASK-0001', 'p1', 201, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    # Proposed TASK-0002 has SAME title "Setup Unit Tests", but under STORY-002
    proposed = BacklogPlanItem(
        proposed_id="TASK-0002",
        kind=WorkItemKind.TASK,
        title="Setup Unit Tests",
        description="Standard unit test setup for story 002.",
        parent_id="STORY-002",
    )

    res = engine.evaluate_item("p1", proposed)
    # Must NOT match TASK-0001 because they belong to different parent scopes
    assert res.decision_status == "PASSED"
    assert res.is_passed is True


def test_same_semantic_objective_same_parent_detected(mem_db, tmp_path):
    """Within the same parent scope, items with high semantic similarity (>=0.85) are rejected."""
    meta = json.dumps({"kind": "STORY", "title": "User Submits Credit Card Details", "parent_id": "FEATURE-001"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('STORY-010', 'p1', 301, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    # Semantically equivalent story under the same FEATURE-001 with >=0.85 similarity
    proposed = BacklogPlanItem(
        proposed_id="STORY-099",
        kind=WorkItemKind.STORY,
        title="User Submits Credit Card Details Now",
        description="Slight rephrasing of the existing story title.",
        parent_id="FEATURE-001",
    )

    res = engine.evaluate_item("p1", proposed)
    assert res.decision_status == "DUPLICATE_REJECTED"
    assert res.similarity_score >= 0.85
    assert res.matched_id == "STORY-010"


def test_possible_match_returns_review_required(mem_db, tmp_path):
    """Items in ambiguous zone [0.70, 0.85) return AMBIGUITY_DETECTED requiring human review."""
    meta = json.dumps({"kind": "STORY", "title": "User Submits Credit Card Details", "parent_id": "FEATURE-001"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('STORY-020', 'p1', 401, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    # Partially overlapping title in same scope (similarity ~ 0.72)
    proposed = BacklogPlanItem(
        proposed_id="STORY-021",
        kind=WorkItemKind.STORY,
        title="User Submits Credit Card Info",
        description="Alternative wording that triggers ambiguous zone.",
        parent_id="FEATURE-001",
    )

    res = engine.evaluate_item("p1", proposed)
    assert res.decision_status == "AMBIGUITY_DETECTED"
    assert 0.70 <= res.similarity_score < 0.85
    assert res.is_ambiguous is True
    assert res.matched_id == "STORY-020"


def test_distinct_item_remains_distinct(mem_db, tmp_path):
    """Items with completely distinct semantic objectives pass automated QBC."""
    meta = json.dumps({"kind": "FEATURE", "title": "Kafka Event Streaming", "parent_id": "EPIC-001"})
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('FEATURE-030', 'p1', 501, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    proposed = BacklogPlanItem(
        proposed_id="FEATURE-031",
        kind=WorkItemKind.FEATURE,
        title="PostgreSQL Database Replication",
        description="Configure WAL archiving and standby nodes.",
        parent_id="EPIC-001",
    )

    res = engine.evaluate_item("p1", proposed)
    assert res.decision_status == "PASSED"
    assert res.is_passed is True
    assert res.similarity_score < 0.70


def test_sqlite_candidate_source(mem_db, tmp_path):
    """QBC successfully discovers candidates stored in SQLite."""
    mem_db.execute(
        "INSERT INTO backlog_plans (plan_id, project_id, status, created_by, content_hash, created_at, updated_at) "
        "VALUES ('p-old', 'p1', 'APPROVED', '04-arch', 'h1', '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z')"
    )
    mem_db.execute(
        "INSERT INTO backlog_plan_items (item_id, plan_id, proposed_id, canonical_id, kind, title, description, parent_id, sequence_order, created_at) "
        "VALUES ('p-old:0', 'p-old', 'EPIC-001', 'EPIC-001', 'EPIC', 'Core Architecture Engine', 'Desc', NULL, 0, '2026-09-18T00:00:00Z')"
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)
    discovered = engine.discover_sqlite_items("p1")
    assert any(d.canonical_id == "EPIC-001" and d.source == "SQLITE" for d in discovered)


def test_filesystem_candidate_source(mem_db, tmp_path):
    """QBC successfully discovers candidates from physical work/<project_id>/ directories."""
    proj_dir = tmp_path / "work" / "p1" / "EPIC-001" / "features" / "FEATURE-001"
    proj_dir.mkdir(parents=True, exist_ok=True)
    status_content = {
        "id": "FEATURE-001",
        "type": "feature",
        "title": "Existing Filesystem Feature",
        "parent_id": "EPIC-001",
    }
    (proj_dir / "status.yaml").write_text(yaml.safe_dump(status_content), encoding="utf-8")

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)
    discovered = engine.discover_filesystem_items("p1")
    assert len(discovered) >= 1
    found = next((d for d in discovered if d.canonical_id == "FEATURE-001"), None)
    assert found is not None
    assert found.source == "FILESYSTEM"
    assert found.title == "Existing Filesystem Feature"
    assert found.parent_id == "EPIC-001"


def test_azure_candidate_source(mem_db, tmp_path):
    """QBC integrates remote Azure Boards items via AzureBoardsQueryPort."""
    mock_port = MockAzurePort(
        items=[
            {
                "id": 9901,
                "type": "Epic",
                "title": "Remote Azure Boards Epic",
                "parent_id": None,
            }
        ]
    )

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path, azure_query_port=mock_port)
    discovered = engine.discover_azure_items("p1")
    assert len(discovered) == 1
    assert discovered[0].source == "AZURE_BOARDS"
    assert discovered[0].ado_id == 9901
    assert discovered[0].title == "Remote Azure Boards Epic"


def test_source_unavailable_explicit_fallback(mem_db, tmp_path):
    """If Azure port is None or SQLite table empty, QBC handles it without crashing."""
    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path, azure_query_port=None)
    azure_items = engine.discover_azure_items("p1")
    assert azure_items == []

    # Evaluation proceeds normally on available sources
    item = BacklogPlanItem(
        proposed_id="EPIC-900",
        kind=WorkItemKind.EPIC,
        title="Standalone Project Epic",
        description="Evaluates cleanly even with remote source unavailable.",
    )
    res = engine.evaluate_item("p1", item)
    assert res.is_passed is True


def test_no_prefix_collision_bug_avoided(mem_db, tmp_path):
    """Rejects historical bug of split('-')[0] that conflated FEAT with FEATURE or prefixes."""
    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    # In legacy bug, FEAT-001 and FEATURE-001 or US-001 and STORY-001 would falsely collide or corrupt
    item1 = BacklogPlanItem(
        proposed_id="FEATURE-001",
        kind=WorkItemKind.FEATURE,
        title="First Canonical Feature",
        description="Feature description.",
        parent_id="EPIC-001",
    )
    item2 = BacklogPlanItem(
        proposed_id="FEATURE-002",
        kind=WorkItemKind.FEATURE,
        title="Second Canonical Feature",
        description="Different feature title.",
        parent_id="EPIC-001",
    )

    res = engine.evaluate_item("p1", item2, in_flight_items=[item1])
    assert res.is_passed is True
    assert res.decision_status == "PASSED"


def test_no_first_result_wins_evaluates_highest_match(mem_db, tmp_path):
    """QBC does NOT stop at the first candidate; it evaluates all candidates and picks the highest match."""
    # Candidate 1: weak match (similarity ~ 0.50)
    meta1 = json.dumps({"kind": "FEATURE", "title": "Token Dispatcher Basic", "parent_id": "EPIC-001"})
    # Candidate 2: certain duplicate (similarity ~ 0.95)
    meta2 = json.dumps({"kind": "FEATURE", "title": "JWT Token Security Dispatcher", "parent_id": "EPIC-001"})

    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('FEATURE-011', 'p1', 111, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta1,),
    )
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at, metadata_json) "
        "VALUES ('FEATURE-012', 'p1', 112, 'http://url', 'IN_SYNC', 'hash', '2026-09-18T00:00:00Z', ?)",
        (meta2,),
    )
    mem_db.commit()

    engine = SemanticQbcEngine(db_path=mem_db, runtime_root=tmp_path)

    proposed = BacklogPlanItem(
        proposed_id="FEATURE-099",
        kind=WorkItemKind.FEATURE,
        title="JWT Token Security Dispatcher",
        description="Matches Candidate 2 exactly.",
        parent_id="EPIC-001",
    )

    res = engine.evaluate_item("p1", proposed)
    # Must match FEATURE-012 (highest score 1.0), not stop at FEATURE-011
    assert res.decision_status == "DUPLICATE_REJECTED"
    assert res.matched_id == "FEATURE-012"
    assert res.similarity_score == 1.0
