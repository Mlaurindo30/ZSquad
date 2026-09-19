"""
R0 Red Diagnostic Suite — Delivery Lifecycle and State Machine.
Proves failures in R0-LIFE-001 through R0-LIFE-014 against current broken runtime behavior.
DO NOT FIX IN R0. These tests MUST FAIL (RED) to demonstrate the current defects.
"""

from pathlib import Path
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_squad import AgentSquad, SquadError, read_yaml
from continuous_trigger_engine import (
    ContinuousTriggerEngine,
    EVENT_HANDOFF_CREATED,
    EngineEvent,
)


@pytest.fixture
def isolated_squad():
    temp_dir = tempfile.TemporaryDirectory()
    work_dir = Path(temp_dir.name) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    squad = AgentSquad(ROOT, project_name=None, allow_legacy=True)
    yield squad, work_dir
    temp_dir.cleanup()



def test_r0_life_001_discovery_phase_must_be_mandatory_in_development_cycle(isolated_squad):
    """
    R0-LIFE-001: Discovery mandatory?
    Invariant: A full development work item must pass through mandatory discovery phase before implementation.
    Current defect: 'discovery' is absent from cycles.yaml development cycle states (only blueprint -> scaffolding -> implementation).
    """
    squad, _ = isolated_squad
    dev_cycle = squad.cycles.get("cycles", {}).get("development", {})
    states = dev_cycle.get("states", [])

    # Invariant: discovery must be an executable, mandatory state in the development cycle.
    assert "discovery" in states, (
        f"R0-LIFE-001 CONFIRMED: 'discovery' state is missing from development cycle states: {states}"
    )


def test_r0_life_002_g2_design_cannot_be_skipped(isolated_squad):
    """
    R0-LIFE-002: G2 can be skipped?
    Invariant: Advancement from blueprint to scaffolding/readiness requires formal G2-design decision.
    Current defect: advance_state in 'blueprint' only requires G1-product; G2-design is never checked and is skipped.
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("US-LIFECYCLE-G2", "medium", base=work_dir)

    # Place an approved G1-product decision in gate-decisions/
    decisions_dir = item / "gate-decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    from agent_squad import write_yaml
    write_yaml(decisions_dir / "G1-product.yaml", {
        "gate_id": "G1-product",
        "decision": "approved",
        "decider": "product-owner",
    })

    # Invariant: advance_state from blueprint must require G2-design before reaching scaffolding.
    # Current behavior: advance_state succeeds and advances straight to scaffolding without G2!
    with pytest.raises(SquadError, match="(?i)g2-design"):
        res = squad.advance_state(item)
        assert res["state"] != "scaffolding", "G2 was skipped and state advanced directly to scaffolding!"


def test_r0_life_003_gates_out_of_order_must_be_rejected(isolated_squad):
    """
    R0-LIFE-003: Gates out of order.
    Invariant: Gates must be state-eligible; evaluating G5-quality while in 'blueprint' must be rejected.
    Current defect: decide_gate accepts any valid gate regardless of current work item state.
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("US-GATE-ORDER", "medium", base=work_dir)
    status = read_yaml(item / "status.yaml")
    assert status["state"] == "blueprint"

    # Attempt to decide G5-quality when work item is still in 'blueprint'
    g5_criteria = [(c, "pass") for c in squad.get_gate("G5-quality")["criteria"]]
    
    # Invariant: decide_gate must reject gate decision if gate is not eligible for the current state.
    # Current behavior: decide_gate does not validate current state and attempts to evaluate criteria for G5!
    with pytest.raises(SquadError, match="(?i)(state-eligible|not eligible for state|invalid gate for state)"):
        squad.decide_gate(
            item, "G5-quality", "qa-engineer", g5_criteria, ["blueprint.md"],
            human_approved_by="00-delivery-orchestrator", human_evidence="blueprint.md"
        )


def test_r0_life_004_pending_handoff_must_not_cause_state_advancement(isolated_squad):
    """
    R0-LIFE-004: Pending handoff advancement.
    Invariant: Handoff with acknowledgement status 'pending' must NOT cause state advancement.
    Current defect: ContinuousTriggerEngine.handle_handoff_created() immediately invokes squad.advance_state()
    without verifying that the handoff has been acknowledged (status == 'accepted').
    """
    import inspect
    from continuous_trigger_engine import ContinuousTriggerEngine

    src = inspect.getsource(ContinuousTriggerEngine.handle_handoff_created)

    # Invariant: handle_handoff_created must verify that the handoff has been acknowledged before advancing state.
    # Current behavior: it calls squad.advance_state without inspecting handoff acknowledgement status!
    assert "acknowledgement" in src or "ack" in src, (
        "R0-LIFE-004 CONFIRMED: ContinuousTriggerEngine.handle_handoff_created does not verify handoff ACK before calling advance_state"
    )



