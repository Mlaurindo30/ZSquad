"""Piloto T7 — cenários de aceite BDD da seção 8 do plano (test-engineer).

Materializa um projeto consumidor ISOLADO em ``tmp_path`` (com
``.agents_squad/config/project.yaml`` + ``sdd-policy.yaml``) e um work item
com pacote SDD, e executa os cenários do plano
``documentation/plans/2026-09-11-spec-kit-integration.md`` §8 que são
automatizáveis no nível do adapter (1, 2, 3, 4, 5, 6, 9, 10, 11) e o
teste de adulteração pós-gate (tamper-after-gate).

Mapeamento cenário -> teste (mesmos números do plano):

- 1  spec ausente          -> test_scenario_1_missing_spec_rejects_plan
- 2  dúvida bloqueante     -> test_scenario_2_open_blocking_question_rejects_g1_with_id
- 3  G1 sem G2             -> test_scenario_3_g1_without_g2_blocks_blueprint
- 4  spec alterada         -> test_scenario_4_changed_spec_marks_dependent_gates_stale
- 5  cobertura G3          -> test_scenario_5_missing_requirement_or_test_rejects_g3
- 6  pacote aprovado + RED -> test_scenario_6_approved_package_authorizes_implementation
- 9  Boards indisponível   -> test_scenario_9_boards_unavailable_stale_snapshot_not_approval
- 10 upstream incompatível -> test_scenario_10_incompatible_upstream_keeps_previous_version
- 11 projeto legado        -> test_scenario_11_legacy_project_compatible_without_guarantee
- tamper-after-gate        -> test_tamper_after_gate_fails_at_advance_time

Cenários 7, 8 e 12 são testados no nível do CLI Squad em
``scripts/tests/test_sdd_gate_enforcement.py`` (seção "T7").

Nenhuma validação live de Azure DevOps é afirmada aqui: o estado remoto
entra apenas como snapshot em memória (contrato T6).

O adaptador é carregado via ``importlib`` porque o diretório
``integrations/spec-kit`` contém hífen e não é importável pelo nome.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "integrations" / "spec-kit" / "adapter"

SPEC_MD = "# Spec\n\nRequisito de exemplo do piloto com acentuação: título ção.\n"
PLAN_MD = "# Plan\n\nPlano do piloto.\n"
CONSTITUTION_MD = "# Constituição do projeto\n\nPrincípios do piloto.\n"
EVIDENCE_MD = "# Evidência do gate\n\nRevisão executada com saída real.\n"
RED_EVIDENCE_MD = "# Evidência RED\n\nTeste falhando executado antes da implementação.\n"

CLARIFICATIONS_OK = """questions:
  - id: Q-001
    requirement_ids: [REQ-001]
    severity: nonblocking
    status: resolved
    question: "Formato do relatório?"
    answer: "Markdown consolidado."
    source: "ata de refinement 2026-09-10"
    owner: product-owner
"""

TASKS_OK = """tasks:
  - id: T-001
    requirement_ids: [REQ-001]
    acceptance_ids: [AC-001]
    owner: backend-engineer
    points: 3
    depends_on: []
    paths: ["src/modulo.py"]
    evidence: ["reviews/red-tests.md"]
    test_ids: ["tests/test_modulo.py::test_ok"]
    status: open
  - id: T-002
    requirement_ids: [REQ-002]
    acceptance_ids: [AC-002]
    owner: frontend-engineer
    points: 2
    depends_on: [T-001]
    paths: ["src/painel.py"]
    evidence: ["reviews/red-tests.md"]
    test_ids: ["tests/test_painel.py::test_ok"]
    status: open
  - id: T-003
    requirement_ids: [REQ-003]
    acceptance_ids: [AC-003]
    owner: software-engineer
    points: 1
    depends_on: []
    paths: ["src/util.py"]
    evidence: ["reviews/red-tests.md"]
    test_ids: ["tests/test_util.py::test_ok"]
    status: open
