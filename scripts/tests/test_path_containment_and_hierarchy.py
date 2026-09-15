import shutil
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad, SquadError
from project_context import PathContainmentGuard, PathContainmentViolation

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def squad_env():
    project_id = 'test-containment-hierarchy'
    work_dir = ROOT / 'work' / project_id
    lock_dir = ROOT / '.locks' / project_id

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name=project_id)
    yield squad
    cleanup()


def test_path_containment_guard_blocks_external_work(squad_env):
    external_target = ROOT.parent / 'ZSquad_push' / 'work' / 'epic-1'
    with pytest.raises(PathContainmentViolation, match='PathContainmentViolation'):
        PathContainmentGuard.validate_work_path(external_target, ROOT, squad_env.project_name)


def test_path_containment_guard_allows_canonical_work(squad_env):
    canonical_target = squad_env._work_base() / 'EPIC-TEST-01'
    validated = PathContainmentGuard.validate_work_path(canonical_target, ROOT, squad_env.project_name)
    assert validated.resolve() == canonical_target.resolve()


def test_4_tier_hierarchy_creation_success(squad_env):
    epic_path = squad_env.init_work_item('EPIC-PROJ-01', 'high', item_type='epic')
    epic_status = yaml.safe_load((epic_path / 'status.yaml').read_text(encoding='utf-8'))
    assert epic_status['hierarchy_level'] == 1
    assert epic_status['type'] == 'epic'

    feat_path = squad_env.init_work_item('FEAT-PROJ-01', 'medium', item_type='feature', parent_id='EPIC-PROJ-01')
    feat_status = yaml.safe_load((feat_path / 'status.yaml').read_text(encoding='utf-8'))
    assert feat_status['hierarchy_level'] == 2
    assert feat_status['parent_id'] == 'EPIC-PROJ-01'

    story_path = squad_env.init_work_item('US-PROJ-01', 'medium', item_type='story', parent_id='FEAT-PROJ-01', story_points=5)
    story_status = yaml.safe_load((story_path / 'status.yaml').read_text(encoding='utf-8'))
    assert story_status['hierarchy_level'] == 3
    assert story_status['parent_id'] == 'FEAT-PROJ-01'
    assert story_status['story_points'] == 5

    task_path = squad_env.init_work_item('TASK-PROJ-01', 'low', item_type='task', parent_id='US-PROJ-01')
    task_status = yaml.safe_load((task_path / 'status.yaml').read_text(encoding='utf-8'))
    assert task_status['hierarchy_level'] == 4
    assert task_status['parent_id'] == 'US-PROJ-01'


def test_hierarchy_validation_rejections(squad_env):
    with pytest.raises(SquadError, match='requires a parent_id'):
        squad_env.init_work_item('FEAT-FAIL-01', 'medium', item_type='feature')

    with pytest.raises(SquadError, match='Epics cannot have a parent_id'):
        squad_env.init_work_item('EPIC-FAIL-01', 'high', item_type='epic', parent_id='SOME-PARENT')

    squad_env.init_work_item('EPIC-PARENT-01', 'high', item_type='epic')
    with pytest.raises(SquadError, match='does not match required parent type'):
        squad_env.init_work_item('US-FAIL-01', 'medium', item_type='story', parent_id='EPIC-PARENT-01', story_points=3)


def test_story_points_fibonacci_and_max_8_rule(squad_env):
    squad_env.init_work_item('EPIC-SP-01', 'high', item_type='epic')
    squad_env.init_work_item('FEAT-SP-02', 'medium', item_type='feature', parent_id='EPIC-SP-01')

    with pytest.raises(SquadError, match='story_points deve usar Fibonacci'):
        squad_env.init_work_item('US-SP-04', 'medium', item_type='story', parent_id='FEAT-SP-02', story_points=4)

    with pytest.raises(SquadError, match='Story Points > 8'):
        squad_env.init_work_item('US-SP-13', 'medium', item_type='story', parent_id='FEAT-SP-02', story_points=13)


def test_qbc_deduplication_protocol(squad_env):
    squad_env.init_work_item('EPIC-AUTH-01', 'high', item_type='epic')

    with pytest.raises(SquadError, match='QBC Violation: Duplicate epic detected'):
        squad_env.init_work_item('EPIC-AUTH-02', 'high', item_type='epic')

    forced_epic = squad_env.init_work_item('EPIC-AUTH-02', 'high', item_type='epic', force=True)
    assert forced_epic.exists()
