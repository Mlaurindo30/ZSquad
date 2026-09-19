"""Canonical Lifecycle Cycles test suite (R4 - Seção 51).

Covers:
- development cycle
- user-story cycle
- new-project cycle alcançabilidade normal (sem injeção manual)
- bugfix cycle
- spike cycle
- release cycle
- evolution cycle
- incident cycle
- todos usam o mesmo LifecycleEngine
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.lifecycle import (
    CanonicalLifecycleService,
    get_cycle_stages,
    resolve_cycle_for_kind,
    stage_to_legacy_name,
)


@pytest.fixture
def env():
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(temp_dir.name)
    work_dir = root / "work" / "test-proj"
    work_dir.mkdir(parents=True, exist_ok=True)
    banco_dir = root / "banco"
    banco_dir.mkdir(parents=True, exist_ok=True)
    db_path = banco_dir / "squad.db"

    service = CanonicalLifecycleService(db_path=db_path, root_path=root)
    yield root, work_dir, db_path, service
    try:
        temp_dir.cleanup()
    except Exception:
        pass


def _create_item(work_dir: Path, item_id: str, state: str, cycle: str) -> Path:
    item_path = work_dir / item_id
    item_path.mkdir(parents=True, exist_ok=True)
    (item_path / "gate-decisions").mkdir(parents=True, exist_ok=True)
    (item_path / "handoffs").mkdir(parents=True, exist_ok=True)
    (item_path / "evidence").mkdir(parents=True, exist_ok=True)
    (item_path / "receipts").mkdir(parents=True, exist_ok=True)

    status_data = {
        "id": item_id,
        "type": "feature",
        "cycle": cycle,
        "state": state,
        "risk": "low",
        "owner": "delivery-orchestrator",
        "active_agents": ["delivery-orchestrator"],
        "current_gate": None,
        "artifacts": ["status.yaml"],
        "phase_started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (item_path / "status.yaml").write_text(yaml.safe_dump(status_data), encoding="utf-8")
    return item_path


def _add_gate(item_path: Path, gate_id: str, decision: str = "approved") -> None:
    g_file = item_path / "gate-decisions" / f"{gate_id}.yaml"
    data = {
        "decision_id": f"GD-{gate_id}",
        "gate_id": gate_id,
        "work_item_id": item_path.name,
        "decision": decision,
        "decider": "solution-architect",
        "criteria": [{"name": "crit-1", "result": "pass"}],
        "evidence": ["doc.md"],
    }
    g_file.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_development_cycle_resolution_and_stages(env):
    """Ciclo development mapeia feature/task/epic e transiciona ordenadamente."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("feature") == "development"
    assert resolve_cycle_for_kind("task") == "development"

    stages = get_cycle_stages("development", service.cycles_cfg)
    assert len(stages) >= 5
    assert stages[-1] == LifecycleStage.DONE

    item = _create_item(work_dir, "FEAT-DEV-01", state="discovery", cycle="development")
    res = service.transition("FEAT-DEV-01", project_id="test-proj", target_stage="blueprint", item_path=item)
    assert res["state"] == "blueprint"
    assert res["cycle"] == "development"


def test_user_story_cycle_resolution_and_stages(env):
    """Ciclo user-story mapeia story/user-story e transiciona ordenadamente."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("story") == "user-story"
    assert resolve_cycle_for_kind("user-story") == "user-story"

    stages = get_cycle_stages("user-story", service.cycles_cfg)
    assert LifecycleStage.REQUIREMENTS_PRODUCT in stages
    assert stages[-1] == LifecycleStage.DONE

    item = _create_item(work_dir, "US-STORY-01", state="discovery", cycle="user-story")
    res = service.transition("US-STORY-01", project_id="test-proj", target_stage="blueprint", item_path=item)
    assert res["state"] == "blueprint"
    assert res["cycle"] == "user-story"


def test_new_project_cycle_normal_reachability(env):
    """Ciclo new-project é alcançável normalmente sem injeção manual (resolvendo R0-LIFE-014)."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("new-project") == "new-project"
    assert resolve_cycle_for_kind("project_setup") == "new-project"
    assert resolve_cycle_for_kind("setup") == "new-project"

    stages = get_cycle_stages("new-project", service.cycles_cfg)
    assert stages[-1] == LifecycleStage.DONE

    item = _create_item(work_dir, "PROJ-SETUP-01", state="discovery", cycle="new-project")
    res = service.transition("PROJ-SETUP-01", project_id="test-proj", target_stage="blueprint", item_path=item)
    assert res["state"] == "blueprint"
    assert res["cycle"] == "new-project"


