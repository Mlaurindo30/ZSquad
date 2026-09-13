"""Validação estrutural e de cobertura do pacote SDD (T2) — backend-engineer.

``validate_package(package, stage) -> list[dict]`` é pura: recebe o dicionário
normalizado por :func:`adapter.contracts.load_package` (ou um subconjunto seu)
e devolve erros ``{"code", "path", "message"}``. Lista vazia significa
ausência de erros *estruturais* — não substitui revisão semântica humana ou
especializada, nem valida semântica de autorização de estágio (domínio de
T4/T5).

Semântica de ``stage`` (estrutural apenas):

- ``planning``        — exige spec + clarifications (e constituição);
- ``tasking``         — exige spec + clarifications + plan;
- ``implementation``  — exige os quatro inputs + cobertura + DAG;
- ``readiness``       — idêntico a ``implementation``.

Estágio fora desse conjunto gera ``SDD_MALFORMED``. Verificação de hashes
aprovados vs. atuais (``SDD_STALE_GATE``) e política (``SDD_POLICY_INVALID``
em gates) são implementadas em T4; aqui ``SDD_POLICY_INVALID`` aparece
somente em :func:`validate_policy`.

Semântica fail-closed de paths (achado MAJOR-1 da revisão de T2): a
verificação de contenção roda para TODOS os paths (inputs, constitution_path
e tasks.*.paths). Com ``work_dir`` presente, aplica-se contenção completa via
``contracts.resolve_within``. Sem ``work_dir`` (dict construído fora do
runtime de ``load_package``), qualquer path absoluto, com unidade/root
(ex.: ``C:\\Windows\\win.ini``, ``/etc/passwd``) ou com segmento ``..`` é
rejeitado como ``SDD_MALFORMED`` porque a contenção não é verificável;
apenas caminhos relativos simples (que não podem escapar de qualquer raiz)
são aceitos. Verificação de existência em disco exige ``documents`` ou
``work_dir`` (ambos fornecidos por ``load_package``).

Política de IDs (MINOR-3): IDs de task e question são livres, mas seguros —
``^[A-Za-z0-9][A-Za-z0-9._-]*$`` (sem espaços, sem barras, sem ``..``,
não iniciando por separador); duplicatas são rejeitadas.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from .contracts import KNOWN_STAGES, resolve_within

__all__ = ["validate_package", "validate_policy"]

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

REQUIRED_INPUTS_BY_STAGE: dict[str, tuple[str, ...]] = {
    "planning": ("spec", "clarifications"),
    "tasking": ("spec", "clarifications", "plan"),
    "implementation": ("spec", "clarifications", "plan", "tasks"),
    "readiness": ("spec", "clarifications", "plan", "tasks"),
}

VALID_POINTS = (1, 2, 3, 5, 8)
QUESTION_SEVERITIES = ("blocking", "nonblocking")
QUESTION_STATUSES = ("open", "resolved", "accepted_assumption")
_TASK_REQUIRED_FIELDS = (
    "id",
    "requirement_ids",
    "acceptance_ids",
    "owner",
    "points",
    "depends_on",
    "paths",
    "test_ids",
    "status",
)
_QUESTION_REQUIRED_FIELDS = ("id", "requirement_ids", "severity", "status", "question")

# MINOR-3: IDs são livres (não exige padrão TASK-nnn/Q-nnn), porém seguros:
# alfanuméricos + '.', '_', '-', iniciando por letra/número; sem espaços e
# sem separadores de caminho. Documentado no docstring do módulo.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.match(value))


def _err(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def _load_schema(name: str) -> dict | None:
    schema_file = CONTRACTS_DIR / name
    if not schema_file.is_file():
        return None
    try:
        return json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _json_path(verror: jsonschema.ValidationError) -> str:
    parts = [str(part) for part in verror.absolute_path]
    return "package." + ".".join(parts) if parts else "package"


def validate_package(package: dict, stage: str) -> list[dict]:
    """Valida estrutura, cobertura e DAG do pacote SDD para o estágio dado."""
    if stage not in KNOWN_STAGES:
        return [
            _err(
                "SDD_MALFORMED",
                "stage",
                f"estágio desconhecido: {stage!r}; estágios válidos: {', '.join(KNOWN_STAGES)}",
            )
        ]
    if not isinstance(package, dict):
        return [_err("SDD_MALFORMED", "package", "pacote SDD deve ser um objeto (dict)")]

    errors: list[dict] = []
    errors.extend(_check_schema(package))
    errors.extend(_check_stage_inputs(package, stage))
    errors.extend(_check_path_traversal(package))
    errors.extend(_check_requirements(package))
    errors.extend(_check_clarifications(package))
    errors.extend(_check_tasks(package))
    return errors


# ---------------------------------------------------------------------------
# Esquema JSON e inputs por estágio
# ---------------------------------------------------------------------------


def _check_schema(package: dict) -> list[dict]:
    schema = _load_schema("sdd-package.schema.json")
    if schema is None:
        return [
            _err(
                "SDD_MALFORMED",
                "package",
                "esquema contracts/sdd-package.schema.json indisponível (falha fechada)",
            )
        ]
    errors = []
    validator = jsonschema.Draft202012Validator(schema)
    for verror in sorted(validator.iter_errors(package), key=str):
        errors.append(
            _err("SDD_MALFORMED", _json_path(verror), f"violação de schema: {verror.message}")
        )
    return errors


def _document_exists(package: dict, key: str) -> bool:
    documents = package.get("documents")
    if isinstance(documents, dict) and key in documents:
        return bool(documents[key].get("exists"))
    work_dir = package.get("work_dir")
    if work_dir and isinstance(package.get("inputs"), dict):
        entry = package["inputs"].get(key)
        if isinstance(entry, dict):
            safe = resolve_within(Path(work_dir), entry.get("path"))
            return safe is not None and safe.is_file()
    return True  # indeterminado fora do runtime; schema já exige presença


def _check_stage_inputs(package: dict, stage: str) -> list[dict]:
    errors: list[dict] = []
    if not _document_exists(package, "constitution"):
        errors.append(
            _err(
                "SDD_MISSING_INPUT",
                "constitution_path",
                f"constituição não encontrada ou ilegível: {package.get('constitution_path')}",
            )
        )
    inputs = package.get("inputs")
    for key in REQUIRED_INPUTS_BY_STAGE[stage]:
        entry = inputs.get(key) if isinstance(inputs, dict) else None
        if not isinstance(entry, dict):
            errors.append(
                _err(
                    "SDD_MISSING_INPUT",
                    f"inputs.{key}",
                    f"input obrigatório ausente no estágio '{stage}': {key}",
                )
            )
        elif not _document_exists(package, key):
            errors.append(
                _err(
                    "SDD_MISSING_INPUT",
                    f"inputs.{key}.path",
                    f"documento do input '{key}' não encontrado ou ilegível: {entry.get('path')}",
                )
            )
    return errors


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


def _check_path_traversal(package: dict) -> list[dict]:
    """Verifica todos os paths do pacote (inputs, constituição e tasks).

    Com ``work_dir`` presente, aplica contenção via ``resolve_within``.
    Sem ``work_dir`` (dict construído fora do runtime), falha fechada:
    qualquer path absoluto, com unidade/root ou com segmento ``..`` é
    rejeitado porque a contenção não é verificável. Caminhos relativos
    simples não podem escapar da raiz e permanecem aceitos.
    """
    work_dir = package.get("work_dir")
    root = Path(work_dir) if isinstance(work_dir, str) and work_dir else None
    errors: list[dict] = []
    checked: list[tuple[str, object]] = []
    inputs = package.get("inputs")
    if isinstance(inputs, dict):
        for key, entry in inputs.items():
            if isinstance(entry, dict):
                checked.append((f"inputs.{key}.path", entry.get("path")))
    checked.append(("constitution_path", package.get("constitution_path")))
    for task in _iter_tasks(package):
        for rel in task.get("paths") or []:
            checked.append((f"tasks.{task.get('id')}.paths", rel))
    for where, rel in checked:
        if root is not None:
            if resolve_within(root, rel) is None:
                errors.append(
                    _err(
                        "SDD_MALFORMED",
                        where,
                        f"path fora da raiz do trabalho (path traversal): {rel!r}",
                    )
                )
        elif _unverifiable_path(rel):
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    where,
                    f"path não verificável sem work_dir (falha fechada; use caminho "
                    f"relativo simples ou informe work_dir): {rel!r}",
                )
            )
    return errors


def _unverifiable_path(rel) -> bool:
    """True se o path não pode ser declarado seguro sem um work_dir de referência."""
    if not isinstance(rel, str) or not rel:
        return False  # ausente/vazio é tratado pelos checks de obrigatoriedade
    candidate = Path(rel)
    if candidate.drive or candidate.root or candidate.is_absolute():
        return True
    return ".." in candidate.parts


# ---------------------------------------------------------------------------
# Requisitos, clarifications e tasks
# ---------------------------------------------------------------------------


def _requirement_ids(package: dict) -> set[str]:
    return {req["id"] for req in package.get("requirements", []) if isinstance(req, dict)}


def _check_requirements(package: dict) -> list[dict]:
    errors: list[dict] = []
    seen: set[str] = set()
    for index, req in enumerate(package.get("requirements", [])):
        if not isinstance(req, dict):
            errors.append(_err("SDD_MALFORMED", f"requirements.{index}", "requisito deve ser um objeto"))
            continue
        req_id = req.get("id")
        if req_id in seen:
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"requirements.{req_id}",
                    f"ID de requisito duplicado: {req_id}",
                )
            )
        seen.add(req_id)
    return errors


def _check_requirements_coverage(package: dict, tasks: list[dict]) -> list[dict]:
    errors: list[dict] = []
    covered: set[str] = set()
    for task in tasks:
        if task.get("test_ids"):
            covered.update(task.get("requirement_ids") or [])
    for req in package.get("requirements", []):
        if not isinstance(req, dict) or req.get("classification") != "code":
            continue
        req_id = req.get("id")
        if req_id not in covered:
            errors.append(
                _err(
                    "SDD_COVERAGE_GAP",
                    f"requirements.{req_id}",
                    f"requisito de código {req_id} sem tarefa com teste correspondente",
                )
            )
    return errors


def _parsed_questions(package: dict) -> list | None:
    documents = package.get("documents")
    if not isinstance(documents, dict):
        return None
    entry = documents.get("clarifications")
    if not isinstance(entry, dict) or not entry.get("exists"):
        return None
    parsed = entry.get("parsed")
    if isinstance(parsed, dict):
        return parsed.get("questions")
    if isinstance(parsed, list):
        return parsed
    return None


def _parsed_tasks(package: dict) -> list | None:
    documents = package.get("documents")
    if not isinstance(documents, dict):
        return None
    entry = documents.get("tasks")
    if not isinstance(entry, dict) or not entry.get("exists"):
        return None
    parsed = entry.get("parsed")
    if isinstance(parsed, dict):
        return parsed.get("tasks")
    if isinstance(parsed, list):
        return parsed
    return None


def _iter_tasks(package: dict) -> list[dict]:
    parsed = _parsed_tasks(package)
    if not parsed:
        return []
    return [task for task in parsed if isinstance(task, dict)]


def _check_clarifications(package: dict) -> list[dict]:
    questions = _parsed_questions(package)
    if questions is None:
        return []
    errors: list[dict] = []
    known_reqs = _requirement_ids(package)
    seen_question_ids: set[str] = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            errors.append(
                _err("SDD_MALFORMED", f"clarifications.{index}", "questão deve ser um objeto")
            )
            continue
        q_id = question.get("id")
        base = f"clarifications.{q_id}" if q_id else f"clarifications.{index}"
        if not _valid_id(q_id):
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    base,
                    f"ID de question inválido: {q_id!r}; use apenas letras, números, "
                    "'.' , '_' ou '-' (sem espaços ou separadores de caminho)",
                )
            )
        if isinstance(q_id, str):
            if q_id in seen_question_ids:
                errors.append(
                    _err("SDD_MALFORMED", base, f"ID de question duplicado: {q_id}")
                )
            seen_question_ids.add(q_id)
        for field in _QUESTION_REQUIRED_FIELDS:
            if question.get(field) in (None, ""):
                errors.append(
                    _err("SDD_MALFORMED", base, f"questão sem campo obrigatório '{field}'")
                )
        severity = question.get("severity")
        if severity is not None and severity not in QUESTION_SEVERITIES:
            errors.append(
                _err("SDD_MALFORMED", base, f"severity inválida: {severity!r}")
            )
        status = question.get("status")
        if status is not None and status not in QUESTION_STATUSES:
            errors.append(_err("SDD_MALFORMED", base, f"status inválido: {status!r}"))
        for ref in question.get("requirement_ids") or []:
            if ref not in known_reqs:
                errors.append(
                    _err(
                        "SDD_MALFORMED",
                        base,
                        f"questão referencia requisito inexistente: {ref}",
                    )
                )
        if severity == "blocking":
            if status == "open":
                errors.append(
                    _err(
                        "SDD_OPEN_QUESTION",
                        base,
                        f"questão bloqueante aberta: {q_id} — {question.get('question')}",
                    )
                )
            elif status == "resolved" and not (question.get("answer") and question.get("source")):
                errors.append(
                    _err(
                        "SDD_OPEN_QUESTION",
                        base,
                        f"questão {q_id} marcada resolved sem answer/source; permanece bloqueante",
                    )
                )
            elif status == "accepted_assumption" and not (
                question.get("answer") and question.get("owner") and question.get("review_condition")
            ):
                errors.append(
                    _err(
                        "SDD_OPEN_QUESTION",
                        base,
                        f"questão {q_id} accepted_assumption sem justificativa (answer), "
                        "owner ou review_condition; não disfarça bloqueante aberta",
                    )
                )
    return errors


def _check_tasks(package: dict) -> list[dict]:
    parsed = _parsed_tasks(package)
    if parsed is None:
        return []
    errors: list[dict] = []
    known_reqs = _requirement_ids(package)
    tasks_by_id: dict[str, dict] = {}
    seen: set[str] = set()
    tasks: list[dict] = []
    for index, task in enumerate(parsed):
        if not isinstance(task, dict):
            errors.append(_err("SDD_MALFORMED", f"tasks.{index}", "task deve ser um objeto"))
            continue
        tasks.append(task)
        task_id = task.get("id")
        base = f"tasks.{task_id}" if task_id else f"tasks.{index}"
        for field in _TASK_REQUIRED_FIELDS:
            if task.get(field) in (None, ""):
                errors.append(
                    _err(
                        "SDD_MALFORMED",
                        base,
                        f"task com campo obrigatório '{field}' ausente ou nulo",
                    )
                )
        if not _valid_id(task_id):
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    base,
                    f"ID de task inválido: {task_id!r}; use apenas letras, números, "
                    "'.' , '_' ou '-' (sem espaços ou separadores de caminho)",
                )
            )
        if task_id in seen:
            errors.append(
                _err("SDD_MALFORMED", base, f"ID de task duplicado: {task_id}")
            )
        seen.add(str(task_id))
        if isinstance(task_id, str):
            tasks_by_id[task_id] = task
        points = task.get("points")
        if points is not None and (
            isinstance(points, bool) or not isinstance(points, int) or points not in VALID_POINTS
        ):
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    f"{base}.points",
                    f"points inválido: {points!r}; permitidos: {list(VALID_POINTS)}",
                )
            )
        for ref in task.get("requirement_ids") or []:
            if ref not in known_reqs:
                errors.append(
                    _err("SDD_MALFORMED", base, f"task referencia requisito inexistente: {ref}")
                )
    errors.extend(_check_dependency_graph(tasks_by_id))
    errors.extend(_check_requirements_coverage(package, tasks))
    return errors


def _check_dependency_graph(tasks_by_id: dict[str, dict]) -> list[dict]:
    return _dependency_errors(tasks_by_id)


def _dependency_errors(tasks_by_id: dict[str, dict]) -> list[dict]:
    errors: list[dict] = []
    for task_id, task in tasks_by_id.items():
        for dep in task.get("depends_on") or []:
            if dep not in tasks_by_id:
                errors.append(
                    _err(
                        "SDD_DEPENDENCY_CYCLE",
                        f"tasks.{task_id}.depends_on",
                        f"task {task_id} depende de task inexistente: {dep}",
                    )
                )
    cycle = _find_cycle(tasks_by_id)
    if cycle:
        errors.append(
            _err(
                "SDD_DEPENDENCY_CYCLE",
                "tasks",
                "ciclo de dependências: " + " -> ".join(cycle),
            )
        )
    return errors


def _find_cycle(tasks_by_id: dict[str, dict]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {task_id: WHITE for task_id in tasks_by_id}

    def visit(node: str, stack: list[str]) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for dep in tasks_by_id[node].get("depends_on") or []:
            if dep not in tasks_by_id:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep, stack)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for task_id in tasks_by_id:
        if color[task_id] == WHITE:
            found = visit(task_id, [])
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Política SDD (contrato para T4; validação estrutural aqui)
# ---------------------------------------------------------------------------


def validate_policy(policy: dict) -> list[dict]:
    """Valida a política SDD por projeto contra ``sdd-policy.schema.json``.

    Política inválida, ausente ou ilegível falha fechada
    (``SDD_POLICY_INVALID``).
    """
    if not isinstance(policy, dict):
        return [_err("SDD_POLICY_INVALID", "policy", "política SDD deve ser um objeto (dict)")]
    schema = _load_schema("sdd-policy.schema.json")
    if schema is None:
        return [
            _err(
                "SDD_POLICY_INVALID",
                "policy",
                "esquema contracts/sdd-policy.schema.json indisponível (falha fechada)",
            )
        ]
    errors = []
    validator = jsonschema.Draft202012Validator(schema)
    for verror in sorted(validator.iter_errors(policy), key=str):
        parts = [str(part) for part in verror.absolute_path]
        path = "policy." + ".".join(parts) if parts else "policy"
        errors.append(
            _err("SDD_POLICY_INVALID", path, f"violação de schema: {verror.message}")
        )
    return errors
