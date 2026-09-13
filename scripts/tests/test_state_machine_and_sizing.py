import json
import shutil
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad, SquadError, main as squad_main
from gate_validators import validate_G1_product, validate_G3_readiness, validate_G6_governance_release

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def squad_env():
    """Cria um squad isolado apontando para o runtime real com limpeza e isolamento."""
    work_dir = ROOT / "work" / "test-frente2"
    lock_dir = ROOT / ".locks" / "test-frente2"

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name="test-frente2")
    yield squad
    cleanup()


def test_check_sizing_valid_fibonacci(squad_env):
    """Valida que números de Fibonacci permitidos (1, 2, 3, 5, 8) são aprovados."""
    for pts in [1, 2, 3, 5, 8]:
        res = squad_env.check_sizing(points=pts)
        assert res["status"] == "APPROVED"
        assert res["story_points"] == pts


def test_check_sizing_blocks_above_8(squad_env):
    """Valida bloqueio formal para Story Points > 8 exigindo split pelo 40-agile-coach."""
    for pts in [13, 21, 34]:
        with pytest.raises(SquadError) as exc:
            squad_env.check_sizing(points=pts)
        assert "Bloqueio de proteção cognitiva" in str(exc.value)
        assert "40-agile-coach" in str(exc.value)


def test_check_sizing_invalid_points(squad_env):
    """Valida rejeição de valores fora da escala Fibonacci governada."""
    for pts in [0, 4, 6, 7, 9, 10]:
        with pytest.raises(SquadError) as exc:
            squad_env.check_sizing(points=pts)
        assert "Story Points inválido" in str(exc.value) or "Bloqueio de proteção cognitiva" in str(exc.value)


def test_advance_state_blocked_by_missing_gate(squad_env):
    """Valida que avanço de estado a partir de blueprint é bloqueado sem gate G1 aprovado."""
    work_id = "US-TEST-STATE-01"
    item_path = squad_env.init_work_item(work_id, "low")
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "blueprint"

    # Tentativa de avanço sem decisão de gate em gate-decisions/
    with pytest.raises(SquadError) as exc:
        squad_env.advance_state(item_path)
    assert "Avanço de estado bloqueado" in str(exc.value)
    assert "G1-product" in str(exc.value)


def test_advance_state_success_with_approved_gate(squad_env):
    """Valida avanço determinístico de estado quando a decisão de gate está aprovada."""
    work_id = "US-TEST-STATE-02"
    item_path = squad_env.init_work_item(work_id, "low")

    # Cria uma decisão aprovada para G1-product
    decisions_dir = item_path / "gate-decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    g1_decision = {
        "decision_id": f"GD-{work_id}-G1-PRODUCT",
        "gate_id": "G1-product",
        "work_item_id": work_id,
        "decision": "approved",
        "decider": "product-owner",
        "criteria": [{"name": "blueprint-complete", "result": "pass"}],
        "evidence": ["discovery/brief.md"],
        "human_approval": {"required": False, "status": "not_required"},
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-03T12:00:00Z",
    }
    (decisions_dir / f"GD-{work_id}-G1-PRODUCT.yaml").write_text(
        yaml.safe_dump(g1_decision), encoding="utf-8"
    )

    # Avança o estado
    result = squad_env.advance_state(item_path)
    assert result["previous_state"] == "blueprint"
    assert result["state"] == "scaffolding"
    assert "delivery-orchestrator" in result["responsible_agents"]
    assert "scrum-master" in result["responsible_agents"]

    # Verifica status.yaml persistido
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "scaffolding"
    assert status["owner"] == "delivery-orchestrator"
    assert "scrum-master" in status["active_agents"]


def test_advance_state_terminal_done_blocks(squad_env):
    """Valida que tentar avançar além do estado 'done' gera erro."""
    work_id = "US-TEST-STATE-03"
    item_path = squad_env.init_work_item(work_id, "low")
    status_path = item_path / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "done"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad_env.advance_state(item_path)
    assert "já está no estado terminal 'done'" in str(exc.value)


def test_advance_state_incident_gate_bypass(squad_env, monkeypatch):
    """Valida que ciclos com gate_bypass=true (ex: incident) avançam sem exigir gates."""
    work_id = "BUG-TEST-INCIDENT-01"
    item_path = squad_env.init_work_item(work_id, "high")
    status_path = item_path / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["cycle"] = "incident"
    status["state"] = "triage"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    original_validate = squad_env._validate

    def relaxed_validate(value, schema):
        if schema == "work-item.schema.json":
            v = {k: val for k, val in value.items() if k != "cycle"}
            return original_validate(v, schema)
        return original_validate(value, schema)

    monkeypatch.setattr(squad_env, "_validate", relaxed_validate)

    result = squad_env.advance_state(item_path)
    assert result["previous_state"] == "triage"
    assert result["state"] == "mitigation"


