import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _cycles() -> dict:
    return yaml.safe_load((ROOT / "config/cycles.yaml").read_text(encoding="utf-8"))


def _workflow() -> dict:
    return yaml.safe_load((ROOT / "config/workflow.yaml").read_text(encoding="utf-8"))


def test_default_development_cycle_walks_canonical_states():
    cycles = _cycles()
    workflow = _workflow()
    dev = cycles["cycles"][cycles["default_cycle"]]

    assert dev["entry"] == "blueprint"
    assert dev["states"] == [state["id"] for state in workflow["states"]]


def test_cycle_practices_reference_real_gate_criteria():
    cycles = _cycles()
    workflow = _workflow()
    known = {
        criterion
        for gate in workflow["gates"].values()
        for criterion in gate.get("criteria", [])
    }

    for cycle in cycles["cycles"].values():
        if cycle.get("gate_bypass"):
            continue  # e.g. incident cycle — SRE triage/mitigation/postmortem bypass G1-G6 gates
        for state_practices in cycle.get("practices", {}).values():
            assert set(state_practices) <= known


def test_cycles_registry_is_open_for_new_functions():
    cycles = _cycles()

    assert "development" in cycles["cycles"]
    assert cycles["default_cycle"] in cycles["cycles"]
    assert isinstance(cycles["cycles"], dict)
