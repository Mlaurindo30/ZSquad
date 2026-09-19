"""Testes TDD para CORR-3: despacho real de sdd run, stages.yaml e stage-complete.

Cobre as lacunas do Adendo da auditoria pós-entrega:
1. Despacho gerenciado em sdd run (--dispatch ou default dispatch).
2. Estado persistente da ordem Constitution -> Specify -> Clarify -> Plan -> Tasks -> Analyze -> Implement.
3. Subcomando sdd stage-complete para validar outputs e atualizar stages.yaml + package.json.
4. sdd status reportando o estado de cada estágio da cadeia.
"""

from __future__ import annotations

import json
import sys
import yaml
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError


def _ack(squad: AgentSquad, item: Path, stage: str, result_ref: str) -> None:
    """Simula somente o consumidor persistente; o conteúdo é validado pela etapa."""
    squad.sdd_dispatch_ack(item, stage, "test-consumer", result_ref)


@pytest.fixture
def consumer_project(tmp_path: Path) -> tuple[Path, AgentSquad]:
    """Cria um projeto consumidor temporário com runtime do squad e política ativa."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    squad_cfg = project_root / ".agents_squad" / "config"
    squad_cfg.mkdir(parents=True)
    # Configuração de política SDD obrigatória
    policy = {
        "policy_version": 1,
        "sdd": {
            "required": True,
        },
        "legacy_mode": False,
    }
    (squad_cfg / "sdd-policy.yaml").write_text(json.dumps(policy), encoding="utf-8")
    runtime_root = Path(__file__).resolve().parents[2]
    squad = AgentSquad(root=runtime_root, project_root=project_root)
    # Ativa duravelmente
    squad.sdd_activate()
    return project_root, squad


def test_sdd_init_creates_stages_yaml_matching_schema(consumer_project):
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-001"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-001\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)

    stages_file = item_path / "sdd" / "stages.yaml"
    assert stages_file.is_file(), "sdd_init deve criar sdd/stages.yaml"

    import yaml
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (squad.root / "contracts" / "sdd-stages.schema.json").read_text(encoding="utf-8")
    )
    data = yaml.safe_load(stages_file.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(data)

    assert data["schema_version"] == 1
    assert data["work_id"] == "TEST-STAGES-001"
    for stage_name in (
        "constitution",
        "specify",
        "clarify",
        "plan",
        "tasks",
        "analyze",
        "implement",
    ):
        assert stage_name in data["stages"]
        assert data["stages"][stage_name]["status"] == "pending"


def test_sdd_run_out_of_order_is_refused(consumer_project):
    """Estágio specify não pode ser executado antes de constitution estar completed."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-002"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-002\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)

    with pytest.raises(SquadError, match="SDD_OUT_OF_ORDER.*constitution"):
        squad.sdd_run(item_path, "specify")


def test_sdd_run_queues_and_updates_stage_status(consumer_project):
    """sdd_run persiste a solicitação; só um receipt de execução a despacha."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-003"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-003\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)

    res = squad.sdd_run(item_path, "constitution", dispatch=True)
    assert res["stage"] == "constitution"
    assert res["persona"] == "requirements-analyst"
    assert "dispatch" in res
    assert res["dispatch"]["dispatched"] is False
    assert res["dispatch"]["queued"] is True
    assert "packet" in res["dispatch"]
    assert res["dispatch"]["packet"]["sdd"] is not None

    import yaml
    stages = yaml.safe_load((item_path / "sdd" / "stages.yaml").read_text(encoding="utf-8"))
    assert stages["stages"]["constitution"]["status"] == "queued"
    assert stages["stages"]["constitution"]["dispatched_at"] is not None
    assert stages["stages"]["constitution"]["briefing_sha256"] is not None


def test_sdd_stage_complete_validates_output_and_unlocks_next_stage(consumer_project):
    """sdd_stage_complete valida saída esperada, marca completed e desbloqueia próximo estágio."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-004"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-004\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)
    squad.sdd_run(item_path, "constitution", dispatch=True)

    # Preenche o constitution.md com conteúdo real
    (item_path / "sdd" / "constitution.md").write_text("# Constituição do Projeto\nRegras invioláveis.", encoding="utf-8")
    _ack(squad, item_path, "constitution", "sdd/constitution.md")

    comp = squad.sdd_stage_complete(item_path, "constitution")
    assert comp["status"] == "completed"
    assert comp["stage"] == "constitution"

    # Agora specify está desbloqueado!
    res_spec = squad.sdd_run(item_path, "specify", dispatch=True)
    assert res_spec["stage"] == "specify"
    assert res_spec["dispatch"]["queued"] is True


def test_sdd_status_includes_stages_progress(consumer_project):
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-005"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-005\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)

    status = squad.sdd_status(item_path)
    assert "stages" in status
    assert "current_stage" in status
    assert status["current_stage"] == "constitution"


