"""
R0 Red Diagnostic Suite — Work Item Model, Backlog, and Azure DevOps Integration.
Proves failures in R0-WORK-* and R0-ADO-* against current broken runtime behavior.
DO NOT FIX IN R0. These tests MUST FAIL (RED) to demonstrate the current defects.
"""

from pathlib import Path
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from agent_squad import AgentSquad, SquadError, read_yaml



@pytest.fixture
def isolated_squad():
    temp_dir = tempfile.TemporaryDirectory()
    work_dir = Path(temp_dir.name) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    squad = AgentSquad(ROOT, project_name=None, allow_legacy=True)
    yield squad, work_dir
    temp_dir.cleanup()



def test_r0_work_001_canonical_id_naming_mismatch(isolated_squad):
    """
    R0-WORK-001: Canonical ID mismatch.
    Invariant: Canonical names 'FEATURE-001' and 'STORY-001' must be supported without requiring --type.
    Current defect: _legacy_type_for_id only recognizes 'FEAT' and 'US'; it rejects 'FEATURE-001' as invalid ID.
    """
    squad, work_dir = isolated_squad

    # Invariant: init_work_item("FEATURE-001", "low") must succeed.
    # Current behavior: raises SquadError("ID inválido: FEATURE-001")!
    item = squad.init_work_item("FEATURE-001", "low", base=work_dir)
    assert item.exists()


def test_r0_work_002_physical_hierarchy_must_nest_items(isolated_squad):
    """
    R0-WORK-002: Physical hierarchy.
    Invariant: In a 4-tier hierarchy, child items should be stored hierarchically under parent paths.
    Current defect: All items are completely flattened in base dir (work/<project>/<ID>).
    """
    squad, work_dir = isolated_squad
    epic = squad.init_work_item("EPIC-001", "low", base=work_dir, item_type="epic")
    feat = squad.init_work_item("FEAT-001", "low", base=work_dir, item_type="feature", parent_id="EPIC-001")

    # Invariant: Child item FEAT-001 must be nested inside EPIC-001 folder.
    # Current behavior: feat is work_dir / "FEAT-001", completely sibling and flat!
    assert feat.parent == epic, (
        f"R0-WORK-002 CONFIRMED: Hierarchy is flat; feature path {feat} is not inside epic path {epic}"
    )


def test_r0_work_003_task_must_not_materialize_epic_artifacts(isolated_squad):
    """
    R0-WORK-003: Wrong template materialization.
    Invariant: A technical Task must not materialize high-level product artifacts (epic.md, product-goal.md, backlog.md).
    Current defect: _init_work_item_unlocked unconditionally copies epic.md, product-goal.md, backlog.md to every item.
    """
    squad, work_dir = isolated_squad
    epic = squad.init_work_item("EPIC-001", "low", base=work_dir, item_type="epic")
    feat = squad.init_work_item("FEAT-001", "low", base=work_dir, item_type="feature", parent_id="EPIC-001")
    story = squad.init_work_item("US-001", "low", base=work_dir, item_type="story", parent_id="FEAT-001")
    task = squad.init_work_item("TASK-001", "low", base=work_dir, item_type="task", parent_id="US-001")



    # Invariant: Technical task should NOT have epic.md or product-goal.md
    # Current behavior: task has epic.md, product-goal.md, backlog.md, discovery/brief.md!
    assert not (task / "epic.md").exists(), (
        "R0-WORK-003 CONFIRMED: Technical Task materialized irrelevant epic.md product artifact"
    )
    assert not (task / "product-goal.md").exists(), (
        "R0-WORK-003 CONFIRMED: Technical Task materialized irrelevant product-goal.md artifact"
    )


def test_r0_work_005_qbc_false_matching_distinct_epics(isolated_squad):
    """
    R0-WORK-005: QBC false matching.
    Invariant: Creating EPIC-002 when EPIC-001 exists must be allowed (they are distinct epics).
    Current defect: QBC checks work_id.split('-')[0] == cand_id.split('-')[0] ('epic' == 'epic'),
    falsely classifying EPIC-002 as duplicate of EPIC-001!
    """
    squad, work_dir = isolated_squad
    squad.init_work_item("EPIC-001", "low", base=work_dir, item_type="epic")

    # Invariant: Creating EPIC-002 must succeed without --force.
    # Current behavior: Raises SquadError: QBC Violation: Duplicate epic detected (EPIC-001).
    epic2 = squad.init_work_item("EPIC-002", "low", base=work_dir, item_type="epic", force=False)
    assert epic2.exists()


