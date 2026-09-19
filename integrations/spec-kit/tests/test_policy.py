"""Testes comportamentais de autorização de gates SDD (Tarefa T4).

Cobre o plano (secoes 6 e 7-T4):
- decisao de outro work item -> rejeicao;
- input alterado -> ``SDD_STALE_GATE`` (inclui o exemplo literal do plano);
- autor igual a revisor -> rejeicao;
- politica invalida -> ``SDD_POLICY_INVALID`` (falha fechada);
- evidencia ausente -> rejeicao;
- positivo: decisoes validas autorizam os quatro estagios;
- invalidacao transitiva (spec alterada invalida G2/G3 derivados);
- mudanca de policy_version forc'a reavaliacao;
- modo legado (sdd.required false) permanece compativel;
- resolucao de politica por projeto em scripts/project_context.py.

Os registros de decisao replicam a estrutura REAL produzida por
``scripts/agent_squad.py decide-gate`` (lida de
work/agent_squad/TASK-SPECKIT-T1-20260911/gate-decisions/*.yaml):
decision_id, gate_id, work_item_id, decision, decider, criteria,
evidence, human_approval, conditions, valid_until, decided_at.
Os campos de vinculacao novos (``input_hashes`` e ``policy_version``)
sao aditivos e obrigatorios quando a politica SDD esta ativada.

O pacote e materializado em tmp_path (layout work_dir/sdd/), nunca em
arquivos de producao. O adaptador e carregado via importlib porque o
diretorio 'integrations/spec-kit' contem hifen e nao e importavel pelo
nome. `scripts.project_context` e carregado pelo caminho absoluto (o
diretorio 'spec-kit' tambem contem hifen).
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "integrations" / "spec-kit" / "adapter"

WORK_ID = "TASK-SPECKIT-T4-20260911"

SPEC_MD = "# Spec\n\nRequisito de exemplo com acentuação: título ção.\n"
PLAN_MD = "# Plan\n\nPlano de exemplo.\n"
CONSTITUTION_MD = "# Constituição do projeto\n\nPrincípios.\n"
EVIDENCE_MD = "# Evidência de revisão do gate\n\nConteúdo real em disco.\n"

CLARIFICATIONS_YAML = """questions:
  - id: Q-001
    requirement_ids: [REQ-001]
    severity: nonblocking
    status: resolved
    question: "Formato do relatório?"
    answer: "Markdown consolidado."
    source: "ata de refinement 2026-09-10"
    owner: product-owner
