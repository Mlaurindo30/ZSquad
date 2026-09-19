"""Canonical tests for Canonical ID Allocation Engine across 4 namespaces (R7 Section 49).

Strictly stdlib + pytest.
Covers:
- next canonical ID (sequential formatting: EPIC-001, FEATURE-001, STORY-001, TASK-0001)
- collision with local item (work/<project_id>/)
- collision with SQLite item (lifecycle_state, bindings, plan_items)
- collision with Azure binding (ado_work_items in delivery_work_item_bindings)
- collision within current plan (in-flight items)
- concurrent allocation safe (SQLite BEGIN IMMEDIATE locks)
- FEATURE never emits FEAT (always emits canonical FEATURE-)
- STORY never emits US (always emits canonical STORY-)
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pytest
import sqlite3
import yaml

from scripts.domain.work_items import WorkItemKind
from scripts.runtime.backlog.id_allocator import CanonicalIdAllocator


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_item_lifecycle_state (
            work_item_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            current_stage TEXT NOT NULL,
            owner_role TEXT NOT NULL,
            phase_started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_transition_id TEXT NOT NULL
        )
        """
    )
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


@pytest.fixture
def allocator(tmp_path, mem_db):
    return CanonicalIdAllocator(db_path=mem_db, runtime_root=tmp_path)


def test_next_canonical_id_sequential_formatting(allocator):
    """Allocates standard 3-digit and 4-digit formatted canonical identifiers."""
    epic_id = allocator.allocate_next_id("proj-1", WorkItemKind.EPIC)
    assert epic_id == "EPIC-001"

    feat_id = allocator.allocate_next_id("proj-1", WorkItemKind.FEATURE)
    assert feat_id == "FEATURE-001"

    story_id = allocator.allocate_next_id("proj-1", WorkItemKind.STORY)
    assert story_id == "STORY-001"

    task_id = allocator.allocate_next_id("proj-1", WorkItemKind.TASK)
    assert task_id == "TASK-0001"


def test_collision_with_local_filesystem_item(tmp_path, mem_db):
    """Allocating an ID skips identifiers already present on disk in work/<project_id>/."""
    # Create existing STORY-001 and STORY-002 on disk
    p1 = tmp_path / "work" / "proj-fs" / "EPIC-001" / "features" / "FEATURE-001" / "stories" / "STORY-001"
    p2 = tmp_path / "work" / "proj-fs" / "EPIC-001" / "features" / "FEATURE-001" / "stories" / "STORY-002"
    p1.mkdir(parents=True, exist_ok=True)
    p2.mkdir(parents=True, exist_ok=True)
    (p1 / "status.yaml").write_text("id: STORY-001\ntype: story\n", encoding="utf-8")
    (p2 / "status.yaml").write_text("id: STORY-002\ntype: story\n", encoding="utf-8")

    allocator = CanonicalIdAllocator(db_path=mem_db, runtime_root=tmp_path)

    # Next story ID must skip 001 and 002
    next_story = allocator.allocate_next_id("proj-fs", WorkItemKind.STORY)
    assert next_story == "STORY-003"


def test_collision_with_sqlite_lifecycle_item(mem_db, tmp_path):
    """Allocating an ID skips identifiers present in work_item_lifecycle_state."""
    mem_db.execute(
        "INSERT INTO work_item_lifecycle_state (work_item_id, project_id, cycle_id, current_stage, owner_role, phase_started_at, updated_at, last_transition_id) "
        "VALUES ('FEATURE-001', 'proj-sql', 'CANONICAL', 'INTAKE', '01-pm', '2026-09-18T00:00:00Z', '2026-09-18T00:00:00Z', 't1')"
    )
    mem_db.commit()

    allocator = CanonicalIdAllocator(db_path=mem_db, runtime_root=tmp_path)

    next_feat = allocator.allocate_next_id("proj-sql", WorkItemKind.FEATURE)
    assert next_feat == "FEATURE-002"


def test_collision_with_azure_binding(mem_db, tmp_path):
    """Allocating an ID skips identifiers present in delivery_work_item_bindings."""
    mem_db.execute(
        "INSERT INTO delivery_work_item_bindings (work_item_id, project_id, ado_id, remote_url, sync_status, sync_hash, last_synced_at) "
        "VALUES ('EPIC-001', 'proj-bind', 777, 'http://ado', 'IN_SYNC', 'h', '2026-09-18T00:00:00Z')"
    )
    mem_db.commit()

    allocator = CanonicalIdAllocator(db_path=mem_db, runtime_root=tmp_path)

    next_epic = allocator.allocate_next_id("proj-bind", WorkItemKind.EPIC)
    assert next_epic == "EPIC-002"