def test_bugfix_cycle_resolution_and_stages(env):
    """Ciclo bugfix mapeia bug/bugfix com entrypoint em implementation."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("bug") == "bugfix"
    assert resolve_cycle_for_kind("bugfix") == "bugfix"

    stages = get_cycle_stages("bugfix", service.cycles_cfg)
    assert LifecycleStage.IMPLEMENTATION in stages
    assert stages[-1] == LifecycleStage.DONE

    item = _create_item(work_dir, "BUG-FIX-01", state="implementation", cycle="bugfix")
    (item / "implementation.diff").write_text("+ fix bug", encoding="utf-8")
    res = service.transition("BUG-FIX-01", project_id="test-proj", target_stage="code-security-review", item_path=item)
    assert res["state"] == "code-security-review"
    assert res["cycle"] == "bugfix"


def test_spike_cycle_resolution_and_stages(env):
    """Ciclo spike mapeia spike/study focado em blueprint -> done."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("spike") == "spike"
    assert resolve_cycle_for_kind("study") == "spike"

    stages = get_cycle_stages("spike", service.cycles_cfg)
    assert stages[-1] == LifecycleStage.DONE

    item = _create_item(work_dir, "SPIKE-TECH-01", state="blueprint", cycle="spike")
    _add_gate(item, "G1-product", "approved")
    res = service.transition("SPIKE-TECH-01", project_id="test-proj", target_stage="done", item_path=item)
    assert res["state"] == "done"
    assert res["cycle"] == "spike"


def test_release_cycle_resolution_and_stages(env):
    """Ciclo release mapeia release/rel."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("release") == "release"
    assert resolve_cycle_for_kind("rel") == "release"

    stages = get_cycle_stages("release", service.cycles_cfg)
    assert stages[-1] == LifecycleStage.DONE

    # Release cycle in cycles.yaml has implementation -> done or governance-release -> done
    first_state = stage_to_legacy_name(stages[0])
    item = _create_item(work_dir, "REL-ROLLOUT-01", state=first_state, cycle="release")
    if first_state == "implementation":
        (item / "implementation.diff").write_text("+ release commit", encoding="utf-8")
        _add_gate(item, "G4-code-security", "approved")

    # Advancing through release
    next_stage_name = stage_to_legacy_name(stages[1])
    if first_state == "governance-release":
        _add_gate(item, "G6-governance-release", "approved")
    res = service.transition("REL-ROLLOUT-01", project_id="test-proj", target_stage=next_stage_name, item_path=item)
    assert res["cycle"] == "release"


def test_evolution_cycle_resolution_and_stages(env):
    """Ciclo evolution mapeia evolution/evol."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("evolution") == "evolution"
    assert resolve_cycle_for_kind("evol") == "evolution"

    stages = get_cycle_stages("evolution", service.cycles_cfg)
    assert stages[-1] == LifecycleStage.DONE

    first_state = stage_to_legacy_name(stages[0])
    second_state = stage_to_legacy_name(stages[1])
    item = _create_item(work_dir, "EVOL-REFACTOR-01", state=first_state, cycle="evolution")
    res = service.transition("EVOL-REFACTOR-01", project_id="test-proj", target_stage=second_state, item_path=item)
    assert res["cycle"] == "evolution"


def test_incident_cycle_resolution_and_stages(env):
    """Ciclo incident mapeia incident/hotfix."""
    _, work_dir, _, service = env
    assert resolve_cycle_for_kind("incident") == "incident"
    assert resolve_cycle_for_kind("hotfix") == "incident"

    stages = get_cycle_stages("incident", service.cycles_cfg)
    assert stages[-1] == LifecycleStage.DONE

    first_state = stage_to_legacy_name(stages[0])
    second_state = stage_to_legacy_name(stages[1])
    item = _create_item(work_dir, "INCIDENT-PROD-01", state=first_state, cycle="incident")
    if first_state == "implementation":
        (item / "implementation.diff").write_text("+ hotfix patch", encoding="utf-8")
    res = service.transition("INCIDENT-PROD-01", project_id="test-proj", target_stage=second_state, item_path=item)
    assert res["cycle"] == "incident"


def test_all_cycles_share_same_lifecycle_engine(env):
    """Todos os 8 ciclos utilizam o mesmo CanonicalLifecycleService sem engines bifurcados."""
    _, _, _, service = env
    all_8_cycles = [
        "development", "user-story", "new-project", "bugfix",
        "spike", "release", "evolution", "incident",
    ]
    for cycle_name in all_8_cycles:
        stages = get_cycle_stages(cycle_name, service.cycles_cfg)
        assert len(stages) >= 2
        assert stages[-1] == LifecycleStage.DONE