"""

TASKS_YAML = """tasks:
  - id: T-001
    requirement_ids: [REQ-001]
    acceptance_ids: [AC-001]
    owner: backend-engineer
    points: 3
    depends_on: []
    paths: ["docs/exemplo.md"]
    evidence: ["reviews/rev-exemplo.md"]
    test_ids: ["tests/test_exemplo.py::test_ok"]
    status: open
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_adapter():
    name = "sdd_adapter_t4"
    if name in sys.modules:
        return sys.modules[name], importlib.import_module(f"{name}.policy")
    spec = importlib.util.spec_from_file_location(
        name, ADAPTER_DIR / "__init__.py", submodule_search_locations=[str(ADAPTER_DIR)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover - só se arquivo faltar
        pytest.fail(f"adapter/__init__.py não encontrado em {ADAPTER_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, importlib.import_module(f"{name}.policy")


@pytest.fixture(scope="module")
def adapter():
    return _load_adapter()[0]


@pytest.fixture(scope="module")
def policy_mod():
    return _load_adapter()[1]


def _materialize(work_dir: Path) -> None:
    """Materializa os cinco documentos + evidências no work_dir."""
    sdd = work_dir / "sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    (work_dir / "docs").mkdir(parents=True, exist_ok=True)
    (work_dir / "reviews").mkdir(parents=True, exist_ok=True)

    files: dict[Path, bytes] = {
        sdd / "spec.md": SPEC_MD.encode("utf-8"),
        sdd / "clarifications.yaml": CLARIFICATIONS_YAML.encode("utf-8"),
        sdd / "plan.md": PLAN_MD.encode("utf-8"),
        sdd / "tasks.yaml": TASKS_YAML.encode("utf-8"),
        sdd / "constitution.md": CONSTITUTION_MD.encode("utf-8"),
        work_dir / "docs" / "exemplo.md": b"# exemplo\n",
        work_dir / "reviews" / "rev-exemplo.md": b"ok\n",
        work_dir / "reviews" / "rev-gate.md": EVIDENCE_MD.encode("utf-8"),
    }
    for path, data in files.items():
        path.write_bytes(data)


def _package_on_disk(work_dir: Path) -> dict:
    _materialize(work_dir)
    package = {
        "schema_version": 1,
        "project_id": "agent_squad",
        "work_id": WORK_ID,
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": _sha256(CONSTITUTION_MD.encode("utf-8")),
        "inputs": {
            "spec": {"path": "sdd/spec.md", "revision": "r1", "sha256": _sha256(SPEC_MD.encode("utf-8"))},
            "clarifications": {
                "path": "sdd/clarifications.yaml",
                "revision": "r1",
                "sha256": _sha256(CLARIFICATIONS_YAML.encode("utf-8")),
            },
            "plan": {"path": "sdd/plan.md", "revision": "r1", "sha256": _sha256(PLAN_MD.encode("utf-8"))},
            "tasks": {"path": "sdd/tasks.yaml", "revision": "r1", "sha256": _sha256(TASKS_YAML.encode("utf-8"))},
        },
        "requirements": [
            {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"},
        ],
        "reviews": [],
    }
    (work_dir / "sdd" / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return package


GATE_OWNERS = {
    "G1-product": "product-owner",
    "G2-design": "solution-architect",
    "G3-readiness": "delivery-orchestrator",
}


def _real_hashes() -> dict[str, str]:
    return {
        "spec": _sha256(SPEC_MD.encode("utf-8")),
        "clarifications": _sha256(CLARIFICATIONS_YAML.encode("utf-8")),
        "plan": _sha256(PLAN_MD.encode("utf-8")),
        "tasks": _sha256(TASKS_YAML.encode("utf-8")),
        "constitution": _sha256(CONSTITUTION_MD.encode("utf-8")),
    }


def _decision(
    gate: str,
    input_keys: tuple[str, ...],
    *,
    work_item: str = WORK_ID,
    decision: str = "approved",
    decider: str | None = None,
    evidence: tuple[str, ...] = ("reviews/rev-gate.md",),
    policy_version: int | None = 1,
    input_hashes: dict[str, str] | None = None,
    reviewer: str | None = "security-reviewer",
    author: str | None = "backend-engineer",
    decided_at: str = "2026-09-11T12:00:00Z",
    human_approval: dict | None = None,
) -> dict:
    """Registro de decisão com a estrutura real do decide-gate + vínculos novos."""
    real = _real_hashes()
    record: dict = {
        "decision_id": f"GD-{work_item}-{gate}",
        "gate_id": gate,
        "work_item_id": work_item,
        "decision": decision,
        "decider": decider if decider is not None else GATE_OWNERS[gate],
        "criteria": [{"name": "criterio-exemplo", "result": "pass"}],
        "evidence": list(evidence),
        "human_approval": human_approval
        if human_approval is not None
        else {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
        "conditions": [],
        "valid_until": None,
        "decided_at": decided_at,
    }
    if input_hashes is not False:
        record["input_hashes"] = (
            {key: real[key] for key in input_keys} if input_hashes is None else input_hashes
        )
    if policy_version is not None:
        record["policy_version"] = policy_version
    if reviewer is not None:
        record["reviewer"] = reviewer
    if author is not None:
        record["author"] = author
    return record


def _approved_decisions() -> list[dict]:
    return [
        _decision("G1-product", ("spec", "clarifications", "constitution")),
        _decision("G2-design", ("plan", "constitution")),
        _decision("G3-readiness", ("tasks", "constitution")),
    ]


@pytest.fixture()
def valid_package(adapter, tmp_path):
    """Pacote íntegro em work_dir com espaços/acentos no caminho (Windows)."""
    work_dir = tmp_path / "espaço no caminho" / "WORK T4 ção"
    _package_on_disk(work_dir)
    return adapter.load_package(work_dir)


@pytest.fixture()
def approved_decisions():
    return _approved_decisions()


@pytest.fixture()
def required_policy():
    return {"policy_version": 1, "sdd": {"required": True}}


# ---------------------------------------------------------------------------
# MAJOR-1 (security-reviewer): hash real None / documento ausente no disco
# nunca autoriza — nem via vínculo da decisão, nem via hash declarado.
# ---------------------------------------------------------------------------


def test_real_hash_none_with_none_binding_is_stale(policy_mod, valid_package, required_policy):
    """Probe B do revisor: documents.spec.sha256 = None + inputs.spec.sha256 = None
    + decisão vinculando {"spec": None} tem que dar SDD_STALE_GATE, nunca []."""
    real = _real_hashes()
    valid_package["documents"]["spec"]["sha256"] = None
    valid_package["inputs"]["spec"]["sha256"] = None
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            input_hashes={"spec": None, "clarifications": real["clarifications"], "constitution": real["constitution"]},
        )
    ]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert errors, "documento sem hash real não pode autorizar (fail-open)"
    assert any(e["code"] == "SDD_STALE_GATE" and "spec" in e["message"] for e in errors)


def test_missing_document_with_real_hash_binding_is_stale(policy_mod, valid_package, required_policy):
    """Probe do ponto de vínculo (policy.py:467-480): documento ausente (hash real
    None) com decisão vinculando o hash que existia antes -> SDD_STALE_GATE."""
    real = _real_hashes()
    valid_package["documents"]["spec"] = {
        "exists": False,
        "sha256": None,
        "path": "sdd/spec.md",
        "safe_path": None,
        "parsed": None,
        "content": None,
    }
    valid_package["inputs"]["spec"]["sha256"] = None
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            input_hashes={"spec": real["spec"], "clarifications": real["clarifications"], "constitution": real["constitution"]},
        )
    ]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert errors, "documento ausente no disco não pode autorizar"
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)


def test_constitution_real_hash_none_is_stale(policy_mod, valid_package, required_policy):
    """Constituição ausente (hash real None) invalida todos os gates, mesmo com
    vínculo None coerente e declaração None."""
    real = _real_hashes()
    valid_package["documents"]["constitution"]["sha256"] = None
    valid_package["constitution_sha256"] = None
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            input_hashes={"spec": real["spec"], "clarifications": real["clarifications"], "constitution": None},
        ),
        _decision(
            "G2-design",
            ("plan", "constitution"),
            input_hashes={"plan": real["plan"], "constitution": None},
        ),
        _decision(
            "G3-readiness",
            ("tasks", "constitution"),
            input_hashes={"tasks": real["tasks"], "constitution": None},
        ),
    ]
    errors = policy_mod.authorize(valid_package, "implementation", decisions, required_policy)
    stale = [e for e in errors if e["code"] == "SDD_STALE_GATE"]
    assert stale, "constituição sem hash real não pode autorizar nenhum gate"


