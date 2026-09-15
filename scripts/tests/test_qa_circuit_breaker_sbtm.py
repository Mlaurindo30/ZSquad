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
    work_dir = ROOT / "work" / "test-sbtm-cb"
    lock_dir = ROOT / ".locks" / "test-sbtm-cb"

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name="test-sbtm-cb")
    yield squad
    cleanup()

FAIL_CRITERIA = [
    ("bdd-specification-valid", "pass"),
    ("blueprint-complete", "fail"),
    ("data-contracts-defined-when-applicable", "not_applicable"),
    ("finding-documented", "not_applicable"),
    ("question-stated", "not_applicable"),
    ("timebox-defined", "not_applicable"),
]

PASS_CRITERIA = [
    ("bdd-specification-valid", "pass"),
    ("blueprint-complete", "pass"),
    ("data-contracts-defined-when-applicable", "pass"),
    ("finding-documented", "pass"),
    ("question-stated", "pass"),
    ("timebox-defined", "pass"),
]

def mock_validate_gate(gate_id, item_path):
    return {"findings": [{"criterion": "bdd-specification-valid", "status": "pass"}]}


def test_sbtm_circuit_breaker_exact_boundary_progression(squad_env, monkeypatch):
    """
    SBTM Boundary Probing:
    Probes failure 1, failure 2, and failure 3 (threshold = 2 retries).
    Verifies retry_counts increments and status state transition on trip.
    """
    work_id = "US-SBTM-BOUNDARY-01"
    item_path = squad_env.init_work_item(work_id, "low")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    # Failure 1 (1st rejection): count becomes 1 <= 2 -> No circuit break
    res1 = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=FAIL_CRITERIA,
        evidence=evidence,
    )
    assert res1["decision"] == "changes_requested"
    status1 = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status1["state"] == "blueprint"
    assert status1["retry_counts"]["G1-product"] == 1

    # Failure 2 (2nd rejection): count becomes 2 <= 2 -> No circuit break
    res2 = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=FAIL_CRITERIA,
        evidence=evidence,
    )
    assert res2["decision"] == "changes_requested"
    status2 = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status2["state"] == "blueprint"
    assert status2["retry_counts"]["G1-product"] == 2

    # Failure 3 (3rd rejection): count becomes 3 > 2 -> Trips CIRCUIT_BREAKER_OPEN
    with pytest.raises(SquadError, match=r"CIRCUIT_BREAKER_OPEN: limite de 2 tentativas excedido para o portão G1-product"):
        squad_env.decide_gate(
            item=item_path,
            gate_id="G1-product",
            decider="product-owner",
            criteria=FAIL_CRITERIA,
            evidence=evidence,
        )

    status3 = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status3["state"] == "blocked"
    assert status3["retry_counts"]["G1-product"] == 3


def test_sbtm_gate_retry_isolation(squad_env, monkeypatch):
    """
    SBTM Heuristic - State Partitioning:
    Verifies that failures on one gate do not count against another gate.
    """
    work_id = "US-SBTM-ISOLATION-01"
    item_path = squad_env.init_work_item(work_id, "low")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    # Fail G1-product once
    squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=FAIL_CRITERIA,
        evidence=evidence,
    )
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["retry_counts"]["G1-product"] == 1
    assert "G2-design" not in status["retry_counts"]


def test_sbtm_recovery_after_transient_failure(squad_env, monkeypatch):
    """
    SBTM Heuristic - Resilience & Self-Healing:
    Verifies that if a gate fails once, then passes, it is approved successfully
    and retry_counts preserves the record without crashing or tripping.
    """
    work_id = "US-SBTM-RECOVERY-01"
    item_path = squad_env.init_work_item(work_id, "low")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    # Attempt 1: fails
    res1 = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=FAIL_CRITERIA,
        evidence=evidence,
    )
    assert res1["decision"] == "changes_requested"

    # Attempt 2: corrected and passes
    res2 = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=PASS_CRITERIA,
        evidence=evidence,
    )
    assert res2["decision"] == "approved"
    status = yaml.safe_load((item_path / "status.yaml").read_text(encoding="utf-8"))
    assert status["retry_counts"]["G1-product"] == 1


def test_sbtm_orchestrator_autonomous_approval_aliases(squad_env, monkeypatch):
    """
    SBTM Heuristic - Identity Equivalence:
    Both '00-delivery-orchestrator' and 'delivery-orchestrator' must be valid autonomous approvers.
    """
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)

    for idx, orch_id in enumerate(["00-delivery-orchestrator", "delivery-orchestrator"], start=1):
        work_id = f"US-SBTM-ORCH-0{idx}"
        item_path = squad_env.init_work_item(work_id, "medium")
        evidence = ["blueprint.md"]

        res = squad_env.decide_gate(
            item=item_path,
            gate_id="G1-product",
            decider="product-owner",
            criteria=PASS_CRITERIA,
            evidence=evidence,
            human_approved_by=orch_id,
            human_evidence="blueprint.md",
        )
        assert res["decision"] == "approved"
        assert res["orchestrator_approval"]["approved_by"] == orch_id
        assert res["orchestrator_approval"]["evidence"] == "blueprint.md"
        assert res["human_approval"]["required"] is False


def test_sbtm_orchestrator_rejection_on_nonexistent_evidence(squad_env, monkeypatch):
    """
    SBTM Adversarial Probing:
    Orchestrator submits an evidence path that does NOT exist on filesystem.
    Must be rejected by integrity check.
    """
    work_id = "US-SBTM-EV-GHOST-01"
    item_path = squad_env.init_work_item(work_id, "medium")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    with pytest.raises(SquadError, match="evidência de aprovação autônoma inexistente"):
        squad_env.decide_gate(
            item=item_path,
            gate_id="G1-product",
            decider="product-owner",
            criteria=PASS_CRITERIA,
            evidence=evidence,
            human_approved_by="00-delivery-orchestrator",
            human_evidence="ghost_nonexistent_evidence.md",
        )


def test_sbtm_medium_risk_requires_approval_when_none_supplied(squad_env, monkeypatch):
    """
    SBTM Security & Governance:
    When risk is medium and no approval is provided, decide_gate must strictly reject.
    """
    work_id = "US-SBTM-NO-APPR-01"
    item_path = squad_env.init_work_item(work_id, "medium")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    with pytest.raises(SquadError, match="G1-product exige aprovação humana e evidência"):
        squad_env.decide_gate(
            item=item_path,
            gate_id="G1-product",
            decider="product-owner",
            criteria=PASS_CRITERIA,
            evidence=evidence,
            human_approved_by=None,
            human_evidence=None,
        )


def test_sbtm_non_orchestrator_does_not_create_orchestrator_approval_node(squad_env, monkeypatch):
    """
    SBTM Segregation of Duties:
    Non-orchestrator actor provides sign-off; it is logged as human_approval,
    and MUST NOT produce an orchestrator_approval node.
    """
    work_id = "US-SBTM-HUMAN-01"
    item_path = squad_env.init_work_item(work_id, "medium")
    monkeypatch.setattr("gate_validators.validate_gate", mock_validate_gate)
    evidence = ["blueprint.md"]

    res = squad_env.decide_gate(
        item=item_path,
        gate_id="G1-product",
        decider="product-owner",
        criteria=PASS_CRITERIA,
        evidence=evidence,
        human_approved_by="alice-external-lead",
        human_evidence="blueprint.md",
    )
    assert res["decision"] == "approved"
    assert "orchestrator_approval" not in res
    assert res["human_approval"]["status"] == "approved"
    assert res["human_approval"]["approved_by"] == "alice-external-lead"