def test_workflow_collaborators_and_selectable_agents():
    """Valida que devops-release-engineer e scrum-master foram devidamente registrados em workflow.yaml."""
    workflow = yaml.safe_load((ROOT / "config/workflow.yaml").read_text(encoding="utf-8"))
    states = {s["id"]: s for s in workflow["states"]}

    # Scrum Master em scaffolding
    assert "scrum-master" in states["scaffolding"].get("collaborators", [])

    # DevOps Release Engineer em governance-release e implementation
    assert "devops-release-engineer" in states["governance-release"].get("collaborators", [])
    assert "devops-release-engineer" in states["implementation"].get("selectable_agents", [])

    # G3-readiness definido em gates
    assert "G3-readiness" in workflow["gates"]
    assert workflow["gates"]["G3-readiness"]["owner"] == "delivery-orchestrator"
    assert "scrum-master" in workflow["gates"]["G3-readiness"].get("collaborators", [])


def test_gate_validators_criteria_aligned(tmp_path):
    """Valida alinhamento estrito dos critérios entre gate_validators e workflow.yaml."""
    workflow = yaml.safe_load((ROOT / "config/workflow.yaml").read_text(encoding="utf-8"))

    work_item = tmp_path / "work/US-VAL"
    work_item.mkdir(parents=True)
    (work_item / "status.yaml").write_text(
        yaml.safe_dump({"id": "US-VAL", "risk": "low", "state": "blueprint", "story_points": 3}),
        encoding="utf-8"
    )
    (work_item / "blueprint.md").write_text("# Blueprint\nproblem goal\n", encoding="utf-8")
    (work_item / "specs" / "features").mkdir(parents=True)
    (work_item / "specs" / "features" / "sample.feature").write_text(
        "Feature: Test\n  Scenario: S1\n    Given a\n    When b\n    Then c\n",
        encoding="utf-8"
    )

    # G1
    g1 = validate_G1_product(work_item)
    g1_criteria = [f["criterion"] for f in g1["findings"]]
    assert set(g1_criteria) == set(workflow["gates"]["G1-product"]["criteria"])

    # G3
    (work_item / "plans").mkdir(parents=True, exist_ok=True)
    (work_item / "plans" / "delivery-plan.md").write_text("definition of ready owner responsible", encoding="utf-8")
    g3 = validate_G3_readiness(work_item)
    g3_criteria = [f["criterion"] for f in g3["findings"]]
    assert set(g3_criteria) == set(workflow["gates"]["G3-readiness"]["criteria"])

    # G6
    (work_item / "documentation").mkdir(parents=True, exist_ok=True)
    (work_item / "documentation" / "delivery-ledger.md").write_text(
        "| ID | State | Decision |\n|---|---|---|\n| 1 | done | pass | traceability rollout rollback runbook change\n",
        encoding="utf-8"
    )
    g6 = validate_G6_governance_release(work_item)
    g6_criteria = [f["criterion"] for f in g6["findings"]]
    assert set(g6_criteria) == set(workflow["gates"]["G6-governance-release"]["criteria"])


def test_validate_g3_readiness_passes_with_real_evidences(tmp_path):
    """Valida que G3-readiness aprova com evidências reais e sem bypass."""
    work_item = tmp_path / "work/US-G3-PASS"
    work_item.mkdir(parents=True)
    (work_item / "status.yaml").write_text(
        yaml.safe_dump({"id": "US-G3-PASS", "risk": "low", "state": "scaffolding", "owner": "delivery-orchestrator", "story_points": 5}),
        encoding="utf-8"
    )
    (work_item / "plans").mkdir(parents=True)
    (work_item / "plans" / "delivery-plan.md").write_text(
        "## Definition of Ready\n- dependencies: sem bloqueio\n- owner: delivery-orchestrator\n",
        encoding="utf-8"
    )
    (work_item / "tests").mkdir(parents=True)
    (work_item / "tests" / "test_red.py").write_text("def test_failing(): assert False\n", encoding="utf-8")

    result = validate_G3_readiness(work_item)
    assert result["gate"] == "G3-readiness"
    assert result["approved"] is True
    assert result["next_state"] == "implementation"
    assert all(f["status"] == "PASS" for f in result["findings"])


def test_validate_g3_readiness_fails_without_dependencies_or_sizing(tmp_path):
    """Valida que G3-readiness falha de forma estrita quando faltam dependências ou sizing (sem or True)."""
    work_item = tmp_path / "work/US-G3-FAIL"
    work_item.mkdir(parents=True)
    (work_item / "status.yaml").write_text(
        yaml.safe_dump({"id": "US-G3-FAIL", "risk": "low", "state": "scaffolding"}),
        encoding="utf-8"
    )
    (work_item / "plans").mkdir(parents=True)
    (work_item / "plans" / "delivery-plan.md").write_text("Plan without required keywords\n", encoding="utf-8")

    result = validate_G3_readiness(work_item)
    assert result["approved"] is False
    assert result["next_state"] == "scaffolding"
    findings_map = {f["criterion"]: f for f in result["findings"]}
    assert findings_map["definition-of-ready"]["status"] == "FAIL"
    assert findings_map["dependencies-resolved"]["status"] == "FAIL"
    assert findings_map["owners-assigned"]["status"] == "FAIL"


