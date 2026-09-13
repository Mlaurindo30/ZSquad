"""Testes comportamentais da projeção de tarefas e backlog SDD (Tarefa T6).

Cobre o plano (seção 7/T6): requisito de código sem tarefa/teste
(``SDD_COVERAGE_GAP``), dependência inexistente/ciclo
(``SDD_DEPENDENCY_CYCLE``), conflito de ``remote_id``
(``SDD_REMOTE_CONFLICT``) e indisponibilidade do Boards — dados remotos
vencidos nunca são aceitos como evidência de aprovação
(``SDD_REMOTE_STALE``). Além disso: projeção determinística
( mesma entrada -> saída byte-idêntica ), agrupamento por requisito,
tabela de status e semântica de canonicalidade (com Boards habilitado,
IDs remotos são canônicos e tasks.md é visão gerada; sem Boards,
tasks.yaml é a fonte canônica).

Nenhum teste toca rede, credenciais ou o conector DevOps real: a projeção
é pura e a validação remota opera apenas sobre snapshots em memória.
O adaptador é carregado via importlib porque o diretório
'integrations/spec-kit' contém hífen e não é importável pelo nome.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "integrations" / "spec-kit" / "adapter"


def _load_adapter():
    name = "sdd_adapter_t6"
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


@pytest.fixture(scope="module")
def backlog(adapter):
    return importlib.import_module("sdd_adapter_t6.backlog")


def _task(task_id="T-001", **overrides) -> dict:
    base = {
        "id": task_id,
        "requirement_ids": ["REQ-001"],
        "acceptance_ids": ["AC-001"],
        "owner": "software-engineer",
        "points": 3,
        "depends_on": [],
        "paths": ["src/modulo.py"],
        "evidence": ["reviews/rev-modulo.md"],
        "test_ids": ["tests/test_modulo.py::test_ok"],
        "status": "open",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# project_tasks — projeção determinística
# ---------------------------------------------------------------------------


def test_projection_is_byte_identical_regardless_of_input_order(backlog):
    items = [
        _task("T-002", requirement_ids=["REQ-002"], depends_on=["T-001"], points=5),
        _task("T-001"),
    ]
    first = backlog.project_tasks(items)
    second = backlog.project_tasks(list(reversed(items)))
    third = backlog.project_tasks([dict(reversed(list(t.items()))) for t in items])
    assert first == second == third
    assert first.endswith("\n")


def test_projection_is_stable_across_repeated_calls(backlog):
    items = [_task("T-001"), _task("T-002", requirement_ids=["REQ-002"])]
    assert backlog.project_tasks(items) == backlog.project_tasks(items)


def test_projection_groups_by_requirement_in_sorted_order(backlog):
    items = [
        _task("T-002", requirement_ids=["REQ-002"]),
        _task("T-001", requirement_ids=["REQ-001"]),
    ]
    output = backlog.project_tasks(items)
    req1 = output.index("## REQ-001")
    req2 = output.index("## REQ-002")
    assert req1 < req2
    assert output.index("- T-001 |", req1) < req2
    assert output.index("- T-002 |", req2)


def test_projection_lists_owner_points_dependencies_tests_evidence_remote(backlog):
    output = backlog.project_tasks(
        [
            _task(
                "T-001",
                owner="backend-engineer",
                points=8,
                depends_on=["T-000"],
                test_ids=["tests/test_a.py::test_x", "tests/test_b.py::test_y"],
                evidence=["evidence/e1.md"],
                remote_id="42",
                status="done",
            )
        ]
    )
    assert "- T-001 | owner=backend-engineer | points=8 | status=done | depends_on=T-000 | remote_id=42" in output
    assert "test_ids: tests/test_a.py::test_x, tests/test_b.py::test_y" in output
    assert "evidence: evidence/e1.md" in output


def test_projection_has_status_table_sorted_by_id(backlog):
    output = backlog.project_tasks(
        [_task("T-002", status="open", remote_id="77"), _task("T-001", status="done")]
    )
    header = "| id | status | owner | points | depends_on | remote_id |"
    assert header in output
    row1 = output.index("| T-001 | done |")
    row2 = output.index("| T-002 | open |")
    assert row1 < row2
    assert "| T-002 | open |" in output and "| 77 |" in output


def test_projection_marks_task_without_requirement(backlog):
    output = backlog.project_tasks([_task("T-001", requirement_ids=[])])
    assert "## SEM-REQUISITO" in output
    assert output.index("## SEM-REQUISITO") < output.index("## Tabela de status")


def test_projection_of_empty_list_is_stable_and_explicit(backlog):
    output = backlog.project_tasks([])
    assert "(nenhuma tarefa)" in output
    assert backlog.project_tasks([]) == output


# ---------------------------------------------------------------------------
# validate_backlog — estrutura, dependências
# ---------------------------------------------------------------------------


def test_valid_backlog_has_no_errors(backlog):
    items = [_task("T-001"), _task("T-002", depends_on=["T-001"], points=8)]
    assert backlog.validate_backlog(items) == []


def test_missing_dependency_is_rejected(backlog):
    errors = backlog.validate_backlog([_task("T-002", depends_on=["T-XXX"])])
    assert any(
        e["code"] == "SDD_DEPENDENCY_CYCLE" and e["path"] == "tasks.T-002.depends_on"
        for e in errors
    )


def test_dependency_cycle_is_rejected(backlog):
    items = [
        _task("T-001", depends_on=["T-002"]),
        _task("T-002", depends_on=["T-001"]),
    ]
    errors = backlog.validate_backlog(items)
    assert any(e["code"] == "SDD_DEPENDENCY_CYCLE" and "ciclo" in e["message"] for e in errors)


def test_duplicate_task_id_is_malformed(backlog):
    errors = backlog.validate_backlog([_task("T-001"), _task("T-001")])
    assert any(e["code"] == "SDD_MALFORMED" for e in errors)


def test_invalid_points_is_malformed(backlog):
    errors = backlog.validate_backlog([_task("T-001", points=13)])
    assert any(e["code"] == "SDD_MALFORMED" and "points" in e["path"] for e in errors)


def test_missing_required_field_is_malformed(backlog):
    item = _task("T-001")
    item.pop("owner")
    errors = backlog.validate_backlog([item])
    assert any(e["code"] == "SDD_MALFORMED" for e in errors)


def test_non_dict_item_is_malformed(backlog):
    errors = backlog.validate_backlog(["T-001"])  # type: ignore[list-item]
    assert any(e["code"] == "SDD_MALFORMED" for e in errors)


# ---------------------------------------------------------------------------
# Cobertura — requisito de código sem tarefa/teste
# ---------------------------------------------------------------------------


def _requirements():
    return [
        {"id": "REQ-001", "classification": "code", "acceptance_ids": ["AC-001"]},
        {"id": "REQ-002", "classification": "code", "acceptance_ids": ["AC-002"]},
        {"id": "REQ-DOC", "classification": "documentation"},
    ]


def test_code_requirement_covered_by_task_with_test(backlog):
    items = [_task("T-001"), _task("T-002", requirement_ids=["REQ-002"], test_ids=["tests/test_b.py::t"])]
    assert backlog.check_coverage(_requirements(), items) == []


def test_code_requirement_without_task_is_coverage_gap(backlog):
    errors = backlog.check_coverage(_requirements(), [_task("T-001")])
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and e["path"] == "requirements.REQ-002" for e in errors
    )


def test_code_requirement_with_task_without_test_is_coverage_gap(backlog):
    errors = backlog.check_coverage(_requirements(), [_task("T-001", test_ids=[])])
    assert any(
        e["code"] == "SDD_COVERAGE_GAP" and e["path"] == "requirements.REQ-001" for e in errors
    )


def test_documentation_requirement_does_not_require_test(backlog):
    requirements = [{"id": "REQ-DOC", "classification": "documentation"}]
    items = [_task("T-DOC", requirement_ids=["REQ-DOC"], test_ids=[])]
    assert backlog.check_coverage(requirements, items) == []


# ---------------------------------------------------------------------------
# remote_id — conflito e indisponibilidade do Boards
# ---------------------------------------------------------------------------


def test_duplicate_remote_id_is_conflict(backlog):
    items = [_task("T-001", remote_id="42"), _task("T-002", remote_id="42")]
    errors = backlog.check_remote_ids(items)
    assert any(
        e["code"] == "SDD_REMOTE_CONFLICT" and e["path"] == "tasks.T-002.remote_id" for e in errors
    )


def test_distinct_remote_ids_have_no_conflict(backlog):
    items = [_task("T-001", remote_id="42"), _task("T-002", remote_id="77")]
    assert backlog.check_remote_ids(items) == []


def test_tasks_without_remote_id_have_no_conflict(backlog):
    items = [_task("T-001"), _task("T-002")]
    assert backlog.check_remote_ids(items) == []


def test_boards_unavailable_rejects_stale_snapshot(backlog):
    items = [_task("T-001", remote_id="42")]
    snapshot = {"T-001": "42"}
    errors = backlog.validate_remote_state(items, snapshot, boards_available=False)
    assert any(e["code"] == "SDD_REMOTE_STALE" for e in errors)
    # Nenhuma função de projeção/validação devolve o snapshot como evidência.
    assert all("aprov" not in str(e.get("evidence", "")) for e in errors)


def test_boards_unavailable_without_snapshot_is_not_an_error(backlog):
    # Sem Boards e sem snapshot, tasks.yaml é a fonte canônica: válido.
    items = [_task("T-001")]
    assert backlog.validate_remote_state(items, None, boards_available=False) == []


def test_boards_available_with_consistent_snapshot_is_accepted(backlog):
    items = [_task("T-001", remote_id="42"), _task("T-002", remote_id="77")]
    snapshot = {"T-001": "42", "T-002": "77"}
    assert backlog.validate_remote_state(items, snapshot, boards_available=True) == []


def test_boards_available_with_mismatched_snapshot_is_conflict(backlog):
    items = [_task("T-001", remote_id="42")]
    snapshot = {"T-001": "999"}
    errors = backlog.validate_remote_state(items, snapshot, boards_available=True)
    assert any(e["code"] == "SDD_REMOTE_CONFLICT" for e in errors)


def test_boards_available_with_unknown_task_in_snapshot_is_conflict(backlog):
    items = [_task("T-001", remote_id="42")]
    snapshot = {"T-DESCONHECIDA": "42"}
    errors = backlog.validate_remote_state(items, snapshot, boards_available=True)
    assert any(e["code"] == "SDD_REMOTE_CONFLICT" for e in errors)


def test_malformed_snapshot_is_rejected(backlog):
    errors = backlog.validate_remote_state([_task("T-001")], "nao-e-mapa", boards_available=True)
    assert any(e["code"] == "SDD_MALFORMED" for e in errors)


def test_snapshot_omitting_declared_remote_id_is_conflict(backlog):
    # Sonda P7 do revisor: snapshot que omite uma task que declara remote_id
    # não pode retornar [] silenciosamente — esconde work item excluído
    # remotamente (divergência bidirecional).
    items = [_task("T-001", remote_id="42"), _task("T-002", remote_id="77")]
    snapshot = {"T-002": "77"}  # T-001 omitida do snapshot
    errors = backlog.validate_remote_state(items, snapshot, boards_available=True)
    assert any(
        e["code"] == "SDD_REMOTE_CONFLICT"
        and e["path"] == "tasks.T-001.remote_id"
        and "T-001" in e["message"]
        and "ausente do snapshot" in e["message"]
        for e in errors
    )


def test_snapshot_omitting_task_without_remote_id_is_not_conflict(backlog):
    # Task sem remote_id declarado não é reconciliável por ID remoto: ausência
    # no snapshot é esperada (modo sem Boards por task).
    items = [_task("T-001", remote_id="42"), _task("T-002")]
    snapshot = {"T-001": "42"}
    assert backlog.validate_remote_state(items, snapshot, boards_available=True) == []


def test_task_without_id_remote_conflict_path_is_diagnosable(backlog):
    # MINOR-3: path "tasks..remote_id" (id vazio) é inutilizável em auditoria;
    # o path deve conter o remote_id ou placeholder explícito.
    no_id = _task("T-002", remote_id="42")
    no_id.pop("id")
    errors = backlog.check_remote_ids([_task("T-001", remote_id="42"), no_id])
    conflicts = [e for e in errors if e["code"] == "SDD_REMOTE_CONFLICT"]
    assert conflicts
    assert all(e["path"] != "tasks..remote_id" for e in conflicts)
    assert all("42" in e["path"] for e in conflicts)


def test_project_tasks_docstring_declares_validated_input_precondition(backlog):
    # MINOR-2: docstring deve declarar a pré-condição de entrada validada
    # (determinismo byte-a-byte vale para entradas sem IDs duplicados).
    doc = backlog.project_tasks.__doc__ or ""
    assert "entrada validada" in doc
    assert "duplicado" in doc.lower()