def test_r0_life_005_implementation_requires_execution_receipt(isolated_squad):
    """
    R0-LIFE-005: Implementation without execution proof.
    Invariant: Advancing from 'implementation' to 'code-security-review' requires DispatchReceipt/ExecutionReceipt.
    Current defect: state_to_gate in advance_state has no entry for 'implementation' (None);
    advance_state allows transition without any gate, receipt, or execution evidence!
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("FEAT-IMPL-PROOF", "low", base=work_dir)
    
    # Manually set state to 'implementation'
    status = read_yaml(item / "status.yaml")
    status["state"] = "implementation"
    from agent_squad import write_yaml
    write_yaml(item / "status.yaml", status)

    # Invariant: advance_state from implementation must fail without ExecutionReceipt.
    # Current behavior: advance_state succeeds and advances straight to code-security-review!
    with pytest.raises(SquadError, match="(?i)(execution receipt|dispatch receipt|execution proof)"):
        squad.advance_state(item)


def test_r0_life_006_review_execution_enforcement(isolated_squad):
    """
    R0-LIFE-006: Review execution not enforced.
    Invariant: Advancing from code-security-review requires proof that code-reviewer actually executed (ReviewReceipt).
    Current defect: Gate validation checks text/json findings, not actual specialist execution receipt.
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("FEAT-REV-PROOF", "low", base=work_dir)
    status = read_yaml(item / "status.yaml")
    status["state"] = "code-security-review"
    from agent_squad import write_yaml
    write_yaml(item / "status.yaml", status)

    # Invariant: advance_state must require DispatchReceipt/ReviewReceipt for code-reviewer.
    with pytest.raises(SquadError, match="(?i)(review receipt|reviewer execution)"):
        squad.advance_state(item)


def test_r0_life_011_continuous_engine_orchestration_depth(isolated_squad):
    """
    R0-LIFE-011: Continuous engine is only a state advancer?
    Invariant: ContinuousTriggerEngine must orchestrate real agent dispatch, execution receipts, and handoffs.
    Current defect: run_continuous only loops over squad.advance_state(), lacking dispatch, receipts, and execution.
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("US-CONT-ENGINE", "low", base=work_dir)
    engine = ContinuousTriggerEngine(squad)

    result = engine.run_continuous(str(item), max_steps=1)

    # Invariant: run_continuous result must report agent dispatch or delegation activity.
    # Current behavior: history only contains step/advance_state mutations.
    assert "agent_dispatches" in result, (
        f"R0-LIFE-011 CONFIRMED: ContinuousTriggerEngine does not dispatch agents, only advances states: {result}"
    )



def test_r0_life_012_wip_limits_must_block_excess_items(isolated_squad):
    """
    R0-LIFE-012: WIP limits enforcement.
    Invariant: Configured WIP limit for 'blueprint' (2) must prevent entering/creating a 3rd concurrent item in blueprint.
    Current defect: WIP limits in workflow.yaml are purely decorative; advance_state / init_work_item does not enforce WIP.
    """
    squad, work_dir = isolated_squad
    wip_limit = squad.workflow.get("flow", {}).get("wip_limits", {}).get("blueprint", 2)
    assert wip_limit == 2

    # Create 2 items in blueprint (reaching limit)
    squad.init_work_item("FEAT-WIP-001", "low", base=work_dir)
    squad.init_work_item("FEAT-WIP-002", "low", base=work_dir, force=True)

    # Invariant: Creating a 3rd item in blueprint must raise WIP breach error.
    # Current behavior: It creates without any WIP limit check!
    with pytest.raises(SquadError, match="(?i)(wip limit|wip breach)"):
        squad.init_work_item("FEAT-WIP-003", "low", base=work_dir, force=True)


def test_r0_life_013_phase_timeboxes_enforced_in_lifecycle(isolated_squad):
    """
    R0-LIFE-013: Phase timeboxes.
    Invariant: Expired timebox must influence runtime lifecycle execution (e.g. block or trigger remediation).
    Current defect: advance_state ignores timeboxes completely.
    """
    squad, work_dir = isolated_squad
    item = squad.init_work_item("FEAT-TIMEBOX-EXPIRED", "low", base=work_dir)
    status = read_yaml(item / "status.yaml")
    
    # Set phase_started_at to 100 days ago
    status["phase_started_at"] = "2020-01-01T00:00:00Z"
    from agent_squad import write_yaml
    write_yaml(item / "status.yaml", status)

    # Invariant: advance_state or lifecycle check must reject or tag expired timebox during advancement.
    # Current behavior: advance_state does not check timebox at all.
    with pytest.raises(SquadError, match="(?i)(timebox exceeded|timebox expired)"):
        squad.advance_state(item)


def test_r0_life_014_new_project_cycle_canonical_reachability(isolated_squad):
    """
    R0-LIFE-014: new-project cycle reachability.
    Invariant: 'new-project' must be reachable through canonical type_to_cycle resolution in config/cycles.yaml.
    Current defect: 'new-project' is defined under cycles, but missing from type_to_cycle mapping!
    """
    squad, _ = isolated_squad
    mapping = squad.cycles.get("type_to_cycle", {})

    # Invariant: new-project must be mapped in type_to_cycle
    assert "new-project" in mapping, (
        f"R0-LIFE-014 CONFIRMED: 'new-project' is unreachable via type_to_cycle mapping: {mapping}"
    )