# ---------------------------------------------------------------------------
# MAJOR-2 (security-reviewer): decided_at naive vs aware não pode derrubar
# o chamador — authorize -> list[dict] nunca propaga exceção de registro.
# ---------------------------------------------------------------------------


def test_mixed_naive_and_aware_decided_at_does_not_raise(policy_mod, valid_package, required_policy):
    """Duas decisões ISO-8601 válidas para o mesmo gate, uma com 'Z' (aware) e
    outra sem fuso (naive): sem TypeError; naive é interpretada como UTC."""
    d_early = _decision(
        "G1-product", ("spec", "clarifications", "constitution"),
        decided_at="2026-09-11T12:00:00Z", decision="rejected",
    )
    d_late = _decision(
        "G1-product", ("spec", "clarifications", "constitution"),
        decided_at="2026-09-11T13:00:00",  # naive: interpretada como UTC (mais recente)
    )
    errors = policy_mod.authorize(valid_package, "planning", [d_early, d_late], required_policy)
    assert errors == [], "decisão naive mais recente (13:00 UTC) é a selecionada e é válida"


def test_mixed_decided_at_order_does_not_matter(policy_mod, valid_package, required_policy):
    """Mesma sonda com a ordem da lista invertida: sem exceção, mesma seleção."""
    d_early = _decision(
        "G1-product", ("spec", "clarifications", "constitution"),
        decided_at="2026-09-11T12:00:00Z", decision="rejected",
    )
    d_late = _decision(
        "G1-product", ("spec", "clarifications", "constitution"),
        decided_at="2026-09-11T13:00:00",
    )
    errors = policy_mod.authorize(valid_package, "planning", [d_late, d_early], required_policy)
    assert errors == []