def test_sdd_stage_complete_refuses_when_not_dispatched(consumer_project):
    """Estágio não pode ser concluído se não foi despachado via sdd_run."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-006"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-006\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)
    (item_path / "sdd" / "constitution.md").write_text("# Constituição\nConteúdo válido aqui.", encoding="utf-8")

    with pytest.raises(SquadError, match="SDD_NOT_DISPATCHED"):
        squad.sdd_stage_complete(item_path, "constitution")


def test_sdd_stage_complete_refuses_empty_clarify_and_tasks(consumer_project):
    """Clarify exige questions preenchido e Tasks exige tasks preenchido."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-007"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-007\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)

    # 1. Constitution
    squad.sdd_run(item_path, "constitution", dispatch=True)
    (item_path / "sdd" / "constitution.md").write_text("# Constituição\nRegras do sistema.", encoding="utf-8")
    _ack(squad, item_path, "constitution", "sdd/constitution.md")
    squad.sdd_stage_complete(item_path, "constitution")

    # 2. Specify
    squad.sdd_run(item_path, "specify", dispatch=True)
    (item_path / "sdd" / "spec.md").write_text("# Especificação\nRequisitos do sistema.", encoding="utf-8")
    pkg = json.loads((item_path / "sdd" / "package.json").read_text(encoding="utf-8"))
    pkg["requirements"] = [{"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "documentation"}]
    (item_path / "sdd" / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    _ack(squad, item_path, "specify", "sdd/spec.md")
    squad.sdd_stage_complete(item_path, "specify")

    # 3. Clarify com questions vazio falha
    squad.sdd_run(item_path, "clarify", dispatch=True)
    _ack(squad, item_path, "clarify", "sdd/clarifications.yaml")
    (item_path / "sdd" / "clarifications.yaml").write_text("questions: []\n", encoding="utf-8")
    with pytest.raises(SquadError, match="SDD_EMPTY_QUESTIONS"):
        squad.sdd_stage_complete(item_path, "clarify")

    # Corrige clarify
    (item_path / "sdd" / "clarifications.yaml").write_text(
        "questions:\n  - id: Q-01\n    requirement_ids: [REQ-001]\n    question: Formato?\n    severity: nonblocking\n    status: resolved\n    answer: Markdown.\n    source: ata\n",
        encoding="utf-8",
    )
    squad.sdd_stage_complete(item_path, "clarify")

    # Aprova G1-product para desbloquear plan
    import hashlib
    def _sh(p): return hashlib.sha256(p.read_bytes()).hexdigest()
    g1_hashes = {
        "spec": _sh(item_path / "sdd" / "spec.md"),
        "clarifications": _sh(item_path / "sdd" / "clarifications.yaml"),
        "constitution": _sh(item_path / "sdd" / "constitution.md"),
    }
    (item_path / "gate-decisions").mkdir(parents=True, exist_ok=True)
    (item_path / "reviews").mkdir(parents=True, exist_ok=True)
    (item_path / "reviews" / "rev.md").write_text("# Rev", encoding="utf-8")
    rec_g1 = {
        "decision_id": f"GD-{item_path.name}-G1-PRODUCT",
        "gate_id": "G1-product",
        "work_item_id": item_path.name,
        "decision": "approved",
        "decider": "product-owner",
        "criteria": [{"name": "criterio", "result": "pass"}],
        "evidence": ["reviews/rev.md"],
        "human_approval": {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
        "policy_version": 1,
        "input_hashes": g1_hashes,
    }
    (item_path / "gate-decisions" / f"GD-{item_path.name}-G1-PRODUCT.yaml").write_text(yaml.safe_dump(rec_g1), encoding="utf-8")

    # 4. Plan
    squad.sdd_run(item_path, "plan", dispatch=True)
    (item_path / "sdd" / "plan.md").write_text("# Plano\nArquitetura detalhada.", encoding="utf-8")
    _ack(squad, item_path, "plan", "sdd/plan.md")
    squad.sdd_stage_complete(item_path, "plan")

    # Aprova G2-design para desbloquear tasks e analyze
    g2_hashes = {
        "plan": _sh(item_path / "sdd" / "plan.md"),
        "constitution": _sh(item_path / "sdd" / "constitution.md"),
    }
    rec_g2 = {
        "decision_id": f"GD-{item_path.name}-G2-DESIGN",
        "gate_id": "G2-design",
        "work_item_id": item_path.name,
        "decision": "approved",
        "decider": "solution-architect",
        "criteria": [{"name": "criterio", "result": "pass"}],
        "evidence": ["reviews/rev.md"],
        "human_approval": {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
        "policy_version": 1,
        "input_hashes": g2_hashes,
    }
    (item_path / "gate-decisions" / f"GD-{item_path.name}-G2-DESIGN.yaml").write_text(yaml.safe_dump(rec_g2), encoding="utf-8")

    # 5. Tasks vazio falha
    squad.sdd_run(item_path, "tasks", dispatch=True)
    _ack(squad, item_path, "tasks", "sdd/tasks.yaml")
    (item_path / "sdd" / "tasks.yaml").write_text("tasks: []\n", encoding="utf-8")
    with pytest.raises(SquadError, match="SDD_EMPTY_TASKS"):
        squad.sdd_stage_complete(item_path, "tasks")

    # Corrige tasks
    (item_path / "sdd" / "tasks.yaml").write_text(
        "tasks:\n  - id: T-01\n    requirement_ids: [REQ-001]\n    acceptance_ids: [AC-001]\n    owner: backend-engineer\n    points: 3\n    depends_on: []\n    paths: [implementation.py]\n    test_ids: [tests/test.py]\n    status: open\n",
        encoding="utf-8",
    )
    squad.sdd_stage_complete(item_path, "tasks")

    # 6. Analyze exige saída material
    squad.sdd_run(item_path, "analyze", dispatch=True)
    _ack(squad, item_path, "analyze", "status.yaml")
    with pytest.raises(SquadError, match="SDD_MISSING_OUTPUT"):
        squad.sdd_stage_complete(item_path, "analyze")

    (item_path / "sdd" / "analysis.md").write_text("# Análise\nConsistência verificada 100%.", encoding="utf-8")
    squad.sdd_stage_complete(item_path, "analyze")

    # Aprova G3-readiness (GT-design-review) para desbloquear implement
    g3_hashes = {
        "tasks": _sh(item_path / "sdd" / "tasks.yaml"),
        "constitution": _sh(item_path / "sdd" / "constitution.md"),
    }
    rec_g3 = {
        "decision_id": f"GD-{item_path.name}-GT-DESIGN-REVIEW",
        "gate_id": "GT-design-review",
        "work_item_id": item_path.name,
        "decision": "approved",
        "decider": "delivery-orchestrator",
        "criteria": [{"name": "criterio", "result": "pass"}],
        "evidence": ["reviews/rev.md"],
        "human_approval": {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
        "policy_version": 1,
        "input_hashes": g3_hashes,
    }
    (item_path / "gate-decisions" / f"GD-{item_path.name}-GT-DESIGN-REVIEW.yaml").write_text(yaml.safe_dump(rec_g3), encoding="utf-8")

    # 7. Implement exige output_refs com arquivos reais
    squad.sdd_run(item_path, "implement", dispatch=True)
    _ack(squad, item_path, "implement", "sdd/tasks.yaml")
    with pytest.raises(SquadError, match="SDD_MISSING_OUTPUT"):
        squad.sdd_stage_complete(item_path, "implement")

    with pytest.raises(SquadError, match="SDD_OUTPUT_NOT_IN_TASKS"):
        squad.sdd_stage_complete(item_path, "implement", output_refs=["arquivo_fantasma.py"])

    # Rejeita caminho absoluto em output_refs
    with pytest.raises(SquadError, match="SDD_OUTPUT_ABSOLUTE_DISALLOWED"):
        squad.sdd_stage_complete(item_path, "implement", output_refs=["C:/temp/outside.py"])

    # Rejeita arquivo fora dos limites do projeto/workspace (via caminho relativo escapando com ..)
    with pytest.raises(SquadError, match="SDD_OUTPUT_TRAVERSAL"):
        squad.sdd_stage_complete(item_path, "implement", output_refs=["../../outside.py"])

    # Cria arquivo real de saída dentro dos limites (pertencente ao item ou planejado em tasks.yaml)
    code_out = item_path / "implementation.py"
    code_out.write_text("print('implementado')", encoding="utf-8")
    res_impl = squad.sdd_stage_complete(item_path, "implement", output_refs=["implementation.py"])
    assert res_impl["status"] == "completed"
    assert "implementation.py" in res_impl["output_hashes"]
    assert len(res_impl["output_hashes"]["implementation.py"]) == 64


def test_sdd_stage_complete_refuses_stale_briefing(consumer_project):
    """Se o briefing for alterado após o despacho, stage_complete deve levantar SDD_STALE_BRIEFING."""
    project_root, squad = consumer_project
    item_path = project_root / "work" / "TEST-STAGES-008"
    item_path.mkdir(parents=True)
    (item_path / "status.yaml").write_text(
        "id: TEST-STAGES-008\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item_path)
    squad.sdd_run(item_path, "constitution", dispatch=True)
    _ack(squad, item_path, "constitution", "status.yaml")

    # Adultera o hash do briefing registrado no stages.yaml para simular briefing desatualizado
    stages_file = item_path / "sdd" / "stages.yaml"
    stages_data = yaml.safe_load(stages_file.read_text(encoding="utf-8"))
    stages_data["stages"]["constitution"]["briefing_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
    stages_file.write_text(yaml.safe_dump(stages_data, sort_keys=False), encoding="utf-8")

    (item_path / "sdd" / "constitution.md").write_text("# Constituição\nRegras invioláveis do sistema.", encoding="utf-8")
    with pytest.raises(SquadError, match="SDD_STALE_BRIEFING"):
        squad.sdd_stage_complete(item_path, "constitution")
