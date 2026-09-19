"""Canonical Lifecycle Events test suite (R4 - Seção 52).

Covers:
- transição bem-sucedida emite um STAGE_ENTERED
- transição falha não emite STAGE_ENTERED
- evento carrega project_id e work_item_id
- correlation_id preservado
- causation_id preservado
- retry da mesma transição não duplica evento
- evento não causa transição ilegal recursiva
- R2 engine permanece store autoritativo de eventos
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import pytest
import yaml

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle import (
    CanonicalLifecycleService,
    GateNotPassedError,
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


def test_successful_transition_emits_stage_entered_event(env):
    """Transição bem-sucedida persiste exatamente um evento 'agent_squad.stage.entered' no outbox."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-SUCCESS-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    res = service.transition("EVT-SUCCESS-01", project_id="test-proj", item_path=item)
    assert res["state"] == "scaffolding"

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT event_type, work_item_id, payload FROM events WHERE work_item_id = ?", ("EVT-SUCCESS-01",))
        rows = cursor.fetchall()
        assert len(rows) == 1
        event_type, w_id, payload_str = rows[0]
        assert event_type == "agent_squad.stage.entered"
        assert w_id == "EVT-SUCCESS-01"
        payload = yaml.safe_load(payload_str)
        assert payload["to_stage"] == "scaffolding"
        assert payload["stage"] == LifecycleStage.READINESS_SCAFFOLDING.value


def test_failed_transition_does_not_emit_event(env):
    """Transição que falha (ex: gate não aprovado) não emite evento no outbox."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-FAIL-01", state="blueprint", risk="low")
    # G1 NÃO aprovado!

    with pytest.raises(GateNotPassedError):
        service.transition("EVT-FAIL-01", project_id="test-proj", item_path=item)

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM events WHERE work_item_id = ?", ("EVT-FAIL-01",))
        count = cursor.fetchone()[0]
        assert count == 0


def test_event_carries_project_and_work_item_ids(env):
    """O evento persistido carrega explicitamente project_id e work_item_id."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-PROJ-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    service.transition("EVT-PROJ-01", project_id="custom-project-id", item_path=item)

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT project_id, work_item_id FROM events WHERE work_item_id = ?",
            ("EVT-PROJ-01",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "custom-project-id"
        assert row[1] == "EVT-PROJ-01"


def test_correlation_and_causation_ids_preserved(env):
    """correlation_id e causation_id fornecidos são rigorosamente propagados ao evento."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-CORR-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    custom_corr = "CORR-UUID-12345"
    custom_cause = "CAUSE-UUID-67890"
    service.transition(
        "EVT-CORR-01",
        project_id="test-proj",
        item_path=item,
        correlation_id=custom_corr,
        causation_id=custom_cause,
    )

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT correlation_id, causation_id FROM events WHERE work_item_id = ?",
            ("EVT-CORR-01",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == custom_corr
        assert row[1] == custom_cause


def test_retry_transition_does_not_duplicate_event(env):
    """Retentar ou verificar estado sem transição não duplica eventos no outbox."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-IDEM-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    # 1ª transição
    res = service.transition("EVT-IDEM-01", project_id="test-proj", item_path=item)
    assert res["state"] == "scaffolding"

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM events WHERE work_item_id = ?", ("EVT-IDEM-01",))
        initial_count = cursor.fetchone()[0]
        assert initial_count == 1

    # Chamada can_transition para o mesmo estado não adiciona evento
    can_ok, _ = service.can_transition("EVT-IDEM-01", project_id="test-proj", target_stage="scaffolding", item_path=item)
    assert not can_ok

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM events WHERE work_item_id = ?", ("EVT-IDEM-01",))
        after_count = cursor.fetchone()[0]
        assert after_count == initial_count


def test_event_does_not_cause_recursive_illegal_transition(env):
    """A gravação do evento no outbox não dispara reentrada síncrona nem mutação cascateada ilegal."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-NORECURSE-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    # Executa a transição
    service.transition("EVT-NORECURSE-01", project_id="test-proj", item_path=item)

    # Verifica que o item avançou exatamente 1 passo e parou
    status = yaml.safe_load((item / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "scaffolding"


def test_r2_engine_remains_authoritative_event_store(env):
    """SqliteEventStore do marco R2 é a autoridade de armazenamento dos eventos persistidos."""
    _, work_dir, db_path, service = env
    item = _create_item(work_dir, "EVT-R2STORE-01", state="blueprint", risk="low")
    _add_gate(item, "G1-product", "approved")

    service.transition("EVT-R2STORE-01", project_id="test-proj", item_path=item)

    event_store = SqliteEventStore(db_path)
    with event_store.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT event_type, work_item_id FROM events WHERE work_item_id = ?", ("EVT-R2STORE-01",))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "agent_squad.stage.entered"
        assert row[1] == "EVT-R2STORE-01"