def test_validate_g3_readiness_blocks_story_points_above_8(tmp_path):
    """Valida que G3-readiness bloqueia histórias com Story Points > 8 por proteção cognitiva."""
    work_item = tmp_path / "work/US-G3-OVERSIZED"
    work_item.mkdir(parents=True)
    (work_item / "status.yaml").write_text(
        yaml.safe_dump({"id": "US-G3-OVERSIZED", "risk": "low", "state": "scaffolding", "owner": "delivery-orchestrator", "story_points": 13}),
        encoding="utf-8"
    )
    (work_item / "plans").mkdir(parents=True)
    (work_item / "plans" / "delivery-plan.md").write_text(
        "## Definition of Ready\n- dependencies: resolved\n",
        encoding="utf-8"
    )
    (work_item / "tests").mkdir(parents=True)
    (work_item / "tests" / "test_red.py").write_text("assert False\n", encoding="utf-8")

    result = validate_G3_readiness(work_item)
    assert result["approved"] is False
    findings_map = {f["criterion"]: f for f in result["findings"]}
    assert findings_map["definition-of-ready"]["status"] == "FAIL"
    assert "exceeds limit 8" in findings_map["definition-of-ready"]["evidence"]


def test_advance_state_syncs_with_azure_devops_when_devops_id_present(squad_env, monkeypatch):
    """Valida que advance_state sincroniza estado com Azure DevOps quando devops_id está presente."""
    work_id = "US-TEST-ADO-SYNC"
    item_path = squad_env.init_work_item(work_id, "low")
    status_path = item_path / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["devops_id"] = "9988"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    # Cria decisão aprovada para G1-product
    decisions_dir = item_path / "gate-decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    g1_decision = {
        "decision_id": f"GD-{work_id}-G1-PRODUCT",
        "gate_id": "G1-product",
        "work_item_id": work_id,
        "decision": "approved",
        "decider": "product-owner",
        "criteria": [{"name": "blueprint-complete", "result": "pass"}],
        "evidence": ["discovery/brief.md"],
    }
    (decisions_dir / f"GD-{work_id}-G1-PRODUCT.yaml").write_text(
        yaml.safe_dump(g1_decision), encoding="utf-8"
    )

    sent_calls = []

    class MockClient:
        org = "https://dev.azure.com/testorg"
        project = "Arthemis"

        def send(self, method, url, body, content_type="application/json"):
            sent_calls.append({"method": method, "url": url, "body": body, "content_type": content_type})
            return {"id": 9988}

    class MockConnector:
        def __init__(self, *args, **kwargs):
            self.client = MockClient()

    import devops_platform_connector
    monkeypatch.setattr(devops_platform_connector, "DevOpsPlatformConnector", MockConnector)

    result = squad_env.advance_state(item_path)
    assert result["state"] == "scaffolding"
    assert len(sent_calls) == 1
    assert sent_calls[0]["method"] == "PATCH"
    assert "9988" in sent_calls[0]["url"]
    assert sent_calls[0]["body"] == [{"op": "add", "path": "/fields/System.State", "value": "Active"}]


def test_advance_state_devops_sync_resilient_on_error(squad_env, monkeypatch):
    """Valida que falha na chamada ao ADO não bloqueia o avanço local."""
    work_id = "US-TEST-ADO-FAIL"
    item_path = squad_env.init_work_item(work_id, "low")
    status_path = item_path / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["devops_id"] = "7766"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    decisions_dir = item_path / "gate-decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / f"GD-{work_id}-G1-PRODUCT.yaml").write_text(
        yaml.safe_dump({"decision": "approved", "gate_id": "G1-product"}), encoding="utf-8"
    )

    class BrokenClient:
        org = "testorg"
        project = "Arthemis"

        def send(self, *a, **kw):
            raise ConnectionError("ADO unreachable")

    class BrokenConnector:
        def __init__(self, *args, **kwargs):
            self.client = BrokenClient()

    import devops_platform_connector
    monkeypatch.setattr(devops_platform_connector, "DevOpsPlatformConnector", BrokenConnector)

    result = squad_env.advance_state(item_path)
    assert result["state"] == "scaffolding"


def test_init_work_item_creates_ado_card_when_flag_present(squad_env, monkeypatch):
    """Valida criação de card no ADO quando devops=True em init_work_item."""
    created_items = []

    class MockConnector:
        def __init__(self, *args, **kwargs):
            self.client = None

        def create_work_item(self, **kwargs):
            created_items.append(kwargs)
            return {"id": "5544"}

    import devops_platform_connector
    monkeypatch.setattr(devops_platform_connector, "DevOpsPlatformConnector", MockConnector)

    work_id = "US-TEST-INIT-ADO"
    item_path = squad_env.init_work_item(work_id, "low", devops=True)
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status.get("devops_id") == "5544"
    assert len(created_items) == 1
    assert created_items[0]["title"] == work_id