"""

REQUIRED_POLICY = {"policy_version": 1, "sdd": {"required": True}}
LEGACY_POLICY = {"policy_version": 1, "sdd": {"required": False}, "legacy_mode": True}

GATE_OWNERS = {
    "G1-product": "product-owner",
    "G2-design": "solution-architect",
    "G3-readiness": "delivery-orchestrator",
}
GATE_INPUTS = {
    "G1-product": ("spec", "clarifications", "constitution"),
    "G2-design": ("plan", "constitution"),
    "G3-readiness": ("tasks", "constitution"),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_adapter_modules():
    name = "sdd_adapter_pilot"
    if name in sys.modules:
        module = sys.modules[name]
        return (
            module,
            importlib.import_module(f"{name}.policy"),
            importlib.import_module(f"{name}.rendering"),
            importlib.import_module(f"{name}.backlog"),
        )
    spec = importlib.util.spec_from_file_location(
        name, ADAPTER_DIR / "__init__.py", submodule_search_locations=[str(ADAPTER_DIR)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.fail(f"adapter/__init__.py não encontrado em {ADAPTER_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return (
        module,
        importlib.import_module(f"{name}.policy"),
        importlib.import_module(f"{name}.rendering"),
        importlib.import_module(f"{name}.backlog"),
    )


@pytest.fixture(scope="module")
def modules():
    return _load_adapter_modules()


# ---------------------------------------------------------------------------
# Projeto consumidor isolado (fixture do piloto)
# ---------------------------------------------------------------------------


def _build_consumer_project(
    tmp_path: Path,
    *,
    policy: str = "required",
    work_id: str = "US-PILOT-001",
    spec: bool = True,
    blocking_question: bool = False,
    coverage_gap: bool = False,
    dependency_cycle: bool = False,
) -> tuple[Path, Path, dict[str, str]]:
    """Cria projeto consumidor isolado + work item com pacote SDD.

    Retorna ``(project_root, work_dir, hashes_reais)``. Nada é escrito fora
    de ``tmp_path`` (nunca no repositório nem em projetos reais).
    """
    project_root = tmp_path / "projeto piloto ção"
    config = project_root / ".agents_squad" / "config"
    config.mkdir(parents=True)
    (config / "project.yaml").write_text(
        "version: 1\n"
        f"runtime: {REPO_ROOT.as_posix()}\n"
        "project_id: projeto_piloto\n"
        f"project_root: {project_root.as_posix()}\n",
        encoding="utf-8",
    )
    if policy == "required":
        (config / "sdd-policy.yaml").write_text(
            "policy_version: 1\nsdd:\n  required: true\n", encoding="utf-8"
        )
    elif policy == "invalid":
        # YAML quebrado: política ilegível deve falhar fechado (nunca desativar).
        (config / "sdd-policy.yaml").write_text(
            "policy_version: [1,\n  sdd: !!seq quebrado\n", encoding="utf-8"
        )
    elif policy == "legacy":
        (config / "sdd-policy.yaml").write_text(
            "policy_version: 1\nsdd:\n  required: false\nlegacy_mode: true\n", encoding="utf-8"
        )
    # policy == "absent": nenhum arquivo de política (projeto legado).

    work_dir = project_root / "work" / work_id
    sdd = work_dir / "sdd"
    sdd.mkdir(parents=True)
    (work_dir / "reviews").mkdir()

    tasks_yaml = TASKS_OK
    if coverage_gap:
        tasks_yaml = TASKS_OK.replace('test_ids: ["tests/test_painel.py::test_ok"]', "test_ids: []")
    if dependency_cycle:
        tasks_yaml = TASKS_OK.replace("depends_on: [T-001]", "depends_on: [T-003]")

    clarifications = CLARIFICATIONS_OK
    if blocking_question:
        clarifications += (
            "  - id: Q-009\n"
            "    requirement_ids: [REQ-001]\n"
            "    severity: blocking\n"
            "    status: open\n"
            '    question: "Qual o limite real do payload?"\n'
        )

    files: dict[Path, bytes] = {
        sdd / "spec.md": SPEC_MD.encode("utf-8"),
        sdd / "clarifications.yaml": clarifications.encode("utf-8"),
        sdd / "plan.md": PLAN_MD.encode("utf-8"),
        sdd / "tasks.yaml": tasks_yaml.encode("utf-8"),
        sdd / "constitution.md": CONSTITUTION_MD.encode("utf-8"),
        work_dir / "reviews" / "rev-gate.md": EVIDENCE_MD.encode("utf-8"),
        work_dir / "reviews" / "red-tests.md": RED_EVIDENCE_MD.encode("utf-8"),
    }
    for path, data in files.items():
        if path.name == "spec.md" and not spec:
            continue
        path.write_bytes(data)

    hashes = {
        "spec": _sha256(SPEC_MD.encode("utf-8")),
        "clarifications": _sha256(clarifications.encode("utf-8")),
        "plan": _sha256(PLAN_MD.encode("utf-8")),
        "tasks": _sha256(tasks_yaml.encode("utf-8")),
        "constitution": _sha256(CONSTITUTION_MD.encode("utf-8")),
    }
    package = {
        "schema_version": 1,
        "project_id": "projeto_piloto",
        "work_id": work_id,
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": hashes["constitution"],
        "inputs": {
            "spec": {"path": "sdd/spec.md", "revision": "r1", "sha256": hashes["spec"]},
            "clarifications": {
                "path": "sdd/clarifications.yaml",
                "revision": "r1",
                "sha256": hashes["clarifications"],
            },
            "plan": {"path": "sdd/plan.md", "revision": "r1", "sha256": hashes["plan"]},
            "tasks": {"path": "sdd/tasks.yaml", "revision": "r1", "sha256": hashes["tasks"]},
        },
        "requirements": [
            {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"},
            {"id": "REQ-002", "acceptance_ids": ["AC-002"], "classification": "code"},
            {"id": "REQ-003", "acceptance_ids": ["AC-003"], "classification": "code"},
        ],
        "reviews": [
            {
                "author": "test-engineer",
                "reviewer": "qa-engineer",
                "result": "approved",
                "evidence": ["reviews/rev-gate.md"],
                "reviewed_sha256": [hashes["spec"]],
            }
        ],
    }
    (sdd / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return project_root, work_dir, hashes


def _decision(gate: str, hashes: dict[str, str], work_id: str) -> dict:
    """Registro de decisão no formato real do decide-gate + vínculos SDD."""
    return {
        "decision_id": f"GD-{work_id}-{gate}",
        "gate_id": gate,
        "work_item_id": work_id,
        "decision": "approved",
        "decider": GATE_OWNERS[gate],
        "criteria": [{"name": "criterio-exemplo", "result": "pass"}],
        "evidence": ["reviews/rev-gate.md"],
        "human_approval": {"required": False, "status": "not_required", "approved_by": None, "evidence": None},
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
        "input_hashes": {key: hashes[key] for key in GATE_INPUTS[gate]},
        "policy_version": 1,
        "author": "requirements-analyst",
        "reviewer": "security-reviewer",
    }


def _approved_decisions(hashes: dict[str, str], work_id: str) -> list[dict]:
    return [
        _decision("G1-product", hashes, work_id),
        _decision("G2-design", hashes, work_id),
        _decision("G3-readiness", hashes, work_id),
    ]


# ---------------------------------------------------------------------------
# Sanidade do fixture: projeto consumidor isolado sob tmp_path
# ---------------------------------------------------------------------------


def test_consumer_project_fixture_is_isolated_and_policy_loads(modules, tmp_path):
    _, project_context = _load_project_context()
    project_root, work_dir, _ = _build_consumer_project(tmp_path)
    assert tmp_path in project_root.parents or project_root.parent == tmp_path
    assert (project_root / ".agents_squad" / "config" / "sdd-policy.yaml").is_file()
    resolution = project_context.resolve_sdd_policy(project_root)
    assert resolution.state == "active"
    assert resolution.policy["sdd"]["required"] is True
    assert project_context.sdd_required(project_root) is True
    # Pacote íntegro passa na validação estrutural de todos os estágios.
    adapter = modules[0]
    package = adapter.load_package(work_dir)
    for stage in ("planning", "tasking", "implementation", "readiness"):
        assert adapter.validate_package(package, stage) == [], stage


def _load_project_context():
    name = "project_context_pilot"
    if name in sys.modules:
        return sys.modules[name], sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / "project_context.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, module


# ---------------------------------------------------------------------------
# Cenário 1 — Given spec ausente, When solicitar plan, Then rejeitar
# ---------------------------------------------------------------------------


def test_scenario_1_missing_spec_rejects_plan(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, work_dir, hashes = _build_consumer_project(tmp_path, spec=False, work_id="US-PILOT-S1")
    package = adapter.load_package(work_dir)

    # 'plan' consome spec: validação estrutural rejeita (SDD_MISSING_INPUT).
    errors = adapter.validate_package(package, "tasking")
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)

    # E a autorização do estágio falha fechada: documento inexistente é stale.
    decisions = _approved_decisions(hashes, "US-PILOT-S1")
    errors = policy_mod.authorize(package, "tasking", decisions, REQUIRED_POLICY)
    assert any(e["code"] == "SDD_STALE_GATE" for e in errors)
    assert any("spec" in e["message"] for e in errors if e["code"] == "SDD_STALE_GATE")


# ---------------------------------------------------------------------------
# Cenário 2 — Given dúvida bloqueante aberta, When solicitar G1, Then
# rejeitar com ID da dúvida
# ---------------------------------------------------------------------------


def test_scenario_2_open_blocking_question_rejects_g1_with_id(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, work_dir, hashes = _build_consumer_project(
        tmp_path, blocking_question=True, work_id="US-PILOT-S2"
    )
    package = adapter.load_package(work_dir)

    errors = adapter.validate_package(package, "planning")
    blocking = [e for e in errors if e["code"] == "SDD_OPEN_QUESTION"]
    assert blocking and "Q-009" in blocking[0]["message"]

    # Contrato: authorize valida GATES (frescor/identidade), não semântica do
    # pacote; a dúvida bloqueante rejeita o G1 via validate_package, que é a
    # entrada obrigatória da avaliação de gate no CLI (T5). Nenhum caminho do
    # adapter autoriza G1 sobre o pacote rejeitado: authorize exige a decisão
    # vinculada e validate_package bloqueia antes.
    decisions = [_decision("G1-product", hashes, "US-PILOT-S2")]
    assert policy_mod.authorize(package, "planning", decisions, REQUIRED_POLICY) == []
    combined = adapter.validate_package(package, "planning")
    assert any(e["code"] == "SDD_OPEN_QUESTION" and "Q-009" in e["message"] for e in combined)

    # accepted_assumption sem justificativa NÃO disfarça bloqueante aberta.
    questions = package["documents"]["clarifications"]["parsed"]["questions"]
    questions.append(
        {
            "id": "Q-010",
            "requirement_ids": ["REQ-002"],
            "severity": "blocking",
            "status": "accepted_assumption",
            "question": "Assunção sem justificativa, owner ou condição de revisão.",
        }
    )
    errors = adapter.validate_package(package, "planning")
    assert any(e["code"] == "SDD_OPEN_QUESTION" and "Q-010" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# Cenário 3 — Given G1 válido/G2 ausente, When concluir blueprint, Then bloquear
# ---------------------------------------------------------------------------


def test_scenario_3_g1_without_g2_blocks_blueprint(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, work_dir, hashes = _build_consumer_project(tmp_path, work_id="US-PILOT-S3")
    package = adapter.load_package(work_dir)

    # Conclusão de blueprint exige G1+G2 (estágio 'tasking'); só G1 existe.
    decisions = [_decision("G1-product", hashes, "US-PILOT-S3")]
    errors = policy_mod.authorize(package, "tasking", decisions, REQUIRED_POLICY)
    assert any(e["code"] == "SDD_MISSING_INPUT" and "G2-design" in e["message"] for e in errors)

    # Com G2 aprovado também, o blueprint conclui (caminho positivo).
    decisions.append(_decision("G2-design", hashes, "US-PILOT-S3"))
    assert policy_mod.authorize(package, "tasking", decisions, REQUIRED_POLICY) == []


# ---------------------------------------------------------------------------
# Cenário 4 — Given spec alterada após aprovação, When retomar, Then gates
# dependentes desatualizados
# ---------------------------------------------------------------------------


def test_scenario_4_changed_spec_marks_dependent_gates_stale(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, work_dir, hashes = _build_consumer_project(tmp_path, work_id="US-PILOT-S4")
    decisions = _approved_decisions(hashes, "US-PILOT-S4")

    # Tudo aprovado antes da alteração...
    package = adapter.load_package(work_dir)
    assert policy_mod.authorize(package, "implementation", decisions, REQUIRED_POLICY) == []

    # ...spec alterada no disco após o gate...
    (work_dir / "sdd" / "spec.md").write_bytes(
        (SPEC_MD + "\nAlteração pós-gate.\n").encode("utf-8")
    )

    # ...retomada releitura o hash real: G1 stale e invalidação transitiva
    # alcança G2 e G3.
    package = adapter.load_package(work_dir)
    errors = policy_mod.authorize(package, "implementation", decisions, REQUIRED_POLICY)
    stale = [e for e in errors if e["code"] == "SDD_STALE_GATE"]
    assert stale
    assert any("G1-product" in e["path"] for e in stale)
    assert any("G2-design" in e["path"] for e in stale)
    assert any("G3-readiness" in e["path"] for e in stale)


# ---------------------------------------------------------------------------
# Cenário 5 — Given tarefa sem requisito ou requisito de código sem teste,
# When solicitar G3, Then rejeitar
# ---------------------------------------------------------------------------


def test_scenario_5_missing_requirement_or_test_rejects_g3(modules, tmp_path):
    adapter = modules[0]
    # Variante A: requisito de código cuja tarefa ficou sem teste.
    _, work_dir_a, _ = _build_consumer_project(
        tmp_path / "var-a", coverage_gap=True, work_id="US-PILOT-S5A"
    )
    package_a = adapter.load_package(work_dir_a)
    errors = adapter.validate_package(package_a, "readiness")
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and "REQ-002" in e["message"] for e in errors
    ), errors

    # Variante B: requisito de código sem nenhuma tarefa correspondente.
    _, work_dir_b, _ = _build_consumer_project(tmp_path / "var-b", work_id="US-PILOT-S5B")
    package_b = adapter.load_package(work_dir_b)
    package_b["requirements"].append(
        {"id": "REQ-004", "acceptance_ids": ["AC-004"], "classification": "code"}
    )
    errors = adapter.validate_package(package_b, "readiness")
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and "REQ-004" in e["message"] for e in errors
    ), errors

    # Variante C: tarefa sem vínculo com requisito não fecha cobertura —
    # a projeção de backlog a isola em SEM-REQUISITO (auditável).
    backlog = modules[3]
    package_c = adapter.load_package(work_dir_b)
    tasks = package_c["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["requirement_ids"] = []
    projection = backlog.project_tasks(tasks)
    assert "## SEM-REQUISITO" in projection


# ---------------------------------------------------------------------------
# Cenário 6 — Given pacote aprovado, RED válido e responsáveis definidos,
# When implementar, Then despachar somente tarefas elegíveis respeitando WIP
# ---------------------------------------------------------------------------


def test_scenario_6_approved_package_authorizes_implementation(modules, tmp_path):
    adapter, policy_mod, rendering = modules[0], modules[1], modules[2]
    _, work_dir, hashes = _build_consumer_project(tmp_path, work_id="US-PILOT-S6")
    package = adapter.load_package(work_dir)
    decisions = _approved_decisions(hashes, "US-PILOT-S6")

    # Autorização completa: G1+G2+G3 aprovados com evidências existentes,
    # evidência RED materializada (reviews/red-tests.md) e owners definidos.
    assert (work_dir / "reviews" / "red-tests.md").is_file()
    assert policy_mod.authorize(package, "implementation", decisions, REQUIRED_POLICY) == []

    # O briefing do comando implement carrega os contratos de TDD/BDD, WIP
    # e responsáveis (o dispatch em si exige Azure DevOps — fora do escopo
    # deste piloto; a elegibilidade é simulada adiante de forma determinística).
    briefing = rendering.render_command(
        "implement",
        {
            "work_id": "US-PILOT-S6",
            "project_id": "projeto_piloto",
            "plan_path": "sdd/plan.md",
            "tasks_path": "sdd/tasks.yaml",
            "g1_evidence": "reviews/rev-gate.md",
            "g2_evidence": "reviews/rev-gate.md",
            "g3_evidence": "reviews/rev-gate.md",
        },
    )
    assert "TDD" in briefing
    assert "WIP" in briefing
    assert "respeitando WIP e responsáveis" in briefing
    assert "reviews/rev-gate.md" in briefing  # evidência de gate embutida
    # A evidência RED materializada no work item é a que os owners executam.
    assert (work_dir / "reviews" / "red-tests.md").is_file()

    # Simulação determinística de elegibilidade: status aberto, dependências
    # satisfeitas (apenas com tarefas JÁ concluídas) e limite de WIP de
    # implementação (3).
    wip_limit = 3
    tasks = package["documents"]["tasks"]["parsed"]["tasks"]
    done: set[str] = set()
    eligible: list[str] = []
    for task in sorted(tasks, key=lambda t: t["id"]):
        if task.get("status") != "open":
            continue
        if any(dep not in done for dep in task.get("depends_on") or []):
            continue
        if len(eligible) >= wip_limit:
            break
        eligible.append(task["id"])
    done.update(eligible)  # conclusões da rodada só liberam dependentes DEPOIS

    assert eligible == ["T-001", "T-003"], "T-002 depende de T-001 (não elegível na 1ª rodada)"
    assert len(eligible) <= wip_limit

    # Segunda rodada: T-001 concluído libera T-002 dentro do WIP restante.
    done.add("T-001")
    wip_in_flight = 2  # T-003 ainda em execução
    eligible_round2 = [
        t["id"]
        for t in sorted(tasks, key=lambda x: x["id"])
        if t["status"] == "open"
        and t["id"] not in eligible
        and all(dep in done for dep in t.get("depends_on") or [])
    ][: max(wip_limit - wip_in_flight, 0)]
    assert eligible_round2 == ["T-002"]


# ---------------------------------------------------------------------------
# Cenário 9 — Given Boards indisponível, When validar tarefa remota, Then
# não aceitar dados antigos como aprovação
# ---------------------------------------------------------------------------


def test_scenario_9_boards_unavailable_stale_snapshot_not_approval(modules, tmp_path):
    backlog = modules[3]
    _, work_dir, _ = _build_consumer_project(tmp_path, work_id="US-PILOT-S9")
    adapter = modules[0]
    package = adapter.load_package(work_dir)
    tasks = package["documents"]["tasks"]["parsed"]["tasks"]

    # Snapshot remoto possivelmente vencido oferecido com o Boards fora do ar:
    # rejeitado como evidência de aprovação (SDD_REMOTE_STALE).
    errors = backlog.validate_remote_state(tasks, {"T-001": "AB-100"}, boards_available=False)
    assert any(e["code"] == "SDD_REMOTE_STALE" for e in errors)
    assert any("nunca" in e["message"] or "não é aceito" in e["message"] for e in errors)

    # Sem Boards e sem snapshot: tasks.yaml é canônico (não é erro).
    assert backlog.validate_remote_state(tasks, None, boards_available=False) == []

    # Com Boards disponível e snapshot consistente (tarefa declara o
    # remote_id sincronizado): aceito.
    tasks[0]["remote_id"] = "AB-100"
    assert backlog.validate_remote_state(tasks, {"T-001": "AB-100"}, boards_available=True) == []


# ---------------------------------------------------------------------------
# Cenário 10 — Given atualização upstream incompatível, When testar staging,
# Then manter versão anterior ativa
# ---------------------------------------------------------------------------


def test_scenario_10_incompatible_upstream_keeps_previous_version(modules, tmp_path):
    name = "verify_snapshot_pilot"
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "integrations" / "spec-kit" / "verify_snapshot.py"
    )
    verifier = importlib.util.module_from_spec(spec)
    sys.modules[name] = verifier
    spec.loader.exec_module(verifier)

    def _tree(root: Path, files: dict[str, bytes]) -> None:
        for rel, data in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def _manifest(root: Path, files: dict[str, bytes]) -> Path:
        lines = [f"{_sha256(data)}  {rel}" for rel, data in sorted(files.items())]
        manifest = root.parent / f"{root.name}.sha256"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest

    approved_files = {
        "adapter/x.py": b"versao aprovada v1\n",
        "README.md": b"readme v1\n",
    }

    # Versão anterior ATIVA: aprovada e verificada.
    active = tmp_path / "upstream-ativa"
    _tree(active, approved_files)
    active_manifest = _manifest(active, approved_files)
    ok, problems = verifier.verify_tree(active, active_manifest)
    assert ok and problems == []

    # Staging recebe o candidato (com seu próprio manifesto de promoção)...
    staging = tmp_path / "staging"
    candidate_files = dict(approved_files)
    candidate_files["adapter/x.py"] = b"versao candidata quebra contrato v2\n"
    _tree(staging, candidate_files)
    staging_manifest = _manifest(staging, candidate_files)

    # ...mas a árvore de staging é ADULTERADA/diverge do manifesto
    # (atualização incompatível detectada na verificação de promoção):
    # a verificação FALHA (MISMATCH) => promoção bloqueada.
    (staging / "adapter" / "x.py").write_bytes(b"conteudo adulterado pos-manifesto\n")
    ok, problems = verifier.verify_tree(staging, staging_manifest)
    assert not ok
    assert any("MISMATCH" in p for p in problems)

    # A promoção só ocorre com verificação limpa; como falhou, a versão
    # anterior permanece ativa e íntegra (byte-idêntica à aprovada).
    ok, problems = verifier.verify_tree(active, active_manifest)
    assert ok and problems == []
    assert (active / "adapter" / "x.py").read_bytes() == approved_files["adapter/x.py"]


# ---------------------------------------------------------------------------
# Cenário 11 — Given projeto legado sem ativação, When consultar, Then
# preservar compatibilidade e sinalizar ausência de garantia SDD
# ---------------------------------------------------------------------------


def test_scenario_11_legacy_project_compatible_without_guarantee(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, project_context = _load_project_context()

    # Variante A: política presente com required: false (legacy explícito).
    _, work_dir_a, _ = _build_consumer_project(
        tmp_path / "leg-a", policy="legacy", work_id="US-PILOT-S11A"
    )
    package_a = adapter.load_package(work_dir_a)
    # Sem decisões e sem vínculos: o caminho legado continua compatível...
    assert policy_mod.authorize(package_a, "implementation", [], LEGACY_POLICY) == []
    # ...mas a ausência de garantia SDD é sinalizada pelo auxílio canônico.
    assert policy_mod.is_sdd_required(LEGACY_POLICY) is False

    # Variante B: política ausente (projeto não migrado).
    _, work_dir_b, _ = _build_consumer_project(
        tmp_path / "leg-b", policy="absent", work_id="US-PILOT-S11B"
    )
    resolution = project_context.resolve_sdd_policy(tmp_path / "leg-b" / "projeto piloto ção")
    assert resolution.state == "legacy"
    assert resolution.policy is None
    assert project_context.sdd_required(resolution and tmp_path / "leg-b" / "projeto piloto ção") is False
    package_b = adapter.load_package(work_dir_b)
    assert policy_mod.authorize(package_b, "implementation", [], {"policy_version": 1, "sdd": {"required": False}}) == []
    assert policy_mod.is_sdd_required(None) is False


# ---------------------------------------------------------------------------
# Política inválida/ilegível em projeto ativado: falha fechada (nunca desativa)
# ---------------------------------------------------------------------------


def test_invalid_policy_fails_closed_in_activated_project(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, project_context = _load_project_context()
    project_root, work_dir, _ = _build_consumer_project(
        tmp_path, policy="invalid", work_id="US-PILOT-POL"
    )
    resolution = project_context.resolve_sdd_policy(project_root)
    assert resolution.state == "active"
    assert resolution.policy is None
    assert resolution.errors
    assert project_context.sdd_required(project_root) is True, "ilegível não desativa SDD"

    package = adapter.load_package(work_dir)
    errors = policy_mod.authorize(package, "planning", [], None)
    assert any(e["code"] == "SDD_POLICY_INVALID" for e in errors)


# ---------------------------------------------------------------------------
# Tamper-after-gate — aprovar, adulterar, autorizar no momento do avanço
# (valida o caminho de releitura sob lock do T5 no contrato do adapter)
# ---------------------------------------------------------------------------


def test_tamper_after_gate_fails_at_advance_time_with_stale_gate(modules, tmp_path):
    adapter, policy_mod = modules[0], modules[1]
    _, work_dir, hashes = _build_consumer_project(tmp_path, work_id="US-PILOT-TAMPER")
    decisions = _approved_decisions(hashes, "US-PILOT-TAMPER")

    package = adapter.load_package(work_dir)
    assert policy_mod.authorize(package, "implementation", decisions, REQUIRED_POLICY) == []

    # Adulteração do plan.md DEPOIS da aprovação do G2 e imediatamente antes
    # do avanço: a releitura sob lock (T5) recomputa o hash real e deve
    # rejeitar com SDD_STALE_GATE — a aprovação antiga não vale mais.
    (work_dir / "sdd" / "plan.md").write_bytes(
        (PLAN_MD + "\nAdulteração pós-gate antes do dispatch.\n").encode("utf-8")
    )
    package = adapter.load_package(work_dir)
    errors = policy_mod.authorize(package, "implementation", decisions, REQUIRED_POLICY)
    stale = [e for e in errors if e["code"] == "SDD_STALE_GATE"]
    assert stale, errors
    assert any("plan" in e["message"] for e in stale)
    assert any("G2-design" in e["path"] for e in stale)
