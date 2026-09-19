"""Testes comportamentais do adaptador de contratos SDD (Tarefa T2).

Cobre o pacote valido e os casos estruturais do plano (secao 6/T2):
input ausente, ID duplicado, path fora da raiz, duvida bloqueante,
ciclo de dependencias, points=13, YAML inseguro, carregamento a partir
de outro cwd e caminhos com espacos/acentos no Windows.

O pacote e materializado em tmp_path (layout work_dir/sdd/), nunca em
arquivos de producao. O adaptador e carregado via importlib porque o
diretorio 'integrations/spec-kit' contem hifen e nao e importavel pelo
nome.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "integrations" / "spec-kit" / "adapter"

SPEC_MD = "# Spec\n\nRequisito de exemplo com acentuação: título ção.\n"
PLAN_MD = "# Plan\n\nPlano de exemplo.\n"
CONSTITUTION_MD = "# Constituição do projeto\n\nPrincípios.\n"
DOCS_PATH = "docs/título ção.md"
DOCS_MD = "# Documentação com espaço e acento\n"

CLARIFICATIONS_YAML = """questions:
  - id: Q-001
    requirement_ids: [REQ-001]
    severity: nonblocking
    status: resolved
    question: "Formato do relatório?"
    answer: "Markdown consolidado."
    source: "ata de refinement 2026-09-10"
    owner: product-owner
  - id: Q-002
    requirement_ids: [REQ-002]
    severity: blocking
    status: accepted_assumption
    question: "Limite de tamanho do payload?"
    answer: "Assumido 10 MB até confirmação do cliente."
    source: "e-mail do cliente 2026-09-09"
    owner: backend-engineer
    review_condition: "Revalidar com o cliente antes de G3."
"""

TASKS_YAML = """tasks:
  - id: T-001
    requirement_ids: [REQ-001]
    acceptance_ids: [AC-001]
    owner: technical-writer
    points: 3
    depends_on: []
    paths: ["docs/título ção.md"]
    evidence: ["reviews/rev-documentacao.md"]
    test_ids: []
    status: done
  - id: T-002
    requirement_ids: [REQ-002]
    acceptance_ids: [AC-002]
    owner: backend-engineer
    points: 5
    depends_on: [T-001]
    paths: ["src/modulo.py"]
    evidence: []
    test_ids: ["tests/test_modulo.py::test_ok"]
    status: open
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_adapter():
    name = "sdd_adapter_t2"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ADAPTER_DIR / "__init__.py", submodule_search_locations=[str(ADAPTER_DIR)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover - só se arquivo faltar
        pytest.fail(f"adapter/__init__.py não encontrado em {ADAPTER_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def adapter():
    return _load_adapter()


def _materialize(work_dir: Path, *, omit: tuple[str, ...] = ()) -> dict:
    """Escreve os cinco documentos + docs extras e retorna o package.json em disco."""
    sdd = work_dir / "sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    (work_dir / "docs").mkdir(parents=True, exist_ok=True)

    files: dict[Path, bytes] = {
        sdd / "spec.md": SPEC_MD.encode("utf-8"),
        sdd / "clarifications.yaml": CLARIFICATIONS_YAML.encode("utf-8"),
        sdd / "plan.md": PLAN_MD.encode("utf-8"),
        sdd / "tasks.yaml": TASKS_YAML.encode("utf-8"),
        sdd / "constitution.md": CONSTITUTION_MD.encode("utf-8"),
        work_dir / DOCS_PATH: DOCS_MD.encode("utf-8"),
    }
    for path, data in files.items():
        if path.name in omit:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in omit:
        target = sdd / name
        if target.exists():
            target.unlink()

    package = {
        "schema_version": 1,
        "project_id": "agent_squad",
        "work_id": "TASK-SPECKIT-T2-20260911",
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
            {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "documentation"},
            {"id": "REQ-002", "acceptance_ids": ["AC-002"], "classification": "code"},
        ],
        "reviews": [
            {
                "author": "backend-engineer",
                "reviewer": "code-reviewer",
                "result": "approved",
                "evidence": ["reviews/rev-t2.md"],
                "reviewed_sha256": [_sha256(SPEC_MD.encode("utf-8"))],
            }
        ],
    }
    (sdd / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return package


@pytest.fixture()
def valid_package(adapter, tmp_path):
    """Pacote integro em work_dir com espacos/acentos no caminho (Windows)."""
    work_dir = tmp_path / "espaço no caminho" / "WORK T2 ção"
    _materialize(work_dir)
    return adapter.load_package(work_dir)


# ---------------------------------------------------------------------------
# Pacote valido passa em todos os estagios
# ---------------------------------------------------------------------------


def test_valid_package_passes_all_stages(adapter, valid_package):
    for stage in ("planning", "tasking", "implementation", "readiness"):
        assert adapter.validate_package(valid_package, stage) == [], stage


def test_paths_with_spaces_and_accents(adapter, tmp_path):
    work_dir = tmp_path / "espaço no caminho" / "título ção"
    _materialize(work_dir)
    package = adapter.load_package(work_dir)
    for stage in ("planning", "tasking", "implementation", "readiness"):
        assert adapter.validate_package(package, stage) == [], stage
    assert package["documents"]["tasks"]["exists"] is True


# ---------------------------------------------------------------------------
# Inputs ausentes
# ---------------------------------------------------------------------------


def test_missing_spec_is_rejected(adapter, valid_package):
    valid_package["inputs"].pop("spec")
    errors = adapter.validate_package(valid_package, "planning")
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)


def test_missing_spec_file_on_disk_is_rejected(adapter, tmp_path):
    work_dir = tmp_path / "sem spec"
    _materialize(work_dir, omit=("spec.md",))
    package = adapter.load_package(work_dir)
    errors = adapter.validate_package(package, "planning")
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)


