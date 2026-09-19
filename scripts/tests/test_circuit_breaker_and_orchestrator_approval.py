import shutil
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad, SquadError

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def squad_env():
    work_dir = ROOT / "work" / "test-cb-orch"
    lock_dir = ROOT / ".locks" / "test-cb-orch"

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name="test-cb-orch")
    yield squad
    cleanup()

CRITERIA = [
    ("bdd-specification-valid", "pass"),
    ("blueprint-complete", "fail"),
    ("data-contracts-defined-when-applicable", "not_applicable"),
    ("finding-documented", "not_applicable"),
    ("question-stated", "not_applicable"),
    ("timebox-defined", "not_applicable")
]
PASS_CRITERIA = [
    ("bdd-specification-valid", "pass"),
    ("blueprint-complete", "pass"),
    ("data-contracts-defined-when-applicable", "pass"),
    ("finding-documented", "pass"),
    ("question-stated", "pass"),
    ("timebox-defined", "pass")
]

def mock_validate_gate(gate_id, item_path):
    return {"findings": [{"criterion": "bdd-specification-valid", "status": "pass"}]}

def test_circuit_breaker_trips_on_third_failure(squad_env, monkeypatch):
    work_id = "US-TEST-CB-01"
    item_path = squad_env.init_work_item(work_id, "low")
    
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    
    evidence = ["blueprint.md"]
    
    with pytest.raises(SquadError, match=r"limite de \d+ tentativas excedido"):
        for i in range(4):
            squad_env.decide_gate(
                item=item_path,
                gate_id="G1-product",
                decider="product-owner",
                criteria=CRITERIA,
                evidence=evidence
            )
            
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["state"] == "blocked"
    assert status["retry_counts"]["G1-product"] >= 2


def test_orchestrator_autonomous_approval_medium_risk(squad_env, monkeypatch):
    work_id = "US-TEST-ORCH-01"
    item_path = squad_env.init_work_item(work_id, "medium")
    
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    
    evidence = ["blueprint.md"]
    
    res = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=PASS_CRITERIA,
        evidence=evidence,
        human_approved_by="delivery-orchestrator",
        human_evidence="blueprint.md"
    )
    
    assert res["decision"] == "approved"
    assert res["orchestrator_approval"]["approved_by"] == "delivery-orchestrator"


def test_orchestrator_autonomous_approval_rejects_missing_evidence(squad_env, monkeypatch):
    work_id = "US-TEST-ORCH-02"
    item_path = squad_env.init_work_item(work_id, "medium")
    
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    
    evidence = ["blueprint.md"]
    
    with pytest.raises(SquadError, match="exige aprovação humana e evidência"):
        squad_env.decide_gate(
            item=item_path,
            gate_id="G1-product",
            decider="product-owner",
            criteria=PASS_CRITERIA,
            evidence=evidence,
            human_approved_by="delivery-orchestrator",
            human_evidence=None
        )
