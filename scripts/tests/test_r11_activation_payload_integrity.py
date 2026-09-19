"""Milestone R11.1 Integration Test — End-to-End Canonical Activation Payload Integrity.

Verifies that the canonical product runtime path:
R8 ExecutionAssignment
  ↓
R9 ActivationService (SpecialistInstructionCompiler + SkillResolver + WorkContextBuilder)
  ↓
R10 DelegationService (CanonicalSessionManager + PreflightValidator + DelegationEnvelopeBuilder)
  ↓
R11 DispatchService (HostRegistry + HostDispatchPort -> FakeHostAdapter)

delivers the full governed payload (Persona, Manifest, Native Skill, Assigned Skill, Ancestor Context)
verbatim to the host adapter without direct rendering, semantic truncation, or prompt mutation.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import pytest
import yaml

from scripts.domain.delegation import ExecutionAssignment
from scripts.runtime.activation.service import ActivationService
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.delegation.service import DelegationService
from scripts.runtime.delegation.sessions import CanonicalSessionManager
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


PERSONA_SENTINEL = "PERSONA_SENTINEL_ALPHA_99: Authoritative persona identity and axioms."
MANIFEST_SENTINEL = "MANIFEST_SENTINEL_BETA_88: Governed specialist manifest description."
MANDATORY_SKILL_SENTINEL = "MANDATORY_SKILL_SENTINEL_GAMMA_77: Native TDD and verification guidelines."
ASSIGNED_SKILL_SENTINEL = "ASSIGNED_SKILL_SENTINEL_DELTA_66: Domain-specific integration logic."
ANCESTOR_CONTEXT_SENTINEL = "ANCESTOR_CONTEXT_SENTINEL_EPSILON_55: Epic architecture vision & business goals."


@pytest.fixture
def canonical_fixture(tmp_path):
    runtime_root = tmp_path
    project_id = "sentinel-project"
    work_base = runtime_root / "work" / project_id
    work_base.mkdir(parents=True, exist_ok=True)

    # 1. Setup Canonical 4-tier Work Item Hierarchy: EPIC -> FEATURE -> STORY -> TASK
    epic_dir = work_base / "EPIC-001"
    epic_dir.mkdir(parents=True, exist_ok=True)
    (epic_dir / "status.yaml").write_text(
        yaml.dump(
            {
                "id": "EPIC-001",
                "title": "Sentinel Core Epic",
                "type": "EPIC",
                "stage": "DISCOVERY",
                "status": "APPROVED",
            }
        ),
        encoding="utf-8",
    )
    (epic_dir / "epic.md").write_text(
        f"# Epic Specification\n\n{ANCESTOR_CONTEXT_SENTINEL}\n\nHigh-level architectural constraints.",
        encoding="utf-8",
    )

    feat_dir = work_base / "FEAT-001"
    feat_dir.mkdir(parents=True, exist_ok=True)
    (feat_dir / "status.yaml").write_text(
        yaml.dump(
            {
                "id": "FEAT-001",
                "parent_id": "EPIC-001",
                "title": "Sentinel Feature",
                "type": "FEATURE",
                "stage": "PLANNING",
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )

    story_dir = work_base / "US-001"
    story_dir.mkdir(parents=True, exist_ok=True)
    (story_dir / "status.yaml").write_text(
        yaml.dump(
            {
                "id": "US-001",
                "parent_id": "FEAT-001",
                "title": "Sentinel User Story",
                "type": "STORY",
                "stage": "IMPLEMENTATION",
                "status": "ACTIVE",
            }
        ),
        encoding="utf-8",
    )

    task_dir = work_base / "TASK-001"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.yaml").write_text(
        yaml.dump(
            {
                "id": "TASK-001",
                "parent_id": "US-001",
                "title": "Implement Sentinel Feature",
                "type": "TASK",
                "stage": "IMPLEMENTATION",
                "status": "ACTIVE",
                "description": "Deliver verified sentinel implementation.",
                "definition_of_done": ["Unit tests pass", "No regressions"],
            }
        ),
        encoding="utf-8",
    )

    # 2. Setup Governed Specialist Agent
    agent_id = "99-sentinel-specialist"
    agent_dir = runtime_root / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "PROMPT.md").write_text(
        f"# AGENT SYSTEM PROMPT\n\n{PERSONA_SENTINEL}\n\nStrict adherence to quality contracts.",
        encoding="utf-8",
    )

    # Native skill
    native_dir = agent_dir / "skills" / "native" / "sentinel-native"
    native_dir.mkdir(parents=True, exist_ok=True)
    (native_dir / "SKILL.md").write_text(
        f"---\nname: sentinel-native\n---\n# Native Skill\n\n{MANDATORY_SKILL_SENTINEL}\n",
        encoding="utf-8",
    )

    # Assigned skill
    assigned_dir = runtime_root / "skills" / "assigned-sentinel"
    assigned_dir.mkdir(parents=True, exist_ok=True)
    (assigned_dir / "SKILL.md").write_text(
        f"---\nname: assigned-sentinel\n---\n# Assigned Skill\n\n{ASSIGNED_SKILL_SENTINEL}\n",
        encoding="utf-8",
    )

    # Specialist Manifest
    manifest_file = agent_dir / "skills" / "manifest.yaml"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        yaml.dump(
            {
                "agent": agent_id,
                "description": MANIFEST_SENTINEL,
                "native": [
                    {"path": f"agents/{agent_id}/skills/native/sentinel-native"}
                ],
                "assigned": [
                    {"path": "skills/assigned-sentinel"}
                ],
                "discovery": {
                    "policy": "curated-local-first",
                    "maximum_loaded": 3,
                },
                "handoff": {
                    "schema": "contracts/handoff.schema.json",
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. Governance Configs
    cfg_dir = runtime_root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "skills-catalog.yaml").write_text(
        yaml.dump(
            {
                "version": 2,
                "cognitive_skill_budget": 7,
                "catalog": [
                    {"name": "assigned-sentinel", "path": "skills/assigned-sentinel"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (cfg_dir / "devops.yaml").write_text(
        yaml.dump(
            {
                "org": "cbvgas",
                "project": "Arthemis",
                "team": "agent-squad",
                "identities": {
                    "development_team": {"email": "squads@cbvgas.com"},
                    "pr_and_card_approver": {"email": "arthemis@cbvgas.com"},
                },
            }
        ),
        encoding="utf-8",
    )

    db_path = runtime_root / "banco" / "squad.db"

    return {
        "runtime_root": runtime_root,
        "project_id": project_id,
        "db_path": db_path,
        "agent_id": agent_id,
    }


def test_canonical_pipeline_end_to_end_payload_integrity(canonical_fixture):
    """Executes the full R8 -> R9 -> R10 -> R11 pipeline and asserts all sentinels are delivered."""
    f = canonical_fixture
    runtime_root = f["runtime_root"]
    project_id = f["project_id"]
    db_path = f["db_path"]
    agent_id = f["agent_id"]

    # =========================================================================
    # STEP 1: R8 ExecutionAssignment
    # =========================================================================
    from scripts.domain.delegation import AssignmentStatus

    assignment = ExecutionAssignment(
        assignment_id="ASN-SENTINEL-100",
        work_item_id="TASK-001",
        agent_id=agent_id,
        assigned_role=agent_id,
        stage="IMPLEMENTATION",
        status=AssignmentStatus.ASSIGNED,
        assigned_at=datetime.now(timezone.utc),
    )

    # =========================================================================
    # STEP 2: R9 ActivationService
    # =========================================================================
    activation_service = ActivationService(runtime_root=runtime_root, db_path=db_path)
    activation_packet = activation_service.activate(
        assignment=assignment,
        project_id=project_id,
        assigned_skills=["skills/assigned-sentinel"],
    )

    # Verify R9 ActivationPacket properties
    assert activation_packet.agent_id == agent_id
    assert activation_packet.work_item_id == "TASK-001"
    assert activation_packet.instruction_hash != ""

    # =========================================================================
    # STEP 3: R10 DelegationService & Preflight
    # =========================================================================
    session_mgr = CanonicalSessionManager(db_path=db_path)
    session_data = session_mgr.create_session(
        host="fake",
        project_root=str(runtime_root),
        work_item="TASK-001",
        project_id=project_id,
        tools=["agent-squad-mcp"],
    )
    session_id = session_data["session_id"]

    delegation_service = DelegationService(runtime_root=runtime_root, db_path=db_path)
    envelope, preflight_result = delegation_service.prepare_delegation(
        activation_packet=activation_packet,
        session_id=session_id,
        sender_role="00-delivery-orchestrator",
    )

    del_repo = DelegationRepository(db_path=db_path)
    del_row = del_repo.get_by_id(envelope.delegation_id)
    assert del_row["status"] == "READY_FOR_DISPATCH"
    assert envelope.delegation_id.startswith("DEL-")
    # Exact hash preservation from R9 to R10
    assert envelope.instruction_hash == activation_packet.instruction_hash
    assert envelope.compiled_instruction == activation_packet.compiled_instruction

    # =========================================================================
    # STEP 4: R11 DispatchService & HostDispatchPort
    # =========================================================================
    fake_adapter = FakeHostAdapter()
    registry = HostRegistry()
    registry.register_adapter("fake", fake_adapter)

    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)

    dispatch_service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
        session_manager=session_mgr,
    )

    receipt = dispatch_service.dispatch_delegation(
        delegation_id=envelope.delegation_id,
        host_override="fake",
    )

    # =========================================================================
    # STEP 5: Verify Final Host-Native Payload Content & Sentinels
    # =========================================================================
    assert len(fake_adapter.dispatches) == 1
    dispatch_record = fake_adapter.dispatches[0]
    delivered_envelope = dispatch_record["envelope"]
    delivered_instruction = delivered_envelope.compiled_instruction

    # 1. Verify Persona PROMPT.md sentinel
    assert PERSONA_SENTINEL in delivered_instruction, "Delivered payload must include Persona PROMPT.md content"

    # 2. Verify Native Skill SKILL.md sentinel
    assert MANDATORY_SKILL_SENTINEL in delivered_instruction, "Delivered payload must include Native SKILL.md content"

    # 3. Verify Assigned Skill SKILL.md sentinel
    assert ASSIGNED_SKILL_SENTINEL in delivered_instruction, "Delivered payload must include Assigned SKILL.md content"

    # 4. Verify Ancestor Context (EPIC -> TASK) sentinel
    assert ANCESTOR_CONTEXT_SENTINEL in delivered_instruction, "Delivered payload must include Ancestor Specification content"

    # 5. Verify Cryptographic Integrity
    expected_hash = hashlib.sha256(delivered_instruction.encode("utf-8")).hexdigest()
    assert delivered_envelope.instruction_hash == expected_hash
    assert receipt.instruction_hash == expected_hash
    assert receipt.receipt_type == "DISPATCH"
    assert receipt.target_agent_id == agent_id
    assert receipt.delegation_id == envelope.delegation_id
