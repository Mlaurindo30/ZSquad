"""E2E SDD via CLI (CORR-1) — correção da auditoria pós-entrega Spec Kit.

Cobre os achados P1 da auditoria (findings/spec-kit-post-delivery-audit.md):

- P1#3: o preflight (advance-state) executa ``validate_package`` ANTES de
  ``authorize`` — dúvida bloqueante aberta e lacuna de cobertura bloqueiam o
  avanço na camada do CLI, não só nos testes do adapter.
- P1#1: subcomandos CLI ``sdd init/status/render/run`` com os sete estágios
  Spec Kit (constitution, specify, clarify, plan, tasks, analyze, implement).
- Passo 9 da auditoria: fluxo E2E real via CLI em projeto fixture (tmp),
  sem rede e sem DevOps: init-work-item -> sdd init -> conteúdo -> decide-gate
  G1..G6 -> advance-state até done no ciclo development.

Semântica de hash do autor (governada): editar um documento exige atualizar a
entrada correspondente em sdd/package.json (revision + sha256) — o pacote NUNCA
é autoatualizado pela CLI, preservando a detecção de adulteração (SDD_STALE_GATE).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError, _build_parser, main as squad_main
from tdd_evidence import evidence_digest

PROJECT_ID = "test-sdd-e2e"

SDD_INPUT_FILES = {
    "spec": "spec.md",
    "clarifications": "clarifications.yaml",
    "plan": "plan.md",
    "tasks": "tasks.yaml",
}

SDD_GATE_INPUTS = {
    "G1-product": ("spec", "clarifications", "constitution"),
    "G2-design": ("plan", "constitution"),
    "G3-readiness": ("tasks", "constitution"),
}
SDD_GATE_OWNER = {
    "G1-product": "product-owner",
    "G2-design": "solution-architect",
    "G3-readiness": "delivery-orchestrator",
}

G1_CRITERIA = [
    "blueprint-complete",
    "bdd-specification-valid",
    "data-contracts-defined-when-applicable",
    "timebox-defined",
    "question-stated",
    "finding-documented",
]
G2_CRITERIA = [
    "adr-recorded",
    "interfaces-defined",
    "threat-model-done",
    "test-strategy-defined",
    "blast-radius-defined",
    "deprecation-strategy-defined",
    "migration-plan-defined",
]
G3_CRITERIA = [
    "definition-of-ready",
    "tests-red-exist-and-fail",
    "owners-assigned",
    "dependencies-resolved",
]
G4_CRITERIA = [
    "spec-conformance",
    "tests-green",
    "tdd-cycle-valid",
    "security-executed",
    "clean-code-executed",
    "tests-executed",
]
G5_CRITERIA = ["acceptance-bdd-executed", "regression-executed", "coverage-executed"]
G6_CRITERIA = [
    "traceability-complete",
    "ledger-current",
    "rollout-rollback-ready",
    "rollout-plan-defined",
    "rollback-plan-defined",
    "runbook-updated",
    "change-record-created",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures (padrão de test_sdd_gate_enforcement.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path):
    """Projeto consumidor com política SDD ativada (sdd.required: true)."""
    root = tmp_path / "consumidor"
    config = root / ".agents_squad" / "config"
    config.mkdir(parents=True)
    (config / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "runtime": str(ROOT),
                "project_id": PROJECT_ID,
                "project_name": PROJECT_ID,
                "project_root": str(root),
                "work_dir": str(ROOT / "work" / PROJECT_ID),
                "db_path": str(ROOT / "banco" / "squad.db"),
                "overrides": {},
            }
        ),
        encoding="utf-8",
    )
    (config / "sdd-policy.yaml").write_text(
        yaml.safe_dump({"policy_version": 1, "sdd": {"required": True}}), encoding="utf-8"
    )
    return root


@pytest.fixture()
def legacy_project_root(tmp_path):
    """Projeto consumidor SEM política SDD (modo legado)."""
    root = tmp_path / "legado"
    config = root / ".agents_squad" / "config"
    config.mkdir(parents=True)
    (config / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "runtime": str(ROOT),
                "project_id": PROJECT_ID,
                "project_name": PROJECT_ID,
                "project_root": str(root),
                "work_dir": str(ROOT / "work" / PROJECT_ID),
                "db_path": str(ROOT / "banco" / "squad.db"),
                "overrides": {},
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def squad(project_root):
    """Squad com política SDD ativa e work items isolados em work/test-sdd-e2e."""
    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    try:
        yield AgentSquad(ROOT, project_name=PROJECT_ID, project_root=project_root)
    finally:
        cleanup()


def _run_cli(capsys, project_root, *argv):
    rc = squad_main(["--project-root", str(project_root), *argv])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _ack_cli(capsys, project_root, work_id: str, stage: str, result_ref: str) -> tuple[int, str, str]:
    """Registra o receipt do consumidor persistente antes do stage-complete."""
    return _run_cli(
        capsys, project_root, "sdd", "dispatch-ack", "--work-item", work_id,
        "--stage", stage, "--consumer", "test-consumer", "--result-ref", result_ref,
    )


# ---------------------------------------------------------------------------
# Helpers de conteúdo governado
# ---------------------------------------------------------------------------


def _read_state(item: Path) -> str:
    return yaml.safe_load((item / "status.yaml").read_text(encoding="utf-8"))["state"]


def _materialize_package(item: Path, *, blocking: bool = False, uncovered: bool = False) -> dict[str, str]:
    """Cria pacote SDD completo (como _materialize_sdd de test_sdd_gate_enforcement)."""
    sdd = item / "sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    (item / "reviews").mkdir(parents=True, exist_ok=True)

    if blocking:
        clarifications = (
            "questions:\n"
            "  - id: Q-009\n"
            "    requirement_ids: [REQ-001]\n"
            "    severity: blocking\n"
            "    status: open\n"
            "    question: \"Formato de saída do relatório?\"\n"
        )
    else:
        clarifications = (
            "questions:\n"
            "  - id: Q-001\n"
            "    requirement_ids: [REQ-001]\n"
            "    severity: nonblocking\n"
            "    status: resolved\n"
            "    question: \"Formato?\"\n"
            "    answer: \"Markdown.\"\n"
            "    source: \"ata 2026-09-11\"\n"
        )
    tasks = (
        "tasks:\n"
        "  - id: T-001\n"
        "    requirement_ids: [REQ-001]\n"
        "    acceptance_ids: [AC-001]\n"
        "    owner: backend-engineer\n"
        "    points: 3\n"
        "    depends_on: []\n"
        "    paths: [\"sdd/spec.md\"]\n"
        f"    test_ids: {['tests/test_exemplo.py'] if not uncovered else []}\n"
        "    status: open\n"
    )
    files = {
        sdd / "spec.md": "# Spec\n\nRequisito REQ-001 de exemplo: fluxo ção.\n",
        sdd / "clarifications.yaml": clarifications,
        sdd / "plan.md": "# Plan\n\nPlano de exemplo.\n",
        sdd / "tasks.yaml": tasks,
        sdd / "constitution.md": "# Constituição do projeto\n\nPrincípios.\n",
        item / "reviews" / "rev-gate.md": "# Evidência do gate\n\nConteúdo real.\n",
    }
    for path, content in files.items():
        path.write_bytes(content.encode("utf-8"))

    hashes = {key: _sha256(files[sdd / name].encode("utf-8")) for key, name in SDD_INPUT_FILES.items()}
    hashes["constitution"] = _sha256(files[sdd / "constitution.md"].encode("utf-8"))
    package = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "work_id": item.name,
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": hashes["constitution"],
        "inputs": {
            key: {"path": f"sdd/{name}", "revision": "r1", "sha256": hashes[key]}
            for key, name in SDD_INPUT_FILES.items()
        },
        "requirements": [
            {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"},
        ],
        "reviews": [],
    }
    (sdd / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return hashes


def _write_decision(
    item: Path,
    gate: str,
    hashes: dict[str, str],
    *,
    stored_gate_id: str | None = None,
) -> Path:
    """Grava GD-*.yaml com a estrutura real do decide-gate + vínculos SDD."""
    stored_gate = stored_gate_id or ("GT-design-review" if gate == "G3-readiness" else gate)
    record: dict = {
        "decision_id": f"GD-{item.name}-{stored_gate.upper()}",
        "gate_id": stored_gate,
        "work_item_id": item.name,
        "decision": "approved",
        "decider": SDD_GATE_OWNER[gate],
        "criteria": [{"name": "criterio-exemplo", "result": "pass"}],
        "evidence": ["reviews/rev-gate.md"],
        "human_approval": {
            "required": False,
            "status": "not_required",
            "approved_by": None,
            "evidence": None,
        },
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
        "policy_version": 1,
        "input_hashes": {key: hashes[key] for key in SDD_GATE_INPUTS[gate]},
    }
    path = item / "gate-decisions" / f"{record['decision_id']}.yaml"
    path.write_text(yaml.safe_dump(record), encoding="utf-8")
    return path


def _write_docs(item: Path, *, blocking: bool = False) -> None:
    """Autor governado: edita os documentos e atualiza o manifesto (revision+sha256)."""
    sdd = item / "sdd"
    if blocking:
        clarifications = (
            "questions:\n"
            "  - id: Q-009\n"
            "    requirement_ids: [REQ-001]\n"
            "    severity: blocking\n"
            "    status: open\n"
            "    question: \"Formato de saída?\"\n"
        )
    else:
        clarifications = (
            "questions:\n"
            "  - id: Q-001\n"
            "    requirement_ids: [REQ-001]\n"
            "    severity: nonblocking\n"
            "    status: resolved\n"
            "    question: \"Formato?\"\n"
            "    answer: \"Markdown.\"\n"
            "    source: \"ata 2026-09-11\"\n"
        )
    files = {
        "constitution.md": "# Constituição do projeto\n\nPrincípios fundamentais de governança.\n",
        "spec.md": "# Spec — fluxo E2E\n\nREQ-001: o fluxo CLI SDD opera ponta a ponta.\n",
        "clarifications.yaml": clarifications,
        "plan.md": "# Plan — fluxo E2E\n\nPlano governado do ciclo.\n",
        "tasks.yaml": (
            "tasks:\n"
            "  - id: T-001\n"
            "    requirement_ids: [REQ-001]\n"
            "    acceptance_ids: [AC-001]\n"
            "    owner: backend-engineer\n"
            "    points: 3\n"
            "    depends_on: []\n"
            "    paths: [\"implementation.py\"]\n"
            "    test_ids: [\"tests/test_e2e.py\"]\n"
            "    status: open\n"
        ),
    }
    for name, content in files.items():
        (sdd / name).write_bytes(content.encode("utf-8"))
    # Evidência de gate usada pelas decisões (precisa existir dentro do item).
    (item / "reviews").mkdir(parents=True, exist_ok=True)
    (item / "reviews" / "rev-gate.md").write_bytes(
        "# Evidência do gate\n\nConteúdo real.\n".encode("utf-8")
    )
    package = json.loads((sdd / "package.json").read_text(encoding="utf-8"))
    package["constitution_sha256"] = _sha256(files["constitution.md"].encode("utf-8"))
    for key, name in SDD_INPUT_FILES.items():
        data = (sdd / name).read_bytes()
        package["inputs"][key] = {
            "path": f"sdd/{name}",
            "revision": "r1",
            "sha256": _sha256(data),
        }
    package["requirements"] = [
        {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"},
    ]
    (sdd / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_bdd(item: Path) -> None:
    features = item / "specs" / "features"
    features.mkdir(parents=True, exist_ok=True)
    (features / "e2e.feature").write_text(
        "Feature: fluxo E2E SDD\n"
        "  Scenario: ciclo completo via CLI\n"
        "    Given um work item com pacote SDD\n"
        "    When os gates são aprovados\n"
        "    Then o item chega em done\n"
        "    @AC-001\n",
        encoding="utf-8",
    )
    (item / "epic.md").write_text(
        "# Objetivo\n\nProblema: reduzir esforço manual. Contrato de dados: schema versionado. "
        "Timebox: 5 dias. Pergunta: como automatizar? Achado e objetivo documentados.\n",
        encoding="utf-8",
    )


def _write_execution_evidence(item: Path) -> None:
    """Evidências executáveis (verification-evidence.schema.json) + ciclo TDD."""
    (item / "reviews" / "rev-gate.md").parent.mkdir(parents=True, exist_ok=True)
    if not (item / "reviews" / "rev-gate.md").exists():
        (item / "reviews" / "rev-gate.md").write_bytes("# Evidência do gate\n".encode("utf-8"))
    hashed = item / "reviews" / "rev-gate.md"
    relative = hashed.relative_to(ROOT).as_posix()
    digest = _sha256(hashed.read_bytes())
    evaluation = item / "evaluation"
    evaluation.mkdir(parents=True, exist_ok=True)
    for verifier in ("clean-code", "unit-tests", "security", "bdd", "regression", "coverage"):
        payload = {
            "schema_version": 1,
            "work_item": item.name,
            "verifier": verifier,
            "persona": "test-engineer",
            "command": [sys.executable, "-m", "pytest"],
            "passed": True,
            "exit_code": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_hashes": {relative: digest},
            "results": (
                {
                    "status": "PASS",
                    "minimum_percent": 80,
                    "total_percent": 90,
                    "branch_percent": 85,
                }
                if verifier == "coverage"
                else {"status": "PASS"}
            ),
        }
        (evaluation / f"{verifier}.json").write_text(json.dumps(payload), encoding="utf-8")
    tdd_dir = evaluation / "tdd"
    tdd_dir.mkdir(parents=True, exist_ok=True)
    red = {"passed": False, "exit_code": 1, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-001"}}
    green = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-001", "previous_digest": evidence_digest(red)}}
    refactor = {"passed": True, "exit_code": 0, "file_hashes": {}, "results": {"test_id": "t", "criterion_id": "AC-001", "previous_digest": evidence_digest(green)}}
    for stage, value in (("red", red), ("green", green), ("refactor", refactor)):
        (tdd_dir / f"{stage}.json").write_text(json.dumps(value), encoding="utf-8")


# ---------------------------------------------------------------------------
# P1#3 — preflight executa validate_package antes de authorize
# ---------------------------------------------------------------------------


def test_open_blocking_question_blocks_advance_state(squad):
    """Pacote com dúvida bloqueante aberta + G1+G2 aprovados: o advance-state
    bloqueia com SDD_OPEN_QUESTION (antes da correção só o adapter pegava)."""
    item = squad.init_work_item("US-CORR-BQ-01", "low")
    hashes = _materialize_package(item, blocking=True)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_OPEN_QUESTION" in str(exc.value)
    assert "Q-009" in str(exc.value)
    assert _read_state(item) == "blueprint"


def test_coverage_gap_blocks_advance_state(squad):
    """Requisito de código sem tarefa com teste: SDD_COVERAGE_GAP bloqueia o
    avanço na camada do CLI (regra antes restrita a validate_package)."""
    item = squad.init_work_item("US-CORR-COV-01", "low")
    hashes = _materialize_package(item, uncovered=True)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_COVERAGE_GAP" in str(exc.value)
    assert _read_state(item) == "blueprint"


def test_structural_validity_keeps_positive_path(squad):
    """Pacote estruturalmente válido continua avançando (G1+G2 concluem blueprint)."""
    item = squad.init_work_item("US-CORR-OK-01", "low")
    hashes = _materialize_package(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)
    result = squad.advance_state(item)
    assert result["state"] == "scaffolding"


# ---------------------------------------------------------------------------
# P1#1 — família CLI `sdd`
# ---------------------------------------------------------------------------


def test_sdd_parser_subcommands():
    parsed = _build_parser().parse_args(["sdd", "init", "--work-item", "US-X"])
    assert (parsed.command, parsed.sdd_command, parsed.work_item) == ("sdd", "init", "US-X")
    parsed = _build_parser().parse_args(
        ["sdd", "run", "--work-item", "US-X", "--stage", "implement"]
    )
    assert parsed.stage == "implement"
    parsed = _build_parser().parse_args(
        ["sdd", "init", "--work-item", "US-X", "--constitution", "const.md", "--force"]
    )
    assert parsed.force is True


def test_sdd_init_creates_valid_skeleton(squad, project_root, capsys):
    squad.init_work_item("US-CORR-INIT-01", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-INIT-01")
    assert rc == 0, err
    item = ROOT / "work" / PROJECT_ID / "US-CORR-INIT-01"
    for name in ("package.json", "spec.md", "clarifications.yaml", "plan.md", "tasks.yaml", "constitution.md"):
        assert (item / "sdd" / name).is_file(), name
    package = json.loads((item / "sdd" / "package.json").read_text(encoding="utf-8"))
    assert package["schema_version"] == 1
    assert package["work_id"] == "US-CORR-INIT-01"
    assert package["requirements"] == []
    assert package["reviews"] == []
    for key, name in SDD_INPUT_FILES.items():
        data = (item / "sdd" / name).read_bytes()
        assert package["inputs"][key]["sha256"] == _sha256(data), key
    assert package["constitution_sha256"] == _sha256((item / "sdd" / "constitution.md").read_bytes())

    # Pacote recém-criado valida estruturalmente no estágio planning.
    import importlib.util

    base = ROOT / "integrations" / "spec-kit" / "adapter"
    spec = importlib.util.spec_from_file_location(
        "sdd_adapter_e2e", base / "__init__.py", submodule_search_locations=[str(base)]
    )
    adapter = importlib.util.module_from_spec(spec)
    sys.modules["sdd_adapter_e2e"] = adapter
    spec.loader.exec_module(adapter)
    loaded = adapter.load_package(item)
    assert adapter.validate_package(loaded, "planning") == []


def test_sdd_init_refuses_duplicate_and_force_recreates(squad, project_root, capsys):
    squad.init_work_item("US-CORR-DUP-01", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-DUP-01")
    assert rc == 0, err
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-DUP-01")
    assert rc == 2
    assert "já existe" in err
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-DUP-01", "--force")
    assert rc == 0, err
    assert "WARN" in out


def test_sdd_init_adopts_constitution_file(squad, project_root, capsys, tmp_path):
    source = tmp_path / "constituicao.md"
    source.write_bytes("# Constituição do consumidor\n\nRegra 1.\n".encode("utf-8"))
    squad.init_work_item("US-CORR-CONST-01", "low")
    rc, out, err = _run_cli(
        capsys, project_root,
        "sdd", "init", "--work-item", "US-CORR-CONST-01", "--constitution", str(source),
    )
    assert rc == 0, err
    item = ROOT / "work" / PROJECT_ID / "US-CORR-CONST-01"
    adopted = (item / "sdd" / "constitution.md").read_bytes()
    assert adopted == source.read_bytes()
    package = json.loads((item / "sdd" / "package.json").read_text(encoding="utf-8"))
    assert package["constitution_sha256"] == _sha256(adopted)


def test_sdd_status_reports_validity_questions_decisions_staleness(squad, project_root, capsys):
    squad.init_work_item("US-CORR-ST-01", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-ST-01")
    assert rc == 0, err
    rc, out, err = _run_cli(capsys, project_root, "sdd", "status", "--work-item", "US-CORR-ST-01")
    assert rc == 0, err
    report = json.loads(out)
    assert report["policy"] == "active"
    assert report["package_present"] is True
    # Esqueleto é estruturalmente válido, mas ainda não autorizado (sem gates).
    assert report["valid"] is True
    assert report["authorized"] is False
    assert report["human"], "status precisa de texto humano"
    # Manifesto placeholder casa com os arquivos: sem staleness.
    assert report["staleness"] == []

    _write_docs(ROOT / "work" / PROJECT_ID / "US-CORR-ST-01", blocking=True)
    rc, out, err = _run_cli(capsys, project_root, "sdd", "status", "--work-item", "US-CORR-ST-01")
    report = json.loads(out)
    assert report["valid"] is False
    assert any(
        error["code"] == "SDD_OPEN_QUESTION" for error in report["open_blocking_questions"]
    )


def test_sdd_init_and_status_work_in_legacy_project(legacy_project_root, capsys):
    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID
    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        squad = AgentSquad(ROOT, project_name=PROJECT_ID, project_root=legacy_project_root)
        squad.init_work_item("US-CORR-LEG-01", "low")
        # init é o caminho de adoção: permitido em projeto legado.
        rc, out, err = _run_cli(capsys, legacy_project_root, "sdd", "init", "--work-item", "US-CORR-LEG-01")
        assert rc == 0, err
        rc, out, err = _run_cli(capsys, legacy_project_root, "sdd", "status", "--work-item", "US-CORR-LEG-01")
        assert rc == 0, err
        report = json.loads(out)
        assert report["policy"] == "legacy"
        assert report["package_present"] is True
        assert report["valid"] is True
        assert report["authorized"] is None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_sdd_render_requires_gate_evidence(squad, project_root, capsys):
    squad.init_work_item("US-CORR-RD-01", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RD-01")
    assert rc == 0, err
    _write_docs(ROOT / "work" / PROJECT_ID / "US-CORR-RD-01")
    # 'plan' exige evidência de G1 (contrato REQUIRED_CONTEXT): fail-closed.
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "render", "--work-item", "US-CORR-RD-01", "--stage", "plan"
    )
    assert rc == 2
    assert "g1_evidence" in err


def test_sdd_render_emits_governed_briefing(squad, project_root, capsys):
    item = squad.init_work_item("US-CORR-RD-02", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RD-02")
    assert rc == 0, err
    _write_docs(item)
    _write_bdd(item)
    squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(name, "pass") for name in G1_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "render", "--work-item", "US-CORR-RD-02", "--stage", "plan"
    )
    assert rc == 0, err
    assert "Briefing de agente" in out
    assert "solution-architect" in out  # persona de 'plan' (STAGE_PERSONA)


def test_sdd_run_blocks_on_structural_error(squad, project_root, capsys):
    item = squad.init_work_item("US-CORR-RUN-01", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RUN-01")
    assert rc == 0, err
    _write_docs(item, blocking=False)
    for stage in ("constitution", "specify"):
        rc_r, out_r, err_r = _run_cli(
            capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-01", "--stage", stage
        )
        assert rc_r == 0, err_r
        result_ref = {"constitution": "sdd/constitution.md", "specify": "sdd/spec.md"}[stage]
        rc_a, out_a, err_a = _ack_cli(capsys, project_root, "US-CORR-RUN-01", stage, result_ref)
        assert rc_a == 0, err_a
        rc, out, err = _run_cli(
            capsys, project_root, "sdd", "stage-complete", "--work-item", "US-CORR-RUN-01", "--stage", stage
        )
        assert rc == 0, err
    # Agora injeta dúvida bloqueante aberta para o estágio clarify falhar no preflight
    _write_docs(item, blocking=True)
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-01", "--stage", "clarify"
    )
    assert rc == 2
    assert "SDD_OPEN_QUESTION" in err


def test_sdd_run_emits_briefing_and_next_gate(squad, project_root, capsys):
    item = squad.init_work_item("US-CORR-RUN-02", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RUN-02")
    assert rc == 0, err
    _write_docs(item)
    _write_bdd(item)
    squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(name, "pass") for name in G1_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    for stage in ("constitution", "specify", "clarify"):
        rc_r, out_r, err_r = _run_cli(
            capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-02", "--stage", stage
        )
        assert rc_r == 0, err_r
        result_ref = {"constitution": "sdd/constitution.md", "specify": "sdd/spec.md", "clarify": "sdd/clarifications.yaml"}[stage]
        rc_a, out_a, err_a = _ack_cli(capsys, project_root, "US-CORR-RUN-02", stage, result_ref)
        assert rc_a == 0, err_a
        rc, out, err = _run_cli(
            capsys, project_root, "sdd", "stage-complete", "--work-item", "US-CORR-RUN-02", "--stage", stage
        )
        assert rc == 0, err
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-02", "--stage", "plan"
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["stage"] == "plan"
    assert payload["persona"] == "solution-architect"
    assert payload["next_gate"] == "G2-design"
    assert "Briefing de agente" in payload["briefing"]


def test_sdd_run_unknown_stage_fails_closed(squad, project_root, capsys):
    squad.init_work_item("US-CORR-RUN-03", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RUN-03")
    assert rc == 0, err
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-03", "--stage", "deploy"
    )
    assert rc == 2
    for stage in ("constitution", "specify", "clarify", "plan", "tasks", "analyze", "implement"):
        assert stage in err


def test_sdd_run_implement_requires_full_preflight(squad, project_root, capsys):
    item = squad.init_work_item("US-CORR-RUN-04", "low")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", "US-CORR-RUN-04")
    assert rc == 0, err
    _write_docs(item)
    _write_bdd(item)
    squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(name, "pass") for name in G1_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    for stage in ("constitution", "specify", "clarify"):
        rc_r, out_r, err_r = _run_cli(
            capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-04", "--stage", stage
        )
        assert rc_r == 0, err_r
        result_ref = {"constitution": "sdd/constitution.md", "specify": "sdd/spec.md", "clarify": "sdd/clarifications.yaml"}[stage]
        rc_a, out_a, err_a = _ack_cli(capsys, project_root, "US-CORR-RUN-04", stage, result_ref)
        assert rc_a == 0, err_a
        rc, out, err = _run_cli(
            capsys, project_root, "sdd", "stage-complete", "--work-item", "US-CORR-RUN-04", "--stage", stage
        )
        assert rc == 0, err

    rc_r, out_r, err_r = _run_cli(
        capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-04", "--stage", "plan"
    )
    assert rc_r == 0, err_r
    rc_a, out_a, err_a = _ack_cli(capsys, project_root, "US-CORR-RUN-04", "plan", "sdd/plan.md")
    assert rc_a == 0, err_a
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "stage-complete", "--work-item", "US-CORR-RUN-04", "--stage", "plan"
    )
    assert rc == 0, err

    # Sem G2-design aprovado, tasks falha o preflight
    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "run", "--work-item", "US-CORR-RUN-04", "--stage", "tasks"
    )
    assert rc == 2
    assert "SDD_MISSING_INPUT" in err
    assert "G2-design" in err


# ---------------------------------------------------------------------------
# Passo 9 da auditoria — E2E real via CLI (ciclo development, fixture tmp)
# ---------------------------------------------------------------------------


def test_e2e_cli_development_cycle_to_done(squad, project_root, capsys):
    work_id = "TASK-E2E-01"
    rc, out, err = _run_cli(
        capsys, project_root, "init-work-item", "--id", work_id, "--risk", "low"
    )
    assert rc == 0, err
    item = ROOT / "work" / PROJECT_ID / work_id

    # 1. sdd init cria o pacote esqueleto.
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", work_id)
    assert rc == 0, err

    # 2. Sem decisões de gate: avanço bloqueado (fail-closed).
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "SDD_MISSING_INPUT" in err
    assert _read_state(item) == "blueprint"

    # 3. Autor preenche os documentos e atualiza o manifesto (revision+sha256).
    _write_docs(item)

    def _decide(gate, decider, criteria, extra=()):
        argv = [
            "decide-gate",
            "--work-item", work_id,
            "--gate", gate,
            "--decider", decider,
            "--criteria", *[f"{name}=pass" for name in criteria],
            "--evidence", "reviews/rev-gate.md",
            *extra,
        ]
        rc, out, err = _run_cli(capsys, project_root, *argv)
        assert rc == 0, f"{gate}: {err}"

    author_reviewer = ["--author", "requirements-analyst", "--reviewer", "security-reviewer"]

    # 4. G1 sem G2: blueprint não conclui.
    _write_bdd(item)
    _decide("G1-product", "product-owner", G1_CRITERIA, author_reviewer)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "G2-design" in err
    assert _read_state(item) == "blueprint"

    # 5. G2 aprova: blueprint -> scaffolding.
    _decide("G2-design", "solution-architect", G2_CRITERIA, author_reviewer)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "scaffolding"

    # 6. Sem G3: implementação bloqueada; com G3: scaffolding -> implementation.
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "G3-readiness" in err
    _decide("G3-readiness", "delivery-orchestrator", G3_CRITERIA, author_reviewer)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "implementation"

    # 7. implementation -> code-security-review (sem gate associado ao estado).
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "code-security-review"

    # 8. Sem G4: saída de code-security-review bloqueada; evidências
    #    executáveis + G4 liberam para quality-validation.
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "G4-code-security" in err
    _write_execution_evidence(item)
    _decide("G4-code-security", "code-reviewer", G4_CRITERIA)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "quality-validation"

    # 9. Sem G5: saída de quality-validation bloqueada; G5 libera.
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "G5-quality" in err
    _decide("G5-quality", "qa-engineer", G5_CRITERIA)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "governance-release"

    # 10. Sem G6: saída de governance-release bloqueada; G6 encerra em done.
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 2
    assert "G6-governance-release" in err
    _decide("G6-governance-release", "governance-auditor", G6_CRITERIA)
    rc, out, err = _run_cli(capsys, project_root, "advance-state", "--work-item", work_id)
    assert rc == 0, err
    assert _read_state(item) == "done"


def test_sdd_e2e_seven_stages_full_flow(squad, project_root, capsys):
    """CORR-3: E2E percorrendo os 7 estágios Spec Kit integralmente pela CLI.

    Fluxo ponta a ponta:
    1. constitution: despacho requirements-analyst + stage-complete
    2. specify: preenchimento de spec.md + sdd run + stage-complete
    3. clarify: esclarecimento de dúvidas em clarifications.yaml + sdd run + stage-complete
    4. G1 decidido -> plan: elaboração de plan.md + sdd run + stage-complete
    5. G2 decidido -> tasks: definição de tasks.yaml + sdd run + stage-complete
    6. analyze: validação de coerência e rastreabilidade + sdd run + stage-complete
    7. G3 decidido -> implement: despacho software-engineer + stage-complete
    8. Validação de stages.yaml com todos os 7 estágios completados e rastreáveis.
    """
    work_id = "TASK-SDD-E2E-7STAGES"
    rc, out, err = _run_cli(capsys, project_root, "init-work-item", "--id", work_id, "--risk", "low")
    assert rc == 0, err
    item = ROOT / "work" / PROJECT_ID / work_id

    # 0. Inicialização do pacote SDD
    rc, out, err = _run_cli(capsys, project_root, "sdd", "init", "--work-item", work_id)
    assert rc == 0, err

    # Verificação de ordem: tentar pular constitution para specify deve falhar fechado
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "specify")
    assert rc == 2
    assert "SDD_OUT_OF_ORDER" in err

    # 1. constitution
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "constitution")
    assert rc == 0, err
    p1 = json.loads(out)
    assert p1["stage"] == "constitution"
    assert p1["persona"] == "requirements-analyst"
    assert p1["dispatch"]["queued"] is True
    assert "Briefing de agente" in p1["briefing"]

    # Persona elabora a constituição real
    const_file = item / "sdd" / "constitution.md"
    const_file.write_text("# Constituição do Projeto\n\nRegras invioláveis de engenharia.\n", encoding="utf-8")
    rc, out, err = _ack_cli(capsys, project_root, work_id, "constitution", "sdd/constitution.md")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "constitution")
    assert rc == 0, err

    # 2. specify (persona elabora spec.md com requisitos e sincroniza package.json)
    spec_file = item / "sdd" / "spec.md"
    spec_file.write_text("# Especificação E2E\n\nREQ-001: Implementar fluxo SDD 7 estágios.\n", encoding="utf-8")
    pkg_file = item / "sdd" / "package.json"
    pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    pkg["requirements"] = [{"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"}]
    pkg_file.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
        # Tarefa inicial para satisfazer cobertura e declarar a saída exata.
    tasks_file = item / "sdd" / "tasks.yaml"
    tasks_file.write_text(
        "tasks:\n"
        "  - id: T-001\n"
        "    requirement_ids: [REQ-001]\n"
        "    acceptance_ids: [AC-001]\n"
        "    owner: software-engineer\n"
        "    points: 3\n"
        "    depends_on: []\n"
            "    paths: [\"implementation.py\"]\n"
        "    test_ids: [\"tests/test_e2e.py\"]\n"
        "    status: open\n",
        encoding="utf-8",
    )

    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "specify")
    assert rc == 0, err
    p2 = json.loads(out)
    assert p2["stage"] == "specify"
    assert p2["persona"] == "requirements-analyst"
    rc, out, err = _ack_cli(capsys, project_root, work_id, "specify", "sdd/spec.md")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "specify")
    assert rc == 0, err

    # 3. clarify (persona esclarece dúvidas bloqueantes)
    clar_file = item / "sdd" / "clarifications.yaml"
    clar_file.write_text(
        "questions:\n"
        "  - id: Q-001\n"
        "    requirement_ids: [REQ-001]\n"
        "    severity: nonblocking\n"
        "    status: resolved\n"
        "    question: 'Escopo dos 7 estágios?'\n"
        "    answer: 'Execução e autorização sequencial governada.'\n"
        "    source: 'revisão técnica'\n",
        encoding="utf-8",
    )
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "clarify")
    assert rc == 0, err
    p3 = json.loads(out)
    assert p3["stage"] == "clarify"
    assert p3["next_gate"] == "G1-product"
    rc, out, err = _ack_cli(capsys, project_root, work_id, "clarify", "sdd/clarifications.yaml")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "clarify")
    assert rc == 0, err

    # Gate G1-product (necessário para autorizar o estágio plan)
    _write_bdd(item)
    (item / "reviews").mkdir(parents=True, exist_ok=True)
    (item / "reviews" / "rev-gate.md").write_text("# Review Gate\n\nAprovado.\n", encoding="utf-8")
    squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(n, "pass") for n in G1_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )

    # 4. plan
    plan_file = item / "sdd" / "plan.md"
    plan_file.write_text("# Plano de Arquitetura E2E\n\nDefinição técnica dos 7 estágios.\n", encoding="utf-8")
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "plan")
    assert rc == 0, err
    p4 = json.loads(out)
    assert p4["stage"] == "plan"
    assert p4["persona"] == "solution-architect"
    assert p4["next_gate"] == "G2-design"
    rc, out, err = _ack_cli(capsys, project_root, work_id, "plan", "sdd/plan.md")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "plan")
    assert rc == 0, err

    # Gate G2-design (necessário para autorizar tasks e analyze)
    squad.decide_gate(
        item,
        "G2-design",
        "solution-architect",
        [(n, "pass") for n in G2_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )

    # 5. tasks
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "tasks")
    assert rc == 0, err
    p5 = json.loads(out)
    assert p5["stage"] == "tasks"
    assert p5["persona"] == "delivery-orchestrator"
    rc, out, err = _ack_cli(capsys, project_root, work_id, "tasks", "sdd/tasks.yaml")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "tasks")
    assert rc == 0, err

    # 6. analyze
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "analyze")
    assert rc == 0, err
    p6 = json.loads(out)
    assert p6["stage"] == "analyze"
    assert p6["persona"] == "delivery-orchestrator"

    # Persona elabora relatório material de análise de consistência
    analysis_file = item / "sdd" / "analysis.md"
    analysis_file.write_text("# Análise de Coerência\n\nConsistência cruzada verificada 100%.\n", encoding="utf-8")
    rc, out, err = _ack_cli(capsys, project_root, work_id, "analyze", "sdd/analysis.md")
    assert rc == 0, err

    rc, out, err = _run_cli(capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "analyze")
    assert rc == 0, err

    # Gate G3-readiness (necessário para autorizar implement)
    squad.decide_gate(
        item,
        "G3-readiness",
        "delivery-orchestrator",
        [(n, "pass") for n in G3_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )

    # 7. implement
    rc, out, err = _run_cli(capsys, project_root, "sdd", "run", "--work-item", work_id, "--stage", "implement")
    assert rc == 0, err
    p7 = json.loads(out)
    assert p7["stage"] == "implement"
    assert p7["persona"] == "software-engineer"
    assert p7["next_gate"] == "G4-code-security"

    # Persona elabora artefato de implementação de código
    impl_code = item / "implementation.py"
    impl_code.write_text("print('implementação 7 estágios concluída com sucesso')\n", encoding="utf-8")
    rc, out, err = _ack_cli(capsys, project_root, work_id, "implement", "implementation.py")
    assert rc == 0, err

    rc, out, err = _run_cli(
        capsys, project_root, "sdd", "stage-complete", "--work-item", work_id, "--stage", "implement", "--output-refs", "implementation.py"
    )
    assert rc == 0, err

    # 8. Verificação final do status de todos os 7 estágios
    rc, out, err = _run_cli(capsys, project_root, "sdd", "status", "--work-item", work_id)
    assert rc == 0, err

    stages_yaml = item / "sdd" / "stages.yaml"
    stages_data = yaml.safe_load(stages_yaml.read_text(encoding="utf-8"))
    for st in ("constitution", "specify", "clarify", "plan", "tasks", "analyze", "implement"):
        entry = stages_data["stages"][st]
        assert entry["status"] == "completed", f"Estágio {st} deveria estar completed"
        assert entry["dispatched_at"] is not None
        assert entry["completed_at"] is not None
        assert entry["briefing_sha256"] is not None
        assert len(entry["briefing_sha256"]) == 64
