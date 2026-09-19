"""Canonical Lifecycle WIP test suite (R4 - Seção 50).

Covers:
- abaixo do WIP -> entrada permitida
- no limite -> entrada rejeitada
- sair do estágio libera capacidade
- contagem escopada corretamente por projeto
- config de WIP inválida rejeitada
- WIP não pode ser burlado via advance_state
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.agent_squad import AgentSquad, SquadError
from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.lifecycle import (
    CanonicalLifecycleService,
    ConfigurationError,
    WIPController,
    WIPLimitExceededError,
)

ROOT = Path(__file__).resolve().parents[2]


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


def _create_item(work_dir: Path, item_id: str, state: str = "blueprint", risk: str = "low", cycle: str = "development") -> Path:
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
        "risk": risk,
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


def test_below_wip_admission_allowed(env):
    """Abaixo do limite de WIP (ex: 1 item em scaffolding onde o limite é 2), entrada é permitida."""
    _, work_dir, _, service = env
    _create_item(work_dir, "FEAT-WIP-OCCUPIED", state="scaffolding")

    item = _create_item(work_dir, "FEAT-WIP-NEW", state="blueprint")
    _add_gate(item, "G1-product", "approved")

    res = service.transition("FEAT-WIP-NEW", project_id="test-proj", item_path=item)
    assert res["state"] == "scaffolding"


def test_at_limit_admission_rejected(env):
    """No limite de WIP (2 itens em scaffolding), admissão de um 3º item é rejeitada com WIPLimitExceededError."""
    _, work_dir, _, service = env
    _create_item(work_dir, "FEAT-WIP-1", state="scaffolding")
    _create_item(work_dir, "FEAT-WIP-2", state="scaffolding")

    item3 = _create_item(work_dir, "FEAT-WIP-3", state="blueprint")
    _add_gate(item3, "G1-product", "approved")

    with pytest.raises(WIPLimitExceededError, match="(?i)WIP breach"):
        service.transition("FEAT-WIP-3", project_id="test-proj", item_path=item3)


def test_exiting_stage_frees_capacity(env):
    """Sair do estágio libera capacidade para outro item entrar."""
    _, work_dir, _, service = env
    item1 = _create_item(work_dir, "FEAT-WIP-REL1", state="scaffolding")
    _create_item(work_dir, "FEAT-WIP-REL2", state="scaffolding")

    # item3 tenta entrar mas é bloqueado
    item3 = _create_item(work_dir, "FEAT-WIP-REL3", state="blueprint")
    _add_gate(item3, "G1-product", "approved")
    with pytest.raises(WIPLimitExceededError):
        service.transition("FEAT-WIP-REL3", project_id="test-proj", item_path=item3)

    # item1 avança de scaffolding para implementation (liberando 1 vaga)
    _add_gate(item1, "G3-readiness", "approved")
    service.transition("FEAT-WIP-REL1", project_id="test-proj", target_stage="implementation", item_path=item1)

    # Agora item3 consegue entrar em scaffolding!
    res = service.transition("FEAT-WIP-REL3", project_id="test-proj", item_path=item3)
    assert res["state"] == "scaffolding"


def test_wip_counts_scoped_by_project(env):
    """Itens em um projeto não consom a cota de WIP de outro projeto."""
    root, _, db_path, service = env
    proj_a = root / "work" / "proj-a"
    proj_b = root / "work" / "proj-b"
    proj_a.mkdir(parents=True, exist_ok=True)
    proj_b.mkdir(parents=True, exist_ok=True)

    # 2 itens em proj-a em scaffolding (limite 2)
    _create_item(proj_a, "FEAT-A-1", state="scaffolding")
    _create_item(proj_a, "FEAT-A-2", state="scaffolding")

    # Item em proj-b quer entrar em scaffolding
    item_b = _create_item(proj_b, "FEAT-B-1", state="blueprint")
    _add_gate(item_b, "G1-product", "approved")

    # Deve conseguir entrar porque proj-b tem zero itens em scaffolding!
    res = service.transition("FEAT-B-1", project_id="proj-b", item_path=item_b)
    assert res["state"] == "scaffolding"


def test_invalid_wip_config_rejected():
    """Configuração de WIP inválida (negativa ou malformada) é rejeitada com ConfigurationError."""
    wip_ctrl = WIPController()

    invalid_cfg_neg = {
        "flow": {
            "wip_limits": {
                "scaffolding": -5
            }
        }
    }
    with pytest.raises(ConfigurationError, match="(?i)(invalid negative wip limit)"):
        wip_ctrl.get_limit("scaffolding", workflow_cfg=invalid_cfg_neg)

    invalid_cfg_str = {
        "flow": {
            "wip_limits": {
                "scaffolding": "not-a-number"
            }
        }
    }
    with pytest.raises(ConfigurationError, match="(?i)(invalid wip limit)"):
        wip_ctrl.get_limit("scaffolding", workflow_cfg=invalid_cfg_str)


def test_wip_cannot_be_bypassed_via_advance_state(env):
    """A fachada advance_state respeita os limites de WIP e levanta SquadError ao tentar violar a capacidade."""
    _, work_dir, _, _ = env
    squad = AgentSquad(root=ROOT, project_name=None, allow_legacy=True)

    # Cria 2 itens em scaffolding
    _create_item(work_dir, "FEAT-ADV-1", state="scaffolding")
    _create_item(work_dir, "FEAT-ADV-2", state="scaffolding")

    # Cria 3º item em blueprint
    item3 = squad.init_work_item("FEAT-ADV-3", "low", base=work_dir)
    _add_gate(item3, "G1-product", "approved")

    with pytest.raises(SquadError, match="(?i)(wip breach|wip limit)"):
        squad.advance_state(item3)