def test_missing_constitution_is_rejected(adapter, tmp_path):
    work_dir = tmp_path / "sem constituicao"
    _materialize(work_dir, omit=("constitution.md",))
    package = adapter.load_package(work_dir)
    errors = adapter.validate_package(package, "planning")
    assert any(e["code"] == "SDD_MISSING_INPUT" for e in errors)


# ---------------------------------------------------------------------------
# Estrutura malformada
# ---------------------------------------------------------------------------


def test_duplicate_requirement_id_is_rejected(adapter, valid_package):
    valid_package["requirements"].append(
        {"id": "REQ-001", "acceptance_ids": [], "classification": "code"}
    )
    errors = adapter.validate_package(valid_package, "planning")
    assert any(e["code"] == "SDD_MALFORMED" and "REQ-001" in e["message"] for e in errors)


def test_duplicate_task_id_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks.append(dict(tasks[0]))
    errors = adapter.validate_package(valid_package, "planning")
    assert any(e["code"] == "SDD_MALFORMED" and "T-001" in e["message"] for e in errors)


def test_points_13_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["points"] = 13
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(e["code"] == "SDD_MALFORMED" and "points" in e["message"] for e in errors)


def test_unknown_stage_is_rejected(adapter, valid_package):
    errors = adapter.validate_package(valid_package, "release")
    assert len(errors) == 1
    assert errors[0]["code"] == "SDD_MALFORMED"
    assert "release" in errors[0]["message"]


def test_non_dict_package_is_rejected(adapter):
    errors = adapter.validate_package(["não", "é", "dict"], "planning")
    assert errors and errors[0]["code"] == "SDD_MALFORMED"


def test_task_path_outside_work_root_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["paths"] = ["../escape.md"]
    errors = adapter.validate_package(valid_package, "planning")
    assert any(e["code"] == "SDD_MALFORMED" and "escape.md" in e["message"] for e in errors)


def test_input_path_outside_work_root_is_rejected(adapter, valid_package):
    valid_package["inputs"]["spec"]["path"] = "../escape.md"
    errors = adapter.validate_package(valid_package, "planning")
    assert any(
        e["code"] == "SDD_MALFORMED" and "inputs.spec.path" in e["path"] for e in errors
    )


# ---------------------------------------------------------------------------
# Clarifications
# ---------------------------------------------------------------------------


