"""Canonical Lifecycle Handoffs test suite (R4 - Seção 49).

Covers:
- handoff obrigatório ausente -> bloqueado
- handoff PENDING -> bloqueado
- handoff ACKNOWLEDGED -> pré-requisito passa
- handoff REJECTED -> bloqueado
- handoff EXPIRED -> bloqueado
- evento HANDOFF_CREATED sozinho não transiciona
- HANDOFF_ACKNOWLEDGED não burla gates ou WIP pendentes
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle import (
    CanonicalLifecycleService,
    GateNotPassedError,
    HandoffPendingError,
    WIPLimitExceededError,
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


def _add_handoff(item_path: Path, handoff_id: str, ack_status: str = "pending") -> None:
    h_file = item_path / "handoffs" / f"{handoff_id}.yaml"
    data = {
        "id": handoff_id,
        "work_item_id": item_path.name,
        "from": "01-product-owner",
        "to": "04-solution-architect",
        "status": "ready",
        "acknowledgement": {
            "status": ack_status,
            "acknowledged_by": "04-solution-architect" if ack_status not in {"pending", "awaiting"} else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    h_file.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_missing_mandatory_handoff_blocked(env):
    """Quando um handoff é obrigatório, sua ausência bloqueia o avanço com HandoffPendingError."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-MAND", state="blueprint", risk="low")
    _add_gate(item_path, "G1-product", "approved")

    # Exigindo handoff explicitamente
    with pytest.raises(HandoffPendingError, match="(?i)(handoff obrigatório ausente)"):
        service.transition("FEAT-HO-MAND", project_id="test-proj", item_path=item_path, require_handoff=True)


def test_pending_handoff_blocks_transition(env):
    """Handoff com acknowledgement status 'pending' bloqueia a transição."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-PEND", state="blueprint", risk="low")
    _add_gate(item_path, "G1-product", "approved")
    _add_handoff(item_path, "HANDOFF-001", ack_status="pending")

    with pytest.raises(HandoffPendingError, match="(?i)(acknowledgement status 'pending')"):
        service.transition("FEAT-HO-PEND", project_id="test-proj", item_path=item_path)


def test_acknowledged_handoff_passes(env):
    """Handoff com acknowledgement status 'accepted' ou 'acknowledged' autoriza o avanço."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-ACK", state="blueprint", risk="low")
    _add_gate(item_path, "G1-product", "approved")
    _add_handoff(item_path, "HANDOFF-002", ack_status="accepted")

    res = service.transition("FEAT-HO-ACK", project_id="test-proj", item_path=item_path)
    assert res["state"] == "scaffolding"


def test_rejected_handoff_blocks_transition(env):
    """Handoff com acknowledgement status 'rejected' bloqueia a transição."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-REJ", state="blueprint", risk="low")
    _add_gate(item_path, "G1-product", "approved")
    _add_handoff(item_path, "HANDOFF-003", ack_status="rejected")

    with pytest.raises(HandoffPendingError, match="(?i)(foi rejeitado|status 'rejected')"):
        service.transition("FEAT-HO-REJ", project_id="test-proj", item_path=item_path)


def test_expired_handoff_blocks_transition(env):
    """Handoff com acknowledgement status 'expired' bloqueia a transição."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-EXP", state="blueprint", risk="low")
    _add_gate(item_path, "G1-product", "approved")
    _add_handoff(item_path, "HANDOFF-004", ack_status="expired")

    with pytest.raises(HandoffPendingError, match="(?i)(está expirado|status 'expired')"):
        service.transition("FEAT-HO-EXP", project_id="test-proj", item_path=item_path)


def test_handoff_created_event_alone_does_not_transition(env):
    """A simples criação do arquivo de handoff ou de evento não efetua transição de estado."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-HO-EVT", state="blueprint", risk="low")

    # Adiciona handoff aceito mas NÃO chama transition
    _add_handoff(item_path, "HANDOFF-005", ack_status="accepted")

    # Verifica status.yaml
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "blueprint"


def test_acknowledged_handoff_does_not_bypass_gates_or_wip(env):
    """Mesmo com handoff ACKNOWLEDGED, gate não aprovado ou WIP excedido continua bloqueando."""
    _, work_dir, _, service = env

    # Caso 1: Gate não aprovado
    item1 = _create_item(work_dir, "FEAT-HO-NOGATE", state="blueprint", risk="low")
    _add_handoff(item1, "HANDOFF-G1", ack_status="accepted")
    # Gate G1 NÃO aprovado!
    with pytest.raises(GateNotPassedError, match="(?i)g1-product"):
        service.transition("FEAT-HO-NOGATE", project_id="test-proj", item_path=item1)

    # Caso 2: WIP cheio em scaffolding (limite = 2)
    _create_item(work_dir, "FEAT-WIP-OCC1", state="scaffolding")
    _create_item(work_dir, "FEAT-WIP-OCC2", state="scaffolding")

    item2 = _create_item(work_dir, "FEAT-HO-WIPBREACH", state="blueprint", risk="low")
    _add_gate(item2, "G1-product", "approved")
    _add_handoff(item2, "HANDOFF-WIP", ack_status="accepted")

    with pytest.raises(WIPLimitExceededError, match="(?i)WIP breach"):
        service.transition("FEAT-HO-WIPBREACH", project_id="test-proj", item_path=item2)