# ---------------------------------------------------------------------------
# MINOR-2: consistência GATE_OWNERS vs config/workflow.yaml (anti-drift)
# ---------------------------------------------------------------------------


def test_gate_owners_match_workflow_yaml(policy_mod):
    """Owners de gate em policy.py têm que bater com config/workflow.yaml."""
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
    )
    gates = workflow["gates"]
    for gate, owner in policy_mod.GATE_OWNERS.items():
        assert gate in gates, f"gate {gate} do mapeamento não existe em workflow.yaml"
        config = gates[gate]
        assert isinstance(config, dict) and config.get("owner") == owner, (
            f"drift de owner no gate {gate}: policy.py={owner!r} "
            f"workflow.yaml={config.get('owner')!r}"
        )
        assert config.get("deprecated") is not True, (
            f"gate {gate} está deprecated em workflow.yaml mas ainda é exigido por authorize"
        )


# ---------------------------------------------------------------------------
# Positivo: decisões válidas autorizam todos os estágios
# ---------------------------------------------------------------------------


def test_valid_decisions_authorize_all_stages(policy_mod, valid_package, approved_decisions, required_policy):
    for stage in ("planning", "tasking", "readiness", "implementation"):
        assert policy_mod.authorize(valid_package, stage, approved_decisions, required_policy) == [], stage


def test_is_sdd_required_helper(policy_mod, required_policy, valid_package):
    assert policy_mod.is_sdd_required(required_policy) is True
    assert policy_mod.is_sdd_required({"policy_version": 1, "sdd": {"required": False}}) is False
    assert policy_mod.is_sdd_required(None) is False
    assert policy_mod.is_sdd_required("não é política") is False


# ---------------------------------------------------------------------------
# Input alterado -> SDD_STALE_GATE (exemplo literal do plano)
# ---------------------------------------------------------------------------


def test_changed_spec_invalidates_plan(policy_mod, valid_package, approved_decisions, required_policy):
    valid_package["inputs"]["spec"]["sha256"] = "0" * 64
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)


def test_spec_changed_on_disk_invalidates_g1(policy_mod, valid_package, approved_decisions, required_policy):
    valid_package["documents"]["spec"]["sha256"] = "f" * 64
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    stale = [e for e in errors if e["code"] == "SDD_STALE_GATE"]
    assert stale and "G1-product" in stale[0]["path"]


def test_constitution_change_stale_all_gates(policy_mod, valid_package, approved_decisions, required_policy):
    valid_package["documents"]["constitution"]["sha256"] = "a" * 64
    errors = policy_mod.authorize(valid_package, "implementation", approved_decisions, required_policy)
    stale_paths = {e["path"] for e in errors if e["code"] == "SDD_STALE_GATE"}
    assert {"decisions.G1-product", "decisions.G2-design", "decisions.G3-readiness"} <= stale_paths


# ---------------------------------------------------------------------------
# Invalidação transitiva
# ---------------------------------------------------------------------------


def test_transitive_invalidation_spec_change_reaches_g2(policy_mod, valid_package, approved_decisions, required_policy):
    """Spec alterada invalida G1 e dependentes: autorização G2 (tasking) cai."""
    valid_package["documents"]["spec"]["sha256"] = "b" * 64
    errors = policy_mod.authorize(valid_package, "tasking", approved_decisions, required_policy)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)
    assert any("G2-design" in e["path"] for e in errors if e["code"] == "SDD_STALE_GATE")


def test_transitive_invalidation_tasks_change_reaches_readiness(policy_mod, valid_package, approved_decisions, required_policy):
    valid_package["documents"]["tasks"]["sha256"] = "c" * 64
    errors = policy_mod.authorize(valid_package, "readiness", approved_decisions, required_policy)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)


# ---------------------------------------------------------------------------
# policy_version mudou -> reavaliação obrigatória
# ---------------------------------------------------------------------------


