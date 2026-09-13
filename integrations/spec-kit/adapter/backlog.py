"""Projeção determinística de tarefas e backlog SDD (T6) — integration-engineer.

Este módulo é puro: sem rede, sem subprocess, sem escrita em disco e sem
nenhuma conexão paralela ao DevOps. A integração existente com Boards
(``integrations/devops_platform_connector.py``) permanece a única via de
acesso remoto; aqui, o estado remoto entra apenas como *snapshot em
memória* para validação. Writes externos dependem de autorização
explícita fora deste módulo — nenhum caminho de escrita existe aqui.

Superfície (contrato da seção 6 do plano):

- ``project_tasks(items: list[dict]) -> str`` — projeção determinística
  das tarefas validadas em conteúdo ``tasks.md``. Mesma entrada ->
  saída byte-idêntica.
- ``validate_backlog(items) -> list[dict]`` — estrutura da lista de
  tarefas isolada (campos obrigatórios, ``points``, IDs, dependências).
- ``check_coverage(requirements, items) -> list[dict]`` — requisito de
  código sem tarefa com teste correspondente.
- ``check_remote_ids(items) -> list[dict]`` — conflito de ``remote_id``.
- ``validate_remote_state(items, snapshot, boards_available) -> list[dict]``
  — semântica de canonicalidade contra snapshot remoto.

Semântica de canonicalidade (seção 7/T6):

- **Com Boards habilitado**: ``tasks.md`` é uma visão GERADA e os IDs
  remotos (``remote_id``) são canônicos; um snapshot consistente com
  ``tasks.yaml`` é aceito, divergente gera conflito.
- **Sem Boards**: ``tasks.yaml`` é a fonte canônica; um snapshot
  (dado possivelmente vencido) nunca é promovido a evidência de
  aprovação — todo snapshot oferecido com o Boards indisponível é
  rejeitado com ``SDD_REMOTE_STALE``. Snapshot ausente não é erro.

Regras de ordenação da projeção (garantem saída byte-idêntica):

1. Tarefas são ordenadas lexicograficamente por ``id``;
2. Seções de requisito são ordenadas lexicograficamente; cada tarefa
   aparece sob o MENOR requisito (ordenado) que referencia — não há
   duplicação de tarefa entre seções;
3. Tarefas sem ``requirement_ids`` vão para a seção ``SEM-REQUISITO``,
   sempre após as seções de requisito e antes da tabela de status;
4. Listas de valor (``depends_on``, ``paths``, ``test_ids``,
   ``evidence``, ``acceptance_ids``) preservam a ordem declarada em
   ``tasks.yaml`` (a ordem da lista faz parte da entrada);
5. Nenhum timestamp ou dado volátil entra na saída.

Códigos de erro (formato ``{"code", "path", "message"}``; códigos
herdados de T2/T4 conforme plano, novos documentados aqui):

- ``SDD_MALFORMED``        — estrutura inválida (campo ausente, id
  duplicado/inválido, ``points`` fora de {1,2,3,5,8}, item não-dict,
  snapshot remoto não-mapeamento).
- ``SDD_COVERAGE_GAP``     — requisito de código sem tarefa com teste
  correspondente.
- ``SDD_DEPENDENCY_CYCLE`` — ``depends_on`` inexistente ou em ciclo
  (semântica idêntica a ``adapter.validation``).
- ``SDD_REMOTE_CONFLICT``  — NOVO (T6): duas tarefas compartilham o
  mesmo ``remote_id``, ou o snapshot do Boards diverge do
  ``remote_id`` declarado em ``tasks.yaml`` (nos dois sentidos: valor
  divergente, tarefa desconhecida no snapshot ou task com
  ``remote_id`` declarado omitida do snapshot).
- ``SDD_REMOTE_STALE``     — NOVO (T6): snapshot remoto oferecido com o
  Boards indisponível; dados vencidos não são evidência de aprovação.
"""

from __future__ import annotations

import re

__all__ = [
    "project_tasks",
    "validate_backlog",
    "check_coverage",
    "check_remote_ids",
    "validate_remote_state",
]

VALID_POINTS = (1, 2, 3, 5, 8)

# Mesmos campos obrigatórios de adapter.validation._TASK_REQUIRED_FIELDS
# (evidence e remote_id permanecem opcionais, conforme o contrato).
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

