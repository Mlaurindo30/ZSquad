"""R9 Unit Tests — ActivationRepository and ActivationService.

Tests SQLite persistence of ActivationPackets, idempotency, and end-to-end activation
transformation from R8 ExecutionAssignment.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.delegation import (
    ActivationPacket,
    AncestorSnapshot,
    AssignmentStatus,
    ExecutionAssignment,
    WorkContext,
)
from scripts.domain.work_items import AcceptanceCriterion, WorkItemKind
from scripts.runtime.activation.repository import ActivationRepository
from scripts.runtime.activation.service import ActivationService


@pytest.fixture
def temp_activation_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db_path = root / "test_squad.db"

        # 1. Project hierarchy
        proj_id = "proj-alpha"
        item_dir = root / "work" / proj_id / "TASK-0001"
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "status.yaml").write_text(
            yaml.dump({
                "id": "TASK-0001",
                "type": "TASK",
                "title": "Build Auth Middleware",
                "description": "Implement JWT validation middleware",
                "stage": "IMPLEMENTATION",
            }),
            encoding="utf-8",
        )

        # 2. Agent prompt & manifest
        agent_dir = root / "agents" / "software-engineer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "PROMPT.md").write_text("# Software Engineer Persona", encoding="utf-8")
        manifest_dir = agent_dir / "skills"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.yaml").write_text(
            yaml.dump({
                "agent": "software-engineer",
                "native": [{"path": "skills/clean-code"}],
                "assigned": [],
                "discovery": {"maximum_loaded": 2},
                "handoff": {"schema": "contracts/handoff.schema.json"},
            }),
            encoding="utf-8",
        )

        # 3. Skill file
        s_dir = root / "skills" / "clean-code"
        s_dir.mkdir(parents=True, exist_ok=True)
        (s_dir / "SKILL.md").write_text("# Clean Code Rules", encoding="utf-8")

        yield root, db_path, proj_id


def test_activation_repository_save_and_retrieve(temp_activation_env):
    root, db_path, proj_id = temp_activation_env
    repo = ActivationRepository(db_path=db_path)

    ctx = WorkContext(
        work_item_id="TASK-0001",
        project_id=proj_id,
        current_stage="IMPLEMENTATION",
        title="Build Auth Middleware",
        description="Implement JWT validation middleware",
        definition_of_done=["Done"],
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-01",
                scenario="Valid token",
                given="User has token",
                when="Request sent",
                then="Allow access",
            )
        ],
        ancestors=[
            AncestorSnapshot(
                work_item_id="STORY-001",
                kind=WorkItemKind.STORY,
                title="Auth Story",
                stage="IMPLEMENTATION",
                spec_summary="User auth",
            )
        ],
        ancestor_artifacts={},
        active_receipts=["RECEIPT_CODE"],
        filesystem_scope=["/scope"],
    )

    instr = "Authoritative compiled instruction text."
    ihash = hashlib.sha256(instr.encode("utf-8")).hexdigest()

    packet = ActivationPacket(
        session_id="ACT-001",
        agent_id="software-engineer",
        role_name="Software Engineer",
        work_item_id="TASK-0001",
        work_context=ctx,
        skill_manifest={"agent": "software-engineer"},
        compiled_instruction=instr,
        instruction_hash=ihash,
        created_at=datetime.now(timezone.utc),
    )

    repo.save(packet=packet, assignment_id="ASG-001", context_fingerprint="fp123")

    retrieved = repo.get_by_id("ACT-001")
    assert retrieved is not None
    assert retrieved.session_id == "ACT-001"
    assert retrieved.agent_id == "software-engineer"
    assert retrieved.role_name == "Software Engineer"
    assert retrieved.work_item_id == "TASK-0001"
    assert retrieved.instruction_hash == ihash
    assert retrieved.work_context.title == "Build Auth Middleware"
    assert len(retrieved.work_context.acceptance_criteria) == 1
    assert len(retrieved.work_context.ancestors) == 1

    repo.close()


def test_activation_service_end_to_end_and_idempotency(temp_activation_env):
    root, db_path, proj_id = temp_activation_env
    service = ActivationService(runtime_root=root, db_path=db_path)

    assignment = ExecutionAssignment(
        assignment_id="ASG-001",
        work_item_id="TASK-0001",
        agent_id="software-engineer",
        assigned_role="Software Engineer",
        stage="IMPLEMENTATION",
        status=AssignmentStatus.ASSIGNED,
    )

    try:
        # 1. First activation
        packet1 = service.activate(assignment=assignment, project_id=proj_id)
        assert packet1.work_item_id == "TASK-0001"
        assert packet1.agent_id == "software-engineer"
        assert "Software Engineer Persona" in packet1.compiled_instruction

        # 2. Second activation (idempotent: returns existing packet without changing session_id)
        packet2 = service.activate(assignment=assignment, project_id=proj_id)
        assert packet2.session_id == packet1.session_id
        assert packet2.instruction_hash == packet1.instruction_hash
    finally:
        service.close()

