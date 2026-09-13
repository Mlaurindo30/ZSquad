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


# ---------------------------------------------------------------------------
# T5 — SDD: preflight declarado nos ciclos e alinhamento de estágios/critérios
# ---------------------------------------------------------------------------


def _load_policy_module():
    import importlib.util

    base = ROOT / "integrations" / "spec-kit" / "adapter"
    name = "sdd_adapter_t5_cycles"
    if name in sys.modules:
        return importlib.import_module(f"{name}.policy")
    spec = importlib.util.spec_from_file_location(
        name, base / "__init__.py", submodule_search_locations=[str(base)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(f"{name}.policy")


def test_bugfix_and_new_project_declare_sdd_preflight():
    """Ciclos que entram direto em implementation não contornam o preflight SDD."""
    cycles = _cycles()

    for cycle_name in ("bugfix", "new-project"):
        cycle = cycles["cycles"][cycle_name]
        assert cycle.get("sdd_preflight") is True, cycle_name


def test_development_cycles_declare_sdd_stages():
    """Ciclos de desenvolvimento declaram as subetapas SDD por estado."""
    cycles = _cycles()
    policy = _load_policy_module()
    known_stages = set(policy.STAGE_GATES)

    for cycle_name in ("development", "user-story", "evolution"):
        sdd_stages = cycles["cycles"][cycle_name].get("sdd_stages")
        assert isinstance(sdd_stages, dict), cycle_name
        assert set(sdd_stages) <= set(state["id"] for state in _workflow()["states"])
        for stage in sdd_stages.values():
            assert stage in known_stages, (cycle_name, stage)
    # Conclusão de blueprint exige G1+G2 (estágio tasking cobre G1 via cadeia).
    assert cycles["cycles"]["development"]["sdd_stages"]["blueprint"] == "tasking"
    # G3 autoriza implementação (estágio implementation cobre G1+G2+G3 via cadeia).
    assert cycles["cycles"]["development"]["sdd_stages"]["scaffolding"] == "implementation"


def test_workflow_sdd_integration_matches_adapter_contract():
    workflow = _workflow()
    policy = _load_policy_module()
    sdd = workflow.get("sdd_integration")
    assert isinstance(sdd, dict)
    assert set(sdd.get("stages", {})) == set(policy.STAGE_GATES)


def test_gate_criteria_cover_executable_validator_findings():
    """Todo critério executável calculado por um validador está configurado em
    algum gate vigente (config não omite verificação executável existente)."""
    workflow = _workflow()
    executable_names = {
        "bdd-specification-valid",
        "tdd-cycle-valid",
        "clean-code-executed",
        "tests-executed",
        "security-executed",
        "acceptance-bdd-executed",
        "regression-executed",
        "coverage-executed",
    }
    configured = {
        criterion
        for gate in workflow["gates"].values()
        for criterion in gate.get("criteria", [])
    }
    # Varredura estática: critérios que os validadores realmente emitem
    # (gate_validators.py calcula findings para os nomes presentes no código).
    source = (ROOT / "scripts" / "gate_validators.py").read_text(encoding="utf-8")
    computed = {name for name in executable_names if f'"{name}"' in source}
    assert computed <= configured