# Mesma política de IDs de adapter.validation (MINOR-3).
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_NO_REQUIREMENT_GROUP = "SEM-REQUISITO"

_HEADER_LINES = (
    "# Tasks — visão gerada (Spec Kit adapter)",
    "",
    "> Projeção determinística de `tasks.yaml`. Não editar à mão.",
    "> Com Boards habilitado, os IDs remotos (`remote_id`) são canônicos e este",
    "> documento é apenas visão gerada; sem Boards, `tasks.yaml` é a fonte",
    "> canônica. Dados remotos vencidos nunca constituem evidência de aprovação.",
    "",
)


def _err(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def _task_id(task: dict) -> str:
    task_id = task.get("id")
    return task_id if isinstance(task_id, str) else ""


def _fmt_list(values) -> str:
    if not values:
        return "-"
    return ", ".join(str(value) for value in values)


def _sorted_tasks(items) -> list[dict]:
    return sorted((item for item in items if isinstance(item, dict)), key=_task_id)


# ---------------------------------------------------------------------------
# project_tasks — projeção determinística (contrato do plano, seção 6)
# ---------------------------------------------------------------------------


def project_tasks(items: list[dict]) -> str:
    """Projeta tarefas validadas em conteúdo ``tasks.md`` determinístico.

    Pré-condição: ``items`` é entrada validada — livre de IDs
    duplicados e campos obrigatórios presentes (``validate_backlog``
    rejeita duplicados e malformações antes da projeção). A garantia de
    determinismo byte-a-byte (mesma entrada -> mesma saída) vale para
    entradas sem IDs duplicados; entradas inválidas têm projeção ainda
    determinística, porém sem significado contratado.

    Pura: não valida (use ``validate_backlog``/``validate_package`` antes),
    não lê nem escreve arquivos, não acessa rede.
    """
    tasks = _sorted_tasks(items or [])

    groups: dict[str, list[dict]] = {}
    for task in tasks:
        reqs = sorted(
            req for req in (task.get("requirement_ids") or []) if isinstance(req, str)
        )
        groups.setdefault(reqs[0] if reqs else _NO_REQUIREMENT_GROUP, []).append(task)

    lines: list[str] = list(_HEADER_LINES)
    if not tasks:
        lines.append("(nenhuma tarefa)")
        lines.append("")

    for req in sorted(groups):
        lines.append(f"## {req}")
        lines.append("")
        for task in groups[req]:
            lines.extend(_render_task(task))
        lines.append("")

    lines.append("## Tabela de status")
    lines.append("")
    lines.append("| id | status | owner | points | depends_on | remote_id |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for task in tasks:
        lines.append(_status_row(task))
    lines.append("")

    return "\n".join(lines)


def _render_task(task: dict) -> list[str]:
    task_id = _task_id(task)
    remote_id = task.get("remote_id")
    summary = (
        f"- {task_id} | owner={task.get('owner') or '-'}"
        f" | points={task.get('points') if task.get('points') is not None else '-'}"
        f" | status={task.get('status') or '-'}"
        f" | depends_on={_fmt_list(task.get('depends_on'))}"
        f" | remote_id={remote_id if remote_id else '-'}"
    )
    return [
        summary,
        f"  acceptance_ids: {_fmt_list(task.get('acceptance_ids'))}",
        f"  paths: {_fmt_list(task.get('paths'))}",
        f"  test_ids: {_fmt_list(task.get('test_ids'))}",
        f"  evidence: {_fmt_list(task.get('evidence'))}",
    ]


def _status_row(task: dict) -> str:
    remote_id = task.get("remote_id")
    return (
        f"| {_task_id(task)} | {task.get('status') or '-'}"
        f" | {task.get('owner') or '-'}"
        f" | {task.get('points') if task.get('points') is not None else '-'}"
        f" | {_fmt_list(task.get('depends_on'))}"
        f" | {remote_id if remote_id else '-'} |"
    )


# ---------------------------------------------------------------------------
# validate_backlog — estrutura e dependências da lista de tarefas
# ---------------------------------------------------------------------------


def validate_backlog(items: list[dict]) -> list[dict]:
    """Valida a lista de tarefas isolada (sem ``package.json``).

    Verificação de cobertura contra requisitos e de referências a
    requisitos pertence a ``check_coverage``/``validate_package``; aqui
    apenas estrutura interna da lista e o grafo de dependências.
    """
    if not isinstance(items, list):
        return [_err("SDD_MALFORMED", "tasks", "lista de tarefas deve ser uma lista (list)")]

    errors: list[dict] = []
    tasks_by_id: dict[str, dict] = {}
    seen: set[str] = set()

    for index, task in enumerate(items):
        if not isinstance(task, dict):
            errors.append(_err("SDD_MALFORMED", f"tasks.{index}", "task deve ser um objeto"))
            continue
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
        if not (isinstance(task_id, str) and _ID_PATTERN.match(task_id)):
            errors.append(
                _err(
                    "SDD_MALFORMED",
                    base,
                    f"ID de task inválido: {task_id!r}; use apenas letras, números, "
                    "'.' , '_' ou '-' (sem espaços ou separadores de caminho)",
                )
            )
        elif task_id in seen:
            errors.append(_err("SDD_MALFORMED", base, f"ID de task duplicado: {task_id}"))
        if isinstance(task_id, str) and task_id not in tasks_by_id:
            tasks_by_id[task_id] = task
        seen.add(str(task_id))
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

    errors.extend(_dependency_errors(tasks_by_id))
    return errors


def _dependency_errors(tasks_by_id: dict[str, dict]) -> list[dict]:
    """Dependência inexistente/ciclo — semântica de adapter.validation."""
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
            _err("SDD_DEPENDENCY_CYCLE", "tasks", "ciclo de dependências: " + " -> ".join(cycle))
        )
    return errors


def _find_cycle(tasks_by_id: dict[str, dict]) -> list[str] | None:
    white, gray, black = 0, 1, 2
    color = {task_id: white for task_id in tasks_by_id}

    def visit(node: str, stack: list[str]) -> list[str] | None:
        color[node] = gray
        stack.append(node)
        for dep in tasks_by_id[node].get("depends_on") or []:
            if dep not in tasks_by_id:
                continue
            if color[dep] == gray:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == white:
                found = visit(dep, stack)
                if found:
                    return found
        stack.pop()
        color[node] = black
        return None

    for task_id in tasks_by_id:
        if color[task_id] == white:
            found = visit(task_id, [])
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Cobertura — requisito de código exige tarefa com teste
# ---------------------------------------------------------------------------


def check_coverage(requirements: list[dict], items: list[dict]) -> list[dict]:
    """Requisito de código sem tarefa com teste correspondente -> ``SDD_COVERAGE_GAP``.

    Mesma semântica de ``adapter.validation._check_requirements_coverage``:
    apenas requisitos com ``classification == "code"`` exigem teste;
    requisitos documentais ficam isentos (evidência de revisão é própria
    do fluxo de gates).
    """
    if not isinstance(requirements, list) or not isinstance(items, list):
        return [
            _err(
                "SDD_MALFORMED",
                "requirements",
                "requirements e items devem ser listas (list)",
            )
        ]
    covered: set[str] = set()
    for task in items:
        if isinstance(task, dict) and task.get("test_ids"):
            covered.update(
                req for req in (task.get("requirement_ids") or []) if isinstance(req, str)
            )
    errors: list[dict] = []
    for req in requirements:
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


# ---------------------------------------------------------------------------
# remote_id — conflitos e indisponibilidade do Boards
# ---------------------------------------------------------------------------


def _remote_path(task: dict) -> str:
    """Path de erro diagnosticável para ``remote_id`` de uma task.

    MINOR-3: task sem ``id`` não gera path vazio (``tasks..remote_id``);
    o placeholder explicita o ``remote_id`` envolvido para auditoria.
    """
    task_id = _task_id(task)
    if task_id:
        return f"tasks.{task_id}.remote_id"
    return f"tasks.<sem-id>(remote_id={task.get('remote_id')!r}).remote_id"


def check_remote_ids(items: list[dict]) -> list[dict]:
    """Detecta ``remote_id`` duplicado entre tarefas -> ``SDD_REMOTE_CONFLICT``.

    Dois work items do Boards não podem ser reivindicados pela mesma
    tarefa: o mapeamento tarefa -> item remoto deve ser injetivo.
    Tarefas sem ``remote_id`` são ignoradas (comportamento sem Boards).
    """
    if not isinstance(items, list):
        return [_err("SDD_MALFORMED", "tasks", "lista de tarefas deve ser uma lista (list)")]
    seen: dict[str, str] = {}
    errors: list[dict] = []
    for task in items:
        if not isinstance(task, dict):
            continue
        remote_id = task.get("remote_id")
        if not remote_id:
            continue
        task_id = _task_id(task)
        owner = seen.get(str(remote_id))
        if owner is not None:
            errors.append(
                _err(
                    "SDD_REMOTE_CONFLICT",
                    _remote_path(task),
                    f"remote_id {remote_id!r} já reivindicado pela task {owner}",
                )
            )
        else:
            seen[str(remote_id)] = task_id
    return errors


def validate_remote_state(
    items: list[dict], snapshot, boards_available: bool
) -> list[dict]:
    """Valida o snapshot remoto contra a semântica de canonicalidade.

    - ``boards_available=False`` e snapshot presente -> ``SDD_REMOTE_STALE``:
      dado remoto possivelmente vencido nunca é promovido a evidência de
      aprovação; a fonte canônica passa a ser ``tasks.yaml``.
    - ``boards_available=False`` e snapshot ausente (``None``) -> ``[]``:
      modo sem Boards, ``tasks.yaml`` canônico, nenhum dado remoto usado.
    - ``boards_available=True``: o snapshot (mapeamento ``task_id ->
      remote_id``) deve coincidir com o ``remote_id`` declarado em
      ``tasks.yaml``, nos DOIS sentidos — divergência de valor, tarefa
      desconhecida no snapshot OU task que declara ``remote_id`` omitida
      do snapshot geram ``SDD_REMOTE_CONFLICT`` (IDs remotos são
      canônicos, mas a divergência exige reconciliação, não aceitação
      silenciosa; omissão pode indicar work item excluído remotamente).

    ``snapshot=None`` com Boards disponível também é ``[]`` (nada a
    reconciliar). Nenhuma função deste módulo devolve snapshot remoto
    como evidência de aprovação.
    """
    if snapshot is None:
        return []
    if not boards_available:
        return [
            _err(
                "SDD_REMOTE_STALE",
                "remote.snapshot",
                "Boards indisponível: snapshot remoto vencido não é aceito como "
                "evidência de aprovação; fonte canônica: tasks.yaml",
            )
        ]
    if not isinstance(snapshot, dict):
        return [
            _err(
                "SDD_MALFORMED",
                "remote.snapshot",
                "snapshot remoto deve ser um mapeamento task_id -> remote_id",
            )
        ]
    known: dict[str, tuple[str, str]] = {}
    for task in items or []:
        if not isinstance(task, dict) or not task.get("remote_id"):
            continue
        task_id = _task_id(task)
        if not task_id:
            continue  # task sem id é malformada; validate_backlog a reporta
        known[task_id] = (str(task["remote_id"]), _remote_path(task))

    errors: list[dict] = []
    for task_id, remote_id in snapshot.items():
        declared = known.get(str(task_id))
        if declared is None:
            errors.append(
                _err(
                    "SDD_REMOTE_CONFLICT",
                    f"remote.snapshot.{task_id}",
                    f"snapshot referencia task desconhecida ou sem remote_id: {task_id}",
                )
            )
        elif declared[0] != str(remote_id):
            errors.append(
                _err(
                    "SDD_REMOTE_CONFLICT",
                    declared[1],
                    f"remote_id do Boards ({remote_id!r}) diverge do declarado em "
                    f"tasks.yaml ({declared[0]!r}); reconciliar antes de prosseguir",
                )
            )
    # Divergência bidirecional (follow-up MAJOR do revisor): task que declara
    # remote_id e foi OMITIDA do snapshot também é conflito — nunca retorna []
    # silenciosamente, pois esconde work item excluído remotamente.
    for task_id, (remote_id, path) in known.items():
        if str(task_id) not in snapshot:
            errors.append(
                _err(
                    "SDD_REMOTE_CONFLICT",
                    path,
                    f"task {task_id} declara remote_id {remote_id!r} ausente do "
                    "snapshot remoto",
                )
            )
    return errors