def test_policy_version_change_forces_reevaluation(policy_mod, valid_package, approved_decisions):
    new_policy = {"policy_version": 2, "sdd": {"required": True}}
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, new_policy)
    assert any(e["code"] == "SDD_STALE_GATE" and "policy_version" in e["message"] for e in errors)


def test_decision_without_input_hashes_is_stale_in_activated_policy(policy_mod, valid_package, required_policy):
    decisions = [_decision("G1-product", ("spec", "clarifications", "constitution"), input_hashes=False, reviewer=None, author=None)]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)


# ---------------------------------------------------------------------------
# Decisão de outro work item / decisão ausente / não aprovada
# ---------------------------------------------------------------------------


def test_decision_from_other_work_item_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    approved_decisions[0]["work_item_id"] = "TASK-SPECKIT-OTHER-20260911"
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("outro work item" in e["message"] for e in errors)


def test_missing_gate_decision_is_rejected(policy_mod, valid_package, required_policy):
    decisions = [_decision("G1-product", ("spec", "clarifications", "constitution"))]
    errors = policy_mod.authorize(valid_package, "tasking", decisions, required_policy)
    assert any(e["code"] == "SDD_MISSING_INPUT" and "G2-design" in e["message"] for e in errors)


def test_implementation_requires_all_three_gates(policy_mod, valid_package, required_policy):
    decisions = _approved_decisions()[:2]
    errors = policy_mod.authorize(valid_package, "implementation", decisions, required_policy)
    assert any(e["code"] == "SDD_MISSING_INPUT" and "G3-readiness" in e["message"] for e in errors)


def test_rejected_decision_does_not_authorize(policy_mod, valid_package, required_policy):
    decisions = [_decision("G1-product", ("spec", "clarifications", "constitution"), decision="rejected")]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)


# ---------------------------------------------------------------------------
# Identidade: owner do gate e autor != revisor
# ---------------------------------------------------------------------------


def test_decider_not_gate_owner_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    approved_decisions[0]["decider"] = "software-engineer"
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("owner" in e["message"] for e in errors)


def test_author_equals_reviewer_is_rejected(policy_mod, valid_package, required_policy):
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            author="security-reviewer",
            reviewer="security-reviewer",
        )
    ]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert errors and any("revisor" in e["message"].lower() for e in errors)


def test_reviewer_equals_decider_is_rejected(policy_mod, valid_package, required_policy):
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            decider="product-owner",
            reviewer="product-owner",
        )
    ]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert errors and any("revisor" in e["message"].lower() for e in errors)


def test_package_review_author_equals_reviewer_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    valid_package["reviews"].append({"author": "code-reviewer", "reviewer": "code-reviewer", "result": "approved"})
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("reviews" in e["path"] for e in errors)


# ---------------------------------------------------------------------------
# Política inválida -> falha fechada
# ---------------------------------------------------------------------------


def test_invalid_policy_fails_closed(policy_mod, valid_package, approved_decisions):
    for bad_policy in (None, "não é dict", {}, {"sdd": {"required": True}}, {"policy_version": 0, "sdd": {"required": True}}):
        errors = policy_mod.authorize(valid_package, "planning", approved_decisions, bad_policy)
        assert errors, f"política {bad_policy!r} não pode autorizar"
        assert all(e["code"] == "SDD_POLICY_INVALID" for e in errors), bad_policy


def test_unknown_stage_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    errors = policy_mod.authorize(valid_package, "release", approved_decisions, required_policy)
    assert len(errors) == 1
    assert errors[0]["code"] == "SDD_MALFORMED"


# ---------------------------------------------------------------------------
# Evidência ausente -> rejeição
# ---------------------------------------------------------------------------


def test_missing_evidence_file_is_rejected(policy_mod, valid_package, approved_decisions, required_policy, tmp_path):
    evidence_file = tmp_path / "espaço no caminho" / "WORK T4 ção" / "reviews" / "rev-gate.md"
    evidence_file.unlink()
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("evidência" in e["message"].lower() for e in errors)


def test_evidence_outside_work_item_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    approved_decisions[0]["evidence"] = ["../../fora.md"]
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("evidência" in e["message"].lower() for e in errors)