def test_r0_work_006_content_quality_enforcement(isolated_squad):
    """
    R0-WORK-006: Content quality enforcement.
    Invariant: Creating a work item with empty description, missing acceptance criteria, and missing DoD must be rejected.
    Current defect: init_work_item allows creating placeholder items without any content quality checks.
    """
    squad, work_dir = isolated_squad

    # Invariant: Creating an empty/unspecified work item must fail content quality check.
    # Current behavior: init_work_item succeeds with purely blank/placeholder files.
    with pytest.raises(SquadError, match="(?i)(quality|acceptance criteria|definition of done|description required)"):
        squad.init_work_item("US-EMPTY-CONTENT", "medium", base=work_dir)


def test_r0_ado_001_project_binding_mandatory(isolated_squad):
    """
    R0-ADO-001: Project binding mandatory?
    Invariant: Work item creation must require a validated Azure DevOps container binding (org/project/team/area).
    Current defect: Work items can be created with zero ADO configuration or binding (devops=False default).
    """
    squad, work_dir = isolated_squad

    # Invariant: Creating a governed work item without complete ADO binding must be rejected.
    # Current behavior: It creates local files happily without checking ADO binding.
    with pytest.raises(SquadError, match="(?i)(ado binding|azure devops binding|devops project required)"):
        squad.init_work_item("US-NO-ADO-BINDING", "medium", base=work_dir)


def test_r0_ado_002_product_lifecycle_confuses_product_with_ado_team_project():
    """
    R0-ADO-002: Product vs Azure Team Project confusion.
    Invariant: Product onboarding must NOT create a new Azure DevOps Team Project;
    it should provision resources inside the existing organizational Team Project container.
    Current defect: azure_devops_lifecycle.py calls create_project(project_name=self.project_name),
    attempting to create an entire new Team Project for each software product!
    """
    import inspect
    from scripts import azure_devops_lifecycle

    src = inspect.getsource(azure_devops_lifecycle.AzureDevOpsLifecycle._phase1_create_project)

    # Invariant: _phase1_create_project should not call create_project for the product.
    # Current behavior: Calls create_project(project_name=self.project_name).
    assert "create_project" not in src, (
        "R0-ADO-002 CONFIRMED: _phase1_create_project calls create_project, confusing product with ADO Team Project"
    )


def test_r0_ado_003_target_project_context_contamination():
    """
    R0-ADO-003: Target project context contamination.
    Invariant: Target product prompt must not default to Agent Squad repository constants ('Arthemis', 'agent-squad', 'cbvgas').
    Current defect: _build_azure_devops_section in render_agent_prompt.py has hardcoded fallbacks to Arthemis/agent-squad/cbvgas.
    """
    import inspect
    from scripts import render_agent_prompt

    src = inspect.getsource(render_agent_prompt._build_azure_devops_section)

    # Invariant: Source must not hardcode 'Arthemis' or 'cbvgas' as fallback defaults for other products.
    # Current behavior: hardcodes 'cbvgas' and 'Arthemis'.
    assert "Arthemis" not in src and "cbvgas" not in src, (
        "R0-ADO-003 CONFIRMED: _build_azure_devops_section contains hardcoded 'Arthemis' and 'cbvgas' fallbacks"
    )


def test_r0_ado_005_ado_failure_semantics_must_not_be_swallowed():
    """
    R0-ADO-005: ADO failure semantics.
    Invariant: A failure in syncing state to Azure DevOps during advance_state must fail or flag PENDING_SYNC.
    Current defect: advance_state in agent_squad.py catches all exceptions with except Exception as _e: print/warn
    and silently allows local state to advance as success!
    """
    import inspect
    from scripts import agent_squad

    src = inspect.getsource(agent_squad.AgentSquad.advance_state)

    # Invariant: advance_state must not swallow ADO sync errors with warning print.
    # Current behavior: contains 'print(f"WARN advance_state_devops_sync_failed: {_e}")'
    assert "WARN advance_state_devops_sync_failed" not in src, (
        "R0-ADO-005 CONFIRMED: advance_state catches and swallows Azure DevOps sync failure"
    )