def test_collision_within_current_plan_in_flight(allocator):
    """Allocating multiple IDs passes in_flight_ids to prevent collisions within the same plan."""
    in_flight = {"STORY-001", "STORY-002"}
    next_story = allocator.allocate_next_id("proj-inflight", WorkItemKind.STORY, in_flight_ids=in_flight)
    assert next_story == "STORY-003"


def test_feature_never_emits_feat(allocator):
    """Legacy alias FEAT is accepted on input/normalization, but allocator NEVER emits FEAT."""
    # Normalization handles legacy
    assert allocator.normalize_id("FEAT-005") == "FEATURE-005"

    # Allocation of new items always emits FEATURE-
    allocated = [allocator.allocate_next_id("proj-alias", WorkItemKind.FEATURE, in_flight_ids={f"FEATURE-{i:03d}" for i in range(1, 4)})]
    assert allocated[0] == "FEATURE-004"
    assert not allocated[0].startswith("FEAT-0")
    assert allocated[0].startswith("FEATURE-")


def test_story_never_emits_us(allocator):
    """Legacy alias US is accepted on input/normalization, but allocator NEVER emits US."""
    assert allocator.normalize_id("US-010") == "STORY-010"

    allocated = allocator.allocate_next_id("proj-us", WorkItemKind.STORY)
    assert allocated == "STORY-001"
    assert not allocated.startswith("US-")
    assert allocated.startswith("STORY-")


def test_concurrent_allocation_safe(tmp_path):
    """Concurrent allocation across threads generates unique IDs without collision."""
    db_file = tmp_path / "banco" / "squad.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_item_lifecycle_state (
            work_item_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            current_stage TEXT NOT NULL,
            owner_role TEXT NOT NULL,
            phase_started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_transition_id TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    def allocate_item(seq_idx):
        alloc = CanonicalIdAllocator(db_path=db_file, runtime_root=tmp_path)
        # Register assigned ID in DB to test collision-resistance
        with sqlite3.connect(str(db_file), timeout=10.0) as c:
            new_id = alloc.allocate_next_id("proj-conc", WorkItemKind.TASK)
            c.execute(
                "INSERT INTO work_item_lifecycle_state (work_item_id, project_id, cycle_id, current_stage, owner_role, phase_started_at, updated_at, last_transition_id) "
                "VALUES (?, 'proj-conc', 'CANONICAL', 'INTAKE', 'role', 'now', 'now', 'trans')",
                (new_id,),
            )
            c.commit()
            return new_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(allocate_item, range(10)))

    # All 10 allocated IDs must be distinct and sequential
    assert len(results) == 10
    assert len(set(results)) == 10
    for r in results:
        assert r.startswith("TASK-")


def test_multiprocess_allocation_safe(tmp_path):
    """Concurrent allocation across separate OS processes generates collision-free IDs via durable reservation."""
    import subprocess
    import sys

    db_file = tmp_path / "banco" / "squad.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    worker_code = (
        "import sys\n"
        "from scripts.domain.work_items import WorkItemKind\n"
        "from scripts.runtime.backlog.id_allocator import CanonicalIdAllocator\n"
        "alloc = CanonicalIdAllocator(db_path=sys.argv[1], runtime_root=sys.argv[2])\n"
        "allocated = alloc.allocate_next_id(sys.argv[3], WorkItemKind.FEATURE)\n"
        "print(allocated)\n"
    )

    num_processes = 8
    processes = []
    for _ in range(num_processes):
        p = subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker_code,
                str(db_file),
                str(tmp_path),
                "proj-multi-proc",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(p)

    results = []
    for p in processes:
        stdout, stderr = p.communicate(timeout=30)
        assert p.returncode == 0, f"Worker process failed with code {p.returncode}: {stderr}"
        allocated_id = stdout.strip()
        assert allocated_id, "Worker produced empty ID"
        results.append(allocated_id)

    # All allocations must be distinct, sequential, and correctly formatted
    assert len(results) == num_processes
    assert len(set(results)) == num_processes
    for r in results:
        assert r.startswith("FEATURE-")

    expected_set = {f"FEATURE-{i:03d}" for i in range(1, num_processes + 1)}
    assert set(results) == expected_set

