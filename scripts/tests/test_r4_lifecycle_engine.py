"""Canonical Lifecycle Engine test suite (R4 - Seção 47).

Covers:
- desenvolvimento: entry stage correto
- estágios canônicos alcançáveis em ordem legal
- pular Discovery rejeitado
- pular Requirements/Product rejeitado
- pular Planning rejeitado
- pular Architecture rejeitado
- pular Readiness rejeitado
- transição arbitrária para trás rejeitada
- salto arbitrário para o futuro rejeitado
- DONE terminal
- estado idêntico tratado explicitamente
- estado desconhecido rejeitado
- ciclo desconhecido rejeitado
- fachada advance_state delega para o engine
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
    InvalidTransitionError,
    LifecycleError,
    get_cycle_stages,
    normalize_stage,
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


def _create_item(work_dir: Path, item_id: str, state: str = "intake", risk: str = "low", cycle: str = "development") -> Path:
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
        "evidence": ["test.md"],
    }
    g_file.write_text(yaml.safe_dump(data), encoding="utf-8")


def _add_receipt(item_path: Path, receipt_type: str = "execution") -> None:
    r_file = item_path / "receipts" / f"{receipt_type}-receipt.yaml"
    r_file.write_text(yaml.safe_dump({"type": receipt_type, "status": "verified"}), encoding="utf-8")


ROOT = Path(__file__).resolve().parents[2]


def test_development_entry_stage_correct(env):
    """Verifica se o ciclo de desenvolvimento tem como entry stage o primeiro estágio canônico."""
    _, _, _, service = env
    dev_stages = get_cycle_stages("development", service.cycles_cfg)
    assert dev_stages[0] in {LifecycleStage.INTAKE, LifecycleStage.DISCOVERY, LifecycleStage.REQUIREMENTS_PRODUCT}
    assert dev_stages[-1] == LifecycleStage.DONE


def test_canonical_stages_reachable_in_legal_order(env):
    """Verifica se os estágios do ciclo de desenvolvimento podem ser percorridos em ordem legal."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-STAGES-01", state="discovery", cycle="development")

    # In discovery, advance to blueprint
    res = service.transition("DEV-STAGES-01", project_id="test-proj", target_stage="blueprint", item_path=item_path)
    assert res["state"] == "blueprint"

    # From blueprint, approve G1 and advance to scaffolding
    _add_gate(item_path, "G1-product", "approved")
    res = service.transition("DEV-STAGES-01", project_id="test-proj", target_stage="scaffolding", item_path=item_path)
    assert res["state"] == "scaffolding"

    # From scaffolding, approve G3-readiness and advance to implementation
    _add_gate(item_path, "G3-readiness", "approved")
    res = service.transition("DEV-STAGES-01", project_id="test-proj", target_stage="implementation", item_path=item_path)
    assert res["state"] == "implementation"


def test_skip_discovery_rejected(env):
    """Pular Discovery diretamente de intake para blueprint ou scaffolding deve ser rejeitado."""
    _, work_dir, _, service = env
    # Custom cycle containing INTAKE -> DISCOVERY -> REQUIREMENTS_PRODUCT
    service_custom = CanonicalLifecycleService(root_path=service.root_path)
    service_custom._cycles_cfg = {
        "cycles": {
            "full": {
                "states": ["intake", "discovery", "blueprint", "scaffolding", "done"]
            }
        }
    }
    item_path = _create_item(work_dir, "DEV-SKIP-DISC", state="intake", cycle="full")

    with pytest.raises(InvalidTransitionError, match="(?i)(salto arbitrário|arbitrary forward jump)"):
        service_custom.transition("DEV-SKIP-DISC", project_id="test-proj", target_stage="blueprint", item_path=item_path)


def test_skip_requirements_rejected(env):
    """Pular Requirements/Product de discovery direto para scaffolding deve ser rejeitado."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-SKIP-REQ", state="discovery", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(salto arbitrário|arbitrary forward jump)"):
        service.transition("DEV-SKIP-REQ", project_id="test-proj", target_stage="scaffolding", item_path=item_path)


def test_skip_planning_rejected(env):
    """Pular Planning quando exigido no fluxo canônico deve ser rejeitado."""
    _, work_dir, _, service = env
    service_custom = CanonicalLifecycleService(root_path=service.root_path)
    service_custom._cycles_cfg = {
        "cycles": {
            "planning-cycle": {
                "states": ["intake", "discovery", "planning", "scaffolding", "done"]
            }
        }
    }
    item_path = _create_item(work_dir, "DEV-SKIP-PLAN", state="intake", cycle="planning-cycle")

    with pytest.raises(InvalidTransitionError):
        service_custom.transition("DEV-SKIP-PLAN", project_id="test-proj", target_stage="planning", item_path=item_path)


def test_skip_architecture_rejected(env):
    """Pular Architecture/Design saltando de blueprint direto para implementation deve ser rejeitado."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-SKIP-ARCH", state="blueprint", cycle="development")
    _add_gate(item_path, "G1-product", "approved")

    with pytest.raises(InvalidTransitionError, match="(?i)(salto arbitrário|arbitrary forward jump)"):
        service.transition("DEV-SKIP-ARCH", project_id="test-proj", target_stage="implementation", item_path=item_path)