def test_open_blocking_question_is_rejected(adapter, valid_package):
    questions = valid_package["documents"]["clarifications"]["parsed"]["questions"]
    questions.append(
        {
            "id": "Q-003",
            "requirement_ids": ["REQ-001"],
            "severity": "blocking",
            "status": "open",
            "question": "Qual o limite real do payload?",
        }
    )
    errors = adapter.validate_package(valid_package, "planning")
    blocking = [e for e in errors if e["code"] == "SDD_OPEN_QUESTION"]
    assert blocking and "Q-003" in blocking[0]["message"]


def test_accepted_assumption_without_justification_masks_as_blocking(adapter, valid_package):
    questions = valid_package["documents"]["clarifications"]["parsed"]["questions"]
    questions.append(
        {
            "id": "Q-004",
            "requirement_ids": ["REQ-002"],
            "severity": "blocking",
            "status": "accepted_assumption",
            "question": "Assunção sem justificativa, owner ou condição de revisão.",
        }
    )
    errors = adapter.validate_package(valid_package, "planning")
    assert any(
        e["code"] == "SDD_OPEN_QUESTION" and "Q-004" in e["message"] for e in errors
    )


# ---------------------------------------------------------------------------
# Dependências e cobertura
# ---------------------------------------------------------------------------


def test_dependency_cycle_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["depends_on"] = ["T-002"]
    tasks[1]["depends_on"] = ["T-001"]
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(e["code"] == "SDD_DEPENDENCY_CYCLE" for e in errors)


def test_dependency_on_unknown_task_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["depends_on"] = ["T-404"]
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(e["code"] == "SDD_DEPENDENCY_CYCLE" for e in errors)


def test_code_requirement_without_task_and_test_is_rejected(adapter, valid_package):
    valid_package["requirements"].append(
        {"id": "REQ-003", "acceptance_ids": ["AC-003"], "classification": "code"}
    )
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and "REQ-003" in e["message"] for e in errors
    )


def test_code_requirement_with_task_without_test_is_rejected(adapter, valid_package):
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[1]["test_ids"] = []
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and "REQ-002" in e["message"] for e in errors
    )


# ---------------------------------------------------------------------------
# Achados do code-reviewer (MAJOR-1, MAJOR-2, MINOR-1, MINOR-3)
# ---------------------------------------------------------------------------


def _hand_built_package() -> dict:
    """Dict válido por schema, mas SEM work_dir/documents (fora do runtime)."""
    fake_hash = "a" * 64
    return {
        "schema_version": 1,
        "project_id": "agent_squad",
        "work_id": "TASK-SPECKIT-T2-20260911",
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": fake_hash,
        "inputs": {
            "spec": {"path": "sdd/spec.md", "revision": "r1", "sha256": fake_hash},
            "clarifications": {"path": "sdd/clarifications.yaml", "revision": "r1", "sha256": fake_hash},
            "plan": {"path": "sdd/plan.md", "revision": "r1", "sha256": fake_hash},
            "tasks": {"path": "sdd/tasks.yaml", "revision": "r1", "sha256": fake_hash},
        },
        "requirements": [],
    }


def test_hostile_dict_absolute_path_is_rejected(adapter):
    """MAJOR-1: dict sem work_dir/documents com path absoluto não pode retornar []."""
    package = _hand_built_package()
    package["inputs"]["spec"]["path"] = "C:\\Windows\\win.ini"
    errors = adapter.validate_package(package, "planning")
    assert errors, "validate_package deve falhar fechado, nunca []"
    assert any(
        e["code"] == "SDD_MALFORMED" and "win.ini" in e["message"] for e in errors
    )


def test_hostile_dict_dotdot_path_is_rejected_without_work_dir(adapter):
    """MAJOR-1: traversal '..' sem work_dir também é rejeitado (falha fechada)."""
    package = _hand_built_package()
    package["inputs"]["spec"]["path"] = "../escape.md"
    errors = adapter.validate_package(package, "planning")
    assert any(
        e["code"] == "SDD_MALFORMED" and "escape.md" in e["message"] for e in errors
    )


def test_clean_relative_paths_without_work_dir_are_accepted(adapter):
    """MAJOR-1: caminhos relativos simples não podem escapar; sem work_dir continuam válidos."""
    errors = adapter.validate_package(_hand_built_package(), "planning")
    assert errors == []


