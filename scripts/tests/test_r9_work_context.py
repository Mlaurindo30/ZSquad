"""R9 Unit Tests — WorkContextBuilder & Ancestor Context.

Tests complete hierarchical context compilation, Gherkin acceptance criteria parsing,
deterministic fingerprinting, and fail-closed integrity.
"""

from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.work_items import AcceptanceCriterion, WorkItemKind
from scripts.runtime.activation.context import WorkContextBuilder
from scripts.runtime.activation.errors import IncompleteContextError


@pytest.fixture
def temp_project_tree():
    """Creates a temporary 4-tier project hierarchy for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        proj_id = "test-proj"
        work_dir = root / "work" / proj_id
        work_dir.mkdir(parents=True, exist_ok=True)

        # 1. Epic
        epic_dir = work_dir / "EPIC-001"
        epic_dir.mkdir(parents=True, exist_ok=True)
        (epic_dir / "status.yaml").write_text(
            yaml.dump({
                "id": "EPIC-001",
                "type": "EPIC",
                "title": "Cloud Platform Migration",
                "stage": "PLANNING",
                "cycle": "CANONICAL_DELIVERY",
            }),
            encoding="utf-8",
        )
        (epic_dir / "epic.md").write_text("# Epic: Cloud Platform Migration\n\nMigrate core services.", encoding="utf-8")

        # 2. Feature
        feat_dir = epic_dir / "features" / "FEATURE-001"
        feat_dir.mkdir(parents=True, exist_ok=True)
        (feat_dir / "status.yaml").write_text(
            yaml.dump({
                "id": "FEATURE-001",
                "type": "FEATURE",
                "parent_id": "EPIC-001",
                "title": "Storage Layer",
                "stage": "ARCHITECTURE_DESIGN",
                "cycle": "CANONICAL_DELIVERY",
            }),
            encoding="utf-8",
        )
        (feat_dir / "feature.md").write_text("# Feature: Storage Layer\n\nImplement S3 bucket storage.", encoding="utf-8")

        # 3. Story
        story_dir = feat_dir / "stories" / "STORY-001"
        story_dir.mkdir(parents=True, exist_ok=True)
        (story_dir / "status.yaml").write_text(
            yaml.dump({
                "id": "STORY-001",
                "type": "STORY",
                "parent_id": "FEATURE-001",
                "title": "Blob Upload API",
                "stage": "IMPLEMENTATION",
                "cycle": "CANONICAL_DELIVERY",
                "story_points": 5,
            }),
            encoding="utf-8",
        )
        (story_dir / "story.md").write_text("# Story: Blob Upload API\n\nAs a user I can upload blobs.", encoding="utf-8")

        # 4. Task
        task_dir = story_dir / "tasks" / "TASK-0001"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.yaml").write_text(
            yaml.dump({
                "id": "TASK-0001",
                "type": "TASK",
                "parent_id": "STORY-001",
                "title": "Implement upload endpoint handler",
                "description": "Write controller code with validation",
                "stage": "IMPLEMENTATION",
                "cycle": "CANONICAL_DELIVERY",
                "definition_of_done": ["Unit tests passing", "Lint clean"],
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "scenario": "Valid file upload",
                        "given": "User is authenticated",
                        "when": "POST /upload with valid PDF",
                        "then": "Return HTTP 201 with blob URL",
                    }
                ],
            }),
            encoding="utf-8",
        )

        yield root, proj_id


def test_work_context_builder_resolves_complete_hierarchy(temp_project_tree):
    root, proj_id = temp_project_tree
    builder = WorkContextBuilder(runtime_root=root, project_id=proj_id)

    ctx, fingerprint = builder.build_work_context("TASK-0001")

    assert ctx.work_item_id == "TASK-0001"
    assert ctx.project_id == proj_id
    assert ctx.current_stage == "IMPLEMENTATION"
    assert ctx.title == "Implement upload endpoint handler"
    assert ctx.description == "Write controller code with validation"
    assert ctx.definition_of_done == ["Unit tests passing", "Lint clean"]
    assert len(ctx.acceptance_criteria) == 1
    ac = ctx.acceptance_criteria[0]
    assert ac.id == "AC-001"
    assert ac.scenario == "Valid file upload"

    # Verify ancestors: [STORY-001, FEATURE-001, EPIC-001]
    assert len(ctx.ancestors) == 3
    assert ctx.ancestors[0].work_item_id == "STORY-001"
    assert ctx.ancestors[0].kind == WorkItemKind.STORY
    assert ctx.ancestors[1].work_item_id == "FEATURE-001"
    assert ctx.ancestors[1].kind == WorkItemKind.FEATURE
    assert ctx.ancestors[2].work_item_id == "EPIC-001"
    assert ctx.ancestors[2].kind == WorkItemKind.EPIC

    # Verify spec artifacts collected
    assert "EPIC-001/epic.md" in ctx.ancestor_artifacts
    assert "FEATURE-001/feature.md" in ctx.ancestor_artifacts
    assert "STORY-001/story.md" in ctx.ancestor_artifacts
    assert fingerprint


def test_context_fingerprint_invalidates_on_ancestor_change(temp_project_tree):
    root, proj_id = temp_project_tree
    builder = WorkContextBuilder(runtime_root=root, project_id=proj_id)

    _, fp1 = builder.build_work_context("TASK-0001")

    # Modify ancestor specification
    epic_file = root / "work" / proj_id / "EPIC-001" / "epic.md"
    epic_file.write_text("# Epic: Updated Migration Vision with New Security Constraints", encoding="utf-8")

    _, fp2 = builder.build_work_context("TASK-0001")
    assert fp1 != fp2, "Context fingerprint MUST invalidate when ancestor content changes"


def test_work_context_builder_fails_closed_on_missing_item(temp_project_tree):
    root, proj_id = temp_project_tree
    builder = WorkContextBuilder(runtime_root=root, project_id=proj_id)

    with pytest.raises(IncompleteContextError, match="Cannot resolve work item path"):
        builder.build_work_context("NONEXISTENT-ITEM-999")


def test_context_fingerprint_stable_across_mtime_modifications(temp_project_tree):
    import os
    import time

    root, proj_id = temp_project_tree
    builder = WorkContextBuilder(runtime_root=root, project_id=proj_id)

    _, fp1 = builder.build_work_context("TASK-0001")

    # Change mtime of files without changing content
    epic_file = root / "work" / proj_id / "EPIC-001" / "epic.md"
    new_mtime = time.time() - 3600
    os.utime(epic_file, (new_mtime, new_mtime))

    task_file = root / "work" / proj_id / "EPIC-001" / "features" / "FEATURE-001" / "stories" / "STORY-001" / "tasks" / "TASK-0001" / "status.yaml"
    os.utime(task_file, (new_mtime, new_mtime))

    _, fp2 = builder.build_work_context("TASK-0001")
    assert fp1 == fp2, "Context fingerprint MUST remain identical when only mtime changes"


def test_context_fingerprint_identical_across_copied_project_tree(temp_project_tree):
    import shutil

    root1, proj_id = temp_project_tree
    builder1 = WorkContextBuilder(runtime_root=root1, project_id=proj_id)
    _, fp1 = builder1.build_work_context("TASK-0001")

    # Copy tree to a different temp directory
    with tempfile.TemporaryDirectory() as tmpdir2:
        root2 = Path(tmpdir2)
        shutil.copytree(root1 / "work", root2 / "work")
        builder2 = WorkContextBuilder(runtime_root=root2, project_id=proj_id)
        _, fp2 = builder2.build_work_context("TASK-0001")
        assert fp1 == fp2, "Copied project tree with identical logical content MUST yield identical fingerprint"