def test_skip_readiness_rejected(env):
    """Pular Readiness/Scaffolding saltando de blueprint direto para code-security-review deve ser rejeitado."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-SKIP-READY", state="blueprint", cycle="development")
    _add_gate(item_path, "G1-product", "approved")

    with pytest.raises(InvalidTransitionError, match="(?i)(salto arbitrário|arbitrary forward jump)"):
        service.transition("DEV-SKIP-READY", project_id="test-proj", target_stage="code-security-review", item_path=item_path)


def test_arbitrary_backward_transition_rejected(env):
    """Transição arbitrária para trás (ex: de scaffolding voltando para discovery) é rejeitada."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-BACK-01", state="scaffolding", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(transição arbitrária para trás|arbitrary backward transition)"):
        service.transition("DEV-BACK-01", project_id="test-proj", target_stage="discovery", item_path=item_path)


def test_arbitrary_forward_jump_rejected(env):
    """Salto arbitrário para o futuro (ex: de blueprint direto para done) é rejeitado."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-JUMP-01", state="blueprint", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(salto arbitrário|arbitrary forward jump)"):
        service.transition("DEV-JUMP-01", project_id="test-proj", target_stage="done", item_path=item_path)


def test_done_terminal_state(env):
    """DONE é estado terminal; qualquer tentativa de transição a partir de DONE deve ser rejeitada."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-DONE-TERM", state="done", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(terminal state 'done'|já está no estado terminal)"):
        service.transition("DEV-DONE-TERM", project_id="test-proj", item_path=item_path)


def test_identical_state_handled_explicitly(env):
    """Transição para o mesmo estado atual deve ser explicitamente rejeitada."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-IDEM-01", state="scaffolding", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(idêntico ao estado atual|identical)"):
        service.transition("DEV-IDEM-01", project_id="test-proj", target_stage="scaffolding", item_path=item_path)


def test_unknown_state_rejected(env):
    """Transição para um estado desconhecido deve levantar InvalidTransitionError."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-UNKNOWN-STAGE", state="scaffolding", cycle="development")

    with pytest.raises(InvalidTransitionError, match="(?i)(unknown lifecycle stage|não pertence)"):
        service.transition("DEV-UNKNOWN-STAGE", project_id="test-proj", target_stage="nonexistent_fantasy_state", item_path=item_path)


def test_unknown_cycle_rejected(env):
    """Transição com um ciclo não existente no catálogo deve levantar ConfigurationError."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "DEV-UNKNOWN-CYCLE", state="blueprint", cycle="mythical-cycle")

    with pytest.raises(ConfigurationError, match="(?i)(not defined in configuration|cycle)"):
        service.transition("DEV-UNKNOWN-CYCLE", project_id="test-proj", item_path=item_path)


def test_advance_state_facade_delegates_to_lifecycle_engine(env):
    """Verifica que squad.advance_state() delega estritamente a execução para o CanonicalLifecycleService."""
    _, work_dir, _, service = env
    squad = AgentSquad(root=ROOT, project_name=None, allow_legacy=True)
    item = squad.init_work_item("FEAT-DELEGATE-01", "low", base=work_dir)
    _add_gate(item, "G1-product", "approved")

    # Inicia em blueprint e chama squad.advance_state
    result = squad.advance_state(item)
    assert result["state"] == "scaffolding"
    assert "transition_id" in result
    assert result["canonical_state"] == LifecycleStage.READINESS_SCAFFOLDING.value


def test_initialize_work_item_canonical_entry(env):
    """Verifica que initialize_work_item registra o item em INTAKE com autoridade R4 única e idempotência."""
    _, work_dir, _, service = env

    init_res = service.initialize_work_item(
        work_item_id="FEAT-INIT-001",
        project_id="test-proj",
        kind="feature",
    )
    assert init_res["work_item_id"] == "FEAT-INIT-001"
    assert init_res["current_stage"] == LifecycleStage.INTAKE.value
    assert init_res["stage"] == "INTAKE"
    assert init_res["legacy_stage"] == "intake"
    assert init_res["cycle_id"] == "development"
    assert init_res["was_created"] is True

    # Idempotência: segunda chamada idêntica retorna o mesmo estado com was_created=False
    re_init = service.initialize_work_item(
        work_item_id="FEAT-INIT-001",
        project_id="test-proj",
        kind="feature",
    )
    assert re_init["was_created"] is False
    assert re_init["current_stage"] == "INTAKE"

    # Conflito: tentativa de reinicializar em projeto diferente falha
    with pytest.raises(LifecycleError, match="Conflicting initialization"):
        service.initialize_work_item(
            work_item_id="FEAT-INIT-001",
            project_id="other-proj",
            kind="feature",
        )

    # Conflito: tentativa de reinicializar com ciclo conflitante falha
    with pytest.raises(LifecycleError, match="Conflicting initialization"):
        service.initialize_work_item(
            work_item_id="FEAT-INIT-001",
            project_id="test-proj",
            cycle_id="bugfix",
        )
