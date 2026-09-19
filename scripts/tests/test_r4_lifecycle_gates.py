"""Canonical Lifecycle Gates test suite (R4 - Seção 48).

Covers:
- G5 em INTAKE rejeitado
- G6 em IMPLEMENTATION rejeitado
- G2 não pode ser silenciosamente pulado quando exigido
- gate correto na fronteira correta aceito
- gate reprovado/failed bloqueia transição
- gate blocked bloqueia transição
- decisão de gate sozinha não muta lifecycle
- evento de gate não pode burlar o engine
- mapeamento de gates tem fonte única de verdade
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import pytest
import yaml

from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle import (
    GATE_TO_STAGE_MAP,
    STAGE_TO_GATE_MAP,
    CanonicalLifecycleService,
    GateNotEligibleError,
    GateNotPassedError,
    assert_gate_eligibility,
    is_gate_eligible,
    verify_gate_approval,
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
        "criteria": [{"name": "spec-valid", "result": "pass" if decision == "approved" else "fail"}],
        "evidence": ["doc.md"],
    }
    g_file.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_g5_in_intake_rejected():
    """G5-quality avaliado em INTAKE deve ser rejeitado com GateNotEligibleError."""
    assert not is_gate_eligible(GateId.G5_QUALITY, LifecycleStage.INTAKE)
    with pytest.raises(GateNotEligibleError, match="not eligible for state"):
        assert_gate_eligibility(GateId.G5_QUALITY, LifecycleStage.INTAKE)
    with pytest.raises(GateNotEligibleError, match="not eligible for state"):
        assert_gate_eligibility("G5-quality", "intake")


def test_g6_in_implementation_rejected():
    """G6-governance-release avaliado em IMPLEMENTATION deve ser rejeitado com GateNotEligibleError."""
    assert not is_gate_eligible(GateId.G6_GOVERNANCE_RELEASE, LifecycleStage.IMPLEMENTATION)
    with pytest.raises(GateNotEligibleError, match="not eligible for state"):
        assert_gate_eligibility(GateId.G6_GOVERNANCE_RELEASE, LifecycleStage.IMPLEMENTATION)
    with pytest.raises(GateNotEligibleError, match="not eligible for state"):
        assert_gate_eligibility("G6-governance-release", "implementation")


def test_g2_cannot_be_silently_skipped_when_required(env):
    """Para itens com risco medium/high/critical, avançar de blueprint para scaffolding exige G2-design."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-RISK-MED", state="blueprint", risk="medium")

    # Apenas G1-product aprovado
    _add_gate(item_path, "G1-product", "approved")

    # Transição deve falhar sem G2-design
    with pytest.raises(GateNotPassedError, match="(?i)g2-design"):
        service.transition("FEAT-RISK-MED", project_id="test-proj", item_path=item_path)

    # Aprovando G2-design, avanço é autorizado
    _add_gate(item_path, "G2-design", "approved")
    res = service.transition("FEAT-RISK-MED", project_id="test-proj", item_path=item_path)
    assert res["state"] == "scaffolding"


def test_correct_gate_at_correct_boundary_accepted():
    """Gates avaliados em seus estágios canônicos corretos são aceitos."""
    # G1 em REQUIREMENTS_PRODUCT / blueprint
    assert is_gate_eligible(GateId.G1_PRODUCT, LifecycleStage.REQUIREMENTS_PRODUCT)
    assert is_gate_eligible("G1-product", "blueprint")

    # G3 em READINESS_SCAFFOLDING / scaffolding
    assert is_gate_eligible(GateId.G3_READINESS, LifecycleStage.READINESS_SCAFFOLDING)
    assert is_gate_eligible("G3-readiness", "scaffolding")

    # G4 em SECURITY_REVIEW / code-security-review
    assert is_gate_eligible(GateId.G4_CODE_SECURITY, LifecycleStage.SECURITY_REVIEW)

    # G5 em QA_VALIDATION / quality-validation
    assert is_gate_eligible(GateId.G5_QUALITY, LifecycleStage.QA_VALIDATION)

    # G6 em GOVERNANCE_RELEASE / governance-release
    assert is_gate_eligible(GateId.G6_GOVERNANCE_RELEASE, LifecycleStage.GOVERNANCE_RELEASE)


def test_failed_gate_blocks_transition(env):
    """Gate com decisão 'rejected' ou 'fail' bloqueia a transição."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-GATE-REJECT", state="blueprint", risk="low")

    # Gate G1 reprovado
    _add_gate(item_path, "G1-product", "rejected")

    with pytest.raises(GateNotPassedError, match="(?i)(exige a aprovação do gate 'g1-product'|nenhuma decisão com status 'approved')"):
        service.transition("FEAT-GATE-REJECT", project_id="test-proj", item_path=item_path)


def test_blocked_gate_blocks_transition(env):
    """Gate com decisão 'blocked' bloqueia a transição."""
    _, work_dir, _, service = env
    item_path = _create_item(work_dir, "FEAT-GATE-BLOCKED", state="blueprint", risk="low")

    _add_gate(item_path, "G1-product", "blocked")

    with pytest.raises(GateNotPassedError, match="(?i)(exige a aprovação do gate 'g1-product'|nenhuma decisão com status 'approved')"):
        service.transition("FEAT-GATE-BLOCKED", project_id="test-proj", item_path=item_path)


def test_gate_decision_alone_does_not_mutate_lifecycle(env):
    """Gravar decisão de gate no disco ou banco NÃO muta o estado do lifecycle sozinho."""
    _, work_dir, db_path, _ = env
    item_path = _create_item(work_dir, "FEAT-GATE-MUT", state="blueprint", risk="low")

    # Grava decisão aprovada
    _add_gate(item_path, "G1-product", "approved")

    # Verifica status.yaml: ainda deve estar em blueprint!
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "blueprint"

    # Verifica SQLite se houver registro: ainda deve estar em blueprint
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_stage FROM work_item_lifecycle_state WHERE work_item_id = ?", ("FEAT-GATE-MUT",))
        row = cursor.fetchone()
        assert row is None or row[0] in {"REQUIREMENTS_PRODUCT", "blueprint"}


def test_gate_event_alone_cannot_bypass_engine(env):
    """Emissão de evento de gate (ex: gate.decided) não transiciona estado sem o LifecycleEngine."""
    _, work_dir, db_path, service = env
    item_path = _create_item(work_dir, "FEAT-GATE-EVT", state="blueprint", risk="low")

    from scripts.domain.events import DomainEvent
    event = DomainEvent(
        event_id="EVT-GATE-DECIDED-001",
        event_type="agent_squad.gate.decided",
        work_item_id="FEAT-GATE-EVT",
        project_id="test-proj",
        source="external_test",
        correlation_id="CORR-01",
        causation_id="CAUSE-01",
        idempotency_key="IDEM-001",
        payload={"gate_id": "G1-product", "status": "approved"},
    )
    event_store = SqliteEventStore(db_path)
    event_store.save_event(event)

    # Estado permanece inalterado
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "blueprint"


def test_gate_mapping_single_source_of_truth():
    """Valida que GATE_TO_STAGE_MAP e STAGE_TO_GATE_MAP são inversos e únicos."""
    for gate, stage in GATE_TO_STAGE_MAP.items():
        assert isinstance(gate, GateId)
        assert isinstance(stage, LifecycleStage)
        assert STAGE_TO_GATE_MAP[stage] == gate