def test_empty_evidence_list_is_rejected(policy_mod, valid_package, approved_decisions, required_policy):
    approved_decisions[0]["evidence"] = []
    errors = policy_mod.authorize(valid_package, "planning", approved_decisions, required_policy)
    assert errors and any("evidência" in e["message"].lower() for e in errors)


# ---------------------------------------------------------------------------
# human_approval exigido não aprovado -> rejeição
# ---------------------------------------------------------------------------


def test_required_human_approval_not_granted_is_rejected(policy_mod, valid_package, required_policy):
    decisions = [
        _decision(
            "G1-product",
            ("spec", "clarifications", "constitution"),
            human_approval={"required": True, "status": "not_required", "approved_by": None, "evidence": None},
        )
    ]
    errors = policy_mod.authorize(valid_package, "planning", decisions, required_policy)
    assert any(e["code"] == "SDD_MISSING_INPUT" and "human_approval" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# Modo legado: compatível, sem garantia SDD
# ---------------------------------------------------------------------------


def test_legacy_policy_stays_compatible_without_bindings(policy_mod, valid_package):
    legacy = {"policy_version": 1, "sdd": {"required": False}, "legacy_mode": True}
    assert policy_mod.is_sdd_required(legacy) is False
    assert policy_mod.authorize(valid_package, "planning", [], legacy) == []


def test_required_flag_wins_over_legacy_mode(policy_mod, valid_package):
    policy = {"policy_version": 1, "sdd": {"required": True}, "legacy_mode": True}
    errors = policy_mod.authorize(valid_package, "planning", [], policy)
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)


# ---------------------------------------------------------------------------
# Resolução de política por projeto (scripts/project_context.py)
# ---------------------------------------------------------------------------


def _load_project_context():
    import types

    name = "project_context_t4"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / "project_context.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_marker(project_root: Path) -> None:
    config = project_root / ".agents_squad" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "project.yaml").write_text(
        "version: 1\n"
        f"runtime: {REPO_ROOT.as_posix()}\n"
        "project_id: projeto_teste\n"
        f"project_root: {project_root.as_posix()}\n",
        encoding="utf-8",
    )


def test_policy_absent_means_legacy_compatible(tmp_path):
    ctx = _load_project_context()
    root = tmp_path / "projeto legado"
    root.mkdir()
    _write_marker(root)
    resolution = ctx.resolve_sdd_policy(root)
    assert resolution.state == "legacy"
    assert resolution.policy is None
    assert resolution.errors == ()
    assert ctx.sdd_required(root) is False


def test_policy_active_and_required(tmp_path):
    ctx = _load_project_context()
    root = tmp_path / "projeto ativado"
    _write_marker(root)
    config = root / ".agents_squad" / "config"
    (config / "sdd-policy.yaml").write_text(
        "policy_version: 1\nsdd:\n  required: true\n", encoding="utf-8"
    )
    resolution = ctx.resolve_sdd_policy(root)
    assert resolution.state == "active"
    assert resolution.errors == ()
    assert resolution.policy["sdd"]["required"] is True
    assert ctx.sdd_required(root) is True


def test_policy_active_but_unreadable_fails_closed(tmp_path):
    ctx = _load_project_context()
    root = tmp_path / "projeto quebrado"
    _write_marker(root)
    config = root / ".agents_squad" / "config"
    (config / "sdd-policy.yaml").write_text(
        "policy_version: [1,\n  sdd: !!seq quebrado\n", encoding="utf-8"
    )
    resolution = ctx.resolve_sdd_policy(root)
    assert resolution.state == "active"
    assert resolution.policy is None
    assert resolution.errors, "política ilegível deve registrar erro"
    assert ctx.sdd_required(root) is True, "falha fechada: ilegível não pode desativar SDD"


def test_policy_active_but_non_dict_fails_closed(tmp_path):
    ctx = _load_project_context()
    root = tmp_path / "projeto lista"
    _write_marker(root)
    config = root / ".agents_squad" / "config"
    (config / "sdd-policy.yaml").write_text("- apenas\n- uma lista\n", encoding="utf-8")
    resolution = ctx.resolve_sdd_policy(root)
    assert resolution.state == "active"
    assert resolution.policy is None
    assert resolution.errors
    assert ctx.sdd_required(root) is True