def test_task_null_required_fields_are_rejected(adapter, valid_package):
    """MAJOR-2: owner/status null devem gerar erro (não apenas chave ausente)."""
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["owner"] = None
    tasks[0]["status"] = None
    errors = adapter.validate_package(valid_package, "implementation")
    for field in ("owner", "status"):
        assert any(
            e["code"] == "SDD_MALFORMED" and f"'{field}'" in e["message"] for e in errors
        ), field


def test_points_null_is_rejected(adapter, valid_package):
    """MAJOR-2: points null não pode contornar a regra Fibonacci."""
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["points"] = None
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(
        e["code"] == "SDD_MALFORMED" and "points" in e["message"] for e in errors
    )


def test_drive_relative_path_is_rejected(adapter, valid_package):
    """MINOR-1: 'C:sdd/spec.md' (unidade relativa) deve ser rejeitado explicitamente."""
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["paths"] = ["C:sdd/spec.md"]
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(
        e["code"] == "SDD_MALFORMED" and "C:sdd/spec.md" in e["message"] for e in errors
    )


def test_duplicate_question_id_is_rejected(adapter, valid_package):
    """MINOR-3: IDs de question duplicados são rejeitados."""
    questions = valid_package["documents"]["clarifications"]["parsed"]["questions"]
    questions.append(dict(questions[0]))
    errors = adapter.validate_package(valid_package, "planning")
    assert any(
        e["code"] == "SDD_MALFORMED" and "Q-001" in e["message"] for e in errors
    )


def test_task_id_with_spaces_or_traversal_is_rejected(adapter, valid_package):
    """MINOR-3: ID de task com espaço ou traversal é rejeitado (IDs livres, mas seguros)."""
    tasks = valid_package["documents"]["tasks"]["parsed"]["tasks"]
    tasks[0]["id"] = "meu id com espaço"
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(e["code"] == "SDD_MALFORMED" and "id" in e["message"].lower() for e in errors)
    tasks[0]["id"] = "../evil"
    errors = adapter.validate_package(valid_package, "implementation")
    assert any(e["code"] == "SDD_MALFORMED" and "ID de task inválido" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# Carregamento seguro
# ---------------------------------------------------------------------------


def test_unsafe_yaml_is_rejected(adapter, tmp_path):
    work_dir = tmp_path / "yaml inseguro"
    _materialize(work_dir)
    unsafe = (
        "questions:\n"
        "  - id: !!python/object/apply:os.system\n"
        "    args: ['echo comprometido']\n"
    )
    (work_dir / "sdd" / "clarifications.yaml").write_text(unsafe, encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        adapter.load_package(work_dir)


def test_malformed_package_json_is_rejected(adapter, tmp_path):
    work_dir = tmp_path / "json quebrado"
    _materialize(work_dir)
    (work_dir / "sdd" / "package.json").write_text("{ não é json", encoding="utf-8")
    with pytest.raises(ValueError):
        adapter.load_package(work_dir)


def test_load_from_other_cwd(tmp_path):
    """load_package + validate_package funcionam de outro cwd, sem sys.path global."""
    work_dir = tmp_path / "outro cwd" / "WORK"
    _materialize(work_dir)
    other_cwd = tmp_path / "cwd-alheio"
    other_cwd.mkdir()
    script = (
        "import importlib.util, json, pathlib, sys\n"
        "base = pathlib.Path(sys.argv[1])\n"
        "work_dir = pathlib.Path(sys.argv[2])\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'sdd_adapter', base / '__init__.py',\n"
        "    submodule_search_locations=[str(base)])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['sdd_adapter'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "pkg = mod.load_package(work_dir)\n"
        "res = {s: mod.validate_package(pkg, s) for s in"
        " ('planning', 'tasking', 'implementation', 'readiness')}\n"
        "print(json.dumps({k: len(v) for k, v in res.items()}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(ADAPTER_DIR), str(work_dir)],
        cwd=other_cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {
        "planning": 0,
        "tasking": 0,
        "implementation": 0,
        "readiness": 0,
    }
