"""Suíte de testes para o Continuous Trigger Engine (TDD Red-Green-Refactor)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import pytest
import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad, SquadError, write_yaml, read_yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def squad_env():
    """Fixture com ambiente isolado do AgentSquad para testes."""
    work_dir = ROOT / "work" / "test-continuous-engine"
    lock_dir = ROOT / ".locks" / "test-continuous-engine"

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name="test-continuous-engine")
    yield squad
    cleanup()


def test_engine_event_dataclass_serialization():
    """Testa criação, conversão para dicionário e desserialização de EngineEvent."""
    from continuous_trigger_engine import EngineEvent

    event = EngineEvent(
        event_id="evt-123",
        event_type="EVENT_HANDOFF_CREATED",
        work_item_id="US-TEST-01",
        payload={"from": "solution-architect", "to": "software-engineer"},
        timestamp="2026-09-15T12:00:00Z",
    )
    as_dict = event.to_dict()
    assert as_dict["event_id"] == "evt-123"
    assert as_dict["event_type"] == "EVENT_HANDOFF_CREATED"
    assert as_dict["work_item_id"] == "US-TEST-01"

    restored = EngineEvent.from_dict(as_dict)
    assert restored.event_id == event.event_id
    assert restored.event_type == event.event_type
    assert restored.work_item_id == event.work_item_id
    assert restored.payload == event.payload
    assert restored.timestamp == event.timestamp


def test_circuit_breaker_state_transitions():
    """Testa transições de estado do CircuitBreakerState com threshold de 2 falhas."""
    from continuous_trigger_engine import CircuitBreakerState

    cb = CircuitBreakerState(work_item_id="US-TEST-01", max_retries=2)
    assert cb.state == "CLOSED"
    assert not cb.is_open()
    assert cb.failure_count == 0

    # 1ª falha: continua CLOSED
    tripped = cb.record_failure("Erro temporário 1")
    assert not tripped
    assert cb.state == "CLOSED"
    assert not cb.is_open()
    assert cb.failure_count == 1
    assert cb.last_failure_reason == "Erro temporário 1"

    # 2ª falha consecutiva: atinge max_retries=2 e desarma para OPEN
    tripped = cb.record_failure("Erro fatal 2")
    assert tripped
    assert cb.state == "OPEN"
    assert cb.is_open()
    assert cb.failure_count == 2
    assert cb.last_failure_reason == "Erro fatal 2"

    # Reset restaura para CLOSED
    cb.reset()
    assert cb.state == "CLOSED"
    assert not cb.is_open()
    assert cb.failure_count == 0


def test_agile_coach_sizing_guard():
    """Testa proteção cognitiva de Story Points (> 8 pontos bloqueia com BLOCKED_SIZING_EXCEEDED)."""
    from continuous_trigger_engine import AgileCoachSizingGuard

    guard = AgileCoachSizingGuard()

    # Cenário 1: Story points <= 8 passa
    status_ok = {"id": "US-OK", "story_points": 5}
    res_ok = guard.validate_sizing(status_ok)
    assert res_ok["allowed"] is True
    assert res_ok["status"] == "SIZING_ALLOWED"

    # Cenário 2: Story points ausente ou 0 passa
    status_default = {"id": "US-DEF"}
    res_default = guard.validate_sizing(status_default)
    assert res_default["allowed"] is True

    # Cenário 3: Story points = 13 excede limite de 8
    status_exceeded = {"id": "US-OVERSIZED", "story_points": 13}
    res_exceeded = guard.validate_sizing(status_exceeded)
    assert res_exceeded["allowed"] is False
    assert res_exceeded["status"] == "BLOCKED_SIZING_EXCEEDED"
    assert "agile-coach" in res_exceeded["action_required"].lower()


def test_po_injection_guard_clearance(squad_env):
    """Testa POInjectionGuard: itens com risco medium/high/critical exigem G1 aprovado com human approval."""
    from continuous_trigger_engine import POInjectionGuard

    guard = POInjectionGuard()
    work_id = "US-TEST-PO-01"
    item_path = squad_env.init_work_item(work_id, "medium")

    # Sem portão G1 aprovado: bloqueia com AWAITING_PO_APPROVAL
    res = guard.validate_g1_clearance(squad_env, work_id)
    assert res["allowed"] is False
    assert res["status"] == "AWAITING_PO_APPROVAL"

    # Cria decisão G1-product mas sem human_approval: ainda bloqueia
    g1_file = item_path / "gate-decisions" / "GD-G1-PRODUCT.yaml"
    g1_data = {
        "decision_id": "GD-G1-PRODUCT",
        "gate_id": "G1-product",
        "work_item_id": work_id,
        "decision": "approved",
        "decider": "product-owner",
        "criteria": [{"name": "blueprint-complete", "result": "pass"}],
        "evidence": ["blueprint.md"],
        "human_approval": {
            "required": True,
            "status": "not_required",
            "approved_by": None,
            "evidence": None,
        },
        "decided_at": "2026-09-15T12:00:00Z",
    }
    write_yaml(g1_file, g1_data)

    res2 = guard.validate_g1_clearance(squad_env, work_id)
    assert res2["allowed"] is False
    assert res2["status"] == "AWAITING_PO_APPROVAL"

    # Atualiza human_approval para approved: agora passa
    g1_data["human_approval"]["status"] = "approved"
    g1_data["human_approval"]["approved_by"] = "miche"
    g1_data["human_approval"]["evidence"] = "documentation/HUMAN-APPROVAL.md"
    write_yaml(g1_file, g1_data)

    res3 = guard.validate_g1_clearance(squad_env, work_id)
    assert res3["allowed"] is True
    assert res3["status"] == "G1_APPROVED"


def test_po_injection_guard_low_risk_bypass(squad_env):
    """Testa que para risco low, POInjectionGuard permite avanço sem aprovação humana obrigatória em G1."""
    from continuous_trigger_engine import POInjectionGuard

    guard = POInjectionGuard()
    work_id = "US-TEST-LOW-01"
    squad_env.init_work_item(work_id, "low")

    res = guard.validate_g1_clearance(squad_env, work_id)
    assert res["allowed"] is True
    assert res["status"] == "LOW_RISK_BYPASS"


def test_continuous_engine_dispatch_and_advance(squad_env):
    """Testa ContinuousTriggerEngine avançando estado de forma reativa a partir de evento de handoff."""
    from continuous_trigger_engine import ContinuousTriggerEngine, EngineEvent

    engine = ContinuousTriggerEngine(squad_env, max_retries=2)
    work_id = "US-TEST-ADV-01"
    item_path = squad_env.init_work_item(work_id, "low")

    # Injeta aprovação de G1 para permitir avanço de blueprint para scaffolding
    g1_file = item_path / "gate-decisions" / "GD-G1-PRODUCT.yaml"
    write_yaml(
        g1_file,
        {
            "decision_id": "GD-G1-PRODUCT",
            "gate_id": "G1-product",
            "work_item_id": work_id,
            "decision": "approved",
            "decider": "product-owner",
            "criteria": [{"name": "blueprint-complete", "result": "pass"}],
            "evidence": ["blueprint.md"],
            "human_approval": {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
            "decided_at": "2026-09-15T12:00:00Z",
        },
    )

    # Cria handoff de blueprint
    (item_path / "blueprint.md").write_text("# Blueprint", encoding="utf-8")
    evidence_dir = item_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "test.log").write_text("ok", encoding="utf-8")
    handoff = squad_env.create_handoff(
        item=item_path,
        sender="solution-architect",
        recipient="delivery-orchestrator",
        summary="Blueprint ready",
        artifacts=["blueprint.md"],
        evidence=["evidence/test.log"],
        memory_delta="MEM-001",
        next_gate="G1-product",
    )

    # Dispara evento handle_handoff_created
    event = EngineEvent(
        event_id="evt-001",
        event_type="EVENT_HANDOFF_CREATED",
        work_item_id=work_id,
        payload={"handoff_id": handoff["id"], "to": "delivery-orchestrator"},
        timestamp="2026-09-15T12:00:00Z",
    )

    result = engine.dispatch(event)
    assert result["status"] == "STATE_ADVANCED"
    assert result["new_state"] == "scaffolding"

    # Confere no status.yaml
    status = read_yaml(item_path / "status.yaml")
    assert status["state"] == "scaffolding"


def test_continuous_engine_circuit_breaker_trip(squad_env):
    """Testa que 2 falhas consecutivas de execução abrem o Circuit Breaker e interrompem o motor contínuo."""
    from continuous_trigger_engine import ContinuousTriggerEngine

    engine = ContinuousTriggerEngine(squad_env, max_retries=2)
    work_id = "US-TEST-FAIL-01"
    item_path = squad_env.init_work_item(work_id, "low")

    # Tenta rodar contínuo quando não há condições de avançar (ex: gate não aprovado)
    # Deve falhar e incrementar o circuit breaker
    res1 = engine.run_continuous(work_id, max_steps=1)
    assert res1["status"] in {"FAILED", "CIRCUIT_BREAKER_RECORDED", "STEP_FAILED"}
    assert engine.get_circuit_breaker(work_id).failure_count == 1
    assert not engine.get_circuit_breaker(work_id).is_open()

    # Segunda tentativa com falha: deve abrir o circuit breaker
    res2 = engine.run_continuous(work_id, max_steps=1)
    assert res2["status"] == "HALTED_CIRCUIT_BREAKER"
    assert engine.get_circuit_breaker(work_id).is_open()

    # Tentativa subsequente com circuit breaker aberto deve recusar imediatamente
    res3 = engine.run_continuous(work_id, max_steps=1)
    assert res3["status"] == "HALTED_CIRCUIT_BREAKER"
    assert "aberto" in res3["message"].lower() or "open" in res3["message"].lower()


def test_continuous_engine_sizing_guard_blocks_run(squad_env):
    """Testa bloqueio por AgileCoachSizingGuard durante run_continuous com story_points > 8."""
    from continuous_trigger_engine import ContinuousTriggerEngine

    engine = ContinuousTriggerEngine(squad_env, max_retries=2)
    work_id = "US-TEST-SIZING-01"
    item_path = squad_env.init_work_item(work_id, "low")

    status = read_yaml(item_path / "status.yaml")
    status["story_points"] = 13
    write_yaml(item_path / "status.yaml", status)

    res = engine.run_continuous(work_id, max_steps=5)
    assert res["status"] == "BLOCKED_SIZING_EXCEEDED"
    assert "agile-coach" in res["message"].lower()


def test_continuous_engine_po_guard_blocks_run(squad_env):
    """Testa bloqueio por POInjectionGuard durante run_continuous para item medium sem G1 aprovado."""
    from continuous_trigger_engine import ContinuousTriggerEngine

    engine = ContinuousTriggerEngine(squad_env, max_retries=2)
    work_id = "US-TEST-POGUARD-01"
    squad_env.init_work_item(work_id, "medium")

    res = engine.run_continuous(work_id, max_steps=5)
    assert res["status"] == "AWAITING_PO_APPROVAL"
    assert "product-owner" in res["message"].lower() or "g1" in res["message"].lower()


def test_continuous_engine_idempotency_on_done(squad_env):
    """Testa idempotência quando o work item já se encontra no estado 'done'."""
    from continuous_trigger_engine import ContinuousTriggerEngine

    engine = ContinuousTriggerEngine(squad_env, max_retries=2)
    work_id = "US-TEST-DONE-01"
    item_path = squad_env.init_work_item(work_id, "low")

    status = read_yaml(item_path / "status.yaml")
    status["state"] = "done"
    write_yaml(item_path / "status.yaml", status)

    res = engine.run_continuous(work_id, max_steps=5)
    assert res["status"] in {"ALREADY_DONE", "COMPLETED_IDEMPOTENT"}
    assert res["steps_executed"] == 0


def test_create_handoff_emits_continuous_event(squad_env):
    """Testa que create_handoff dispara EVENT_HANDOFF_CREATED registrado em events.jsonl."""
    work_id = "US-TEST-HO-EVENT-01"
    item_path = squad_env.init_work_item(work_id, "low")

    (item_path / "blueprint.md").write_text("# Blueprint", encoding="utf-8")
    ev_dir = item_path / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "ev.log").write_text("ev", encoding="utf-8")

    squad_env.create_handoff(
        item=item_path,
        sender="solution-architect",
        recipient="delivery-orchestrator",
        summary="Handoff com trigger",
        artifacts=["blueprint.md"],
        evidence=["evidence/ev.log"],
        memory_delta="MEM-002",
        next_gate="G1-product",
    )

    events_file = item_path / "events" / "events.jsonl"
    assert events_file.is_file()
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    last_event = json.loads(lines[-1])
    assert last_event["event_type"] == "EVENT_HANDOFF_CREATED"
    assert last_event["work_item_id"] == work_id


def test_cli_run_continuous(squad_env, capsys):
    """Testa invocação do subcomando run-continuous via CLI agent_squad."""
    from agent_squad import main

    work_id = "US-TEST-CLI-01"
    item_path = squad_env.init_work_item(work_id, "low")

    status = read_yaml(item_path / "status.yaml")
    status["state"] = "done"
    write_yaml(item_path / "status.yaml", status)

    ret = main(["--project-name", "test-continuous-engine", "run-continuous", "--work-item", work_id])
    assert ret == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] in {"ALREADY_DONE", "COMPLETED_IDEMPOTENT"}


def test_schema_continuous_trigger_validation():
    """Valida que o schema JSON contracts/continuous-trigger.schema.json valida evento e circuit breaker."""
    from jsonschema import Draft202012Validator
    from continuous_trigger_engine import EngineEvent, CircuitBreakerState

    schema_file = ROOT / "contracts" / "continuous-trigger.schema.json"
    assert schema_file.is_file()
    schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema_data)

    # Valida evento
    evt = EngineEvent(
        event_id="evt-999",
        event_type="EVENT_STATE_ADVANCED",
        work_item_id="US-TEST-99",
        payload={"from": "blueprint", "to": "scaffolding"},
        timestamp="2026-09-15T12:00:00Z",
    )
    assert validator.is_valid(evt.to_dict())

    # Valida circuit breaker state
    cb = CircuitBreakerState(
        work_item_id="US-TEST-99",
        failure_count=1,
        max_retries=2,
        state="CLOSED",
        last_failure_reason="Tentativa 1 falhou",
    )
    assert validator.is_valid(cb.to_dict())
