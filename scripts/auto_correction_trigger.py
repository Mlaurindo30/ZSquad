"""Gatilho governado de auto-correção do squad (falha -> proposta Prime-style).

O que é: ponte entre falhas do squad (gate rejeitado, work item bloqueado,
relatório de incidente) e a biblioteca ``scripts/auto_correction.py`` — carrega a
evidência da falha, monta a conversa de correção com limites duros, chama um LLM
da cascata (``scripts/llm_providers.py``) via ``plan_refinement`` e, opcionalmente,
aplica a proposta com rollback automático ante qualquer exceção ou violação de política.

Responsabilidade: ``correct_from_failure(kind, source_path, runtime_root, router,
*, apply=False)`` carrega a fonte conforme o ``kind``, propõe (default) ou aplica
a proposta de refinement e devolve um dicionário de status; ``main(argv)``
expõe isso como CLI com códigos de saída 0/1/2.

Pra que serve: fechar o ciclo de auto-correção inspirado no Prime Agent sem
inventar runtime novo — reaproveita exatamente ``plan_refinement``,
``apply_refinement_proposal`` e ``rollback`` da biblioteca existente, com o LLM
amarrado por regras anti-fabricação (só propor o que a evidência justifica).

Comportamento em falha: LLM indisponível/JSON inválido -> exceção propagada pela
biblioteca (CLI imprime ``CORRECTION_ERROR`` e sai 1); exceção durante o apply OU
violação dos limites duros pós-apply (kinds fora de skill/config/script, path
absoluto fora do runtime root) -> ``rollback`` com ``baseline_state=plan.baseline_state``
e retorno ``{"status": "rolled-back", "error": ...}``; falha do próprio rollback é
registrada em ``rollback.rollback_error`` sem mudar o status; kind inválido ->
``ValueError``; fonte ilegível/inexistente -> exceção de I/O propagada.
Recursos deliberadamente ADIADOS (fora deste arquivo): loop contínuo de monitoramento
(observar ``work/`` e disparar correções periodicamente — aqui só há resiliência
reativa por exceção) e escritor de artefatos de governança (GD-*.yaml, ledger,
deltas de memória — quem consome o dict retornado decide onde registrá-lo).

Conexões: consome ``scripts/auto_correction.py`` (plan/apply/rollback/HarnessState),
``scripts/llm_providers.py`` (LLMRouter/DEFAULT_PROVIDERS na CLI), lê YAML de gate
(``contracts/gate-decision.schema.json``) e de status.yaml; não escreve em nenhum
artigo governado — retorna dados para o chamador decidir.

Dependências & Imports: biblioteca padrão (``argparse``, ``json``, ``os``, ``sys``,
``pathlib``, ``typing``), ``yaml`` (PyYAML, já dependência do projeto) e os módulos
irmãos ``scripts.auto_correction`` e ``scripts.llm_providers``. Nenhuma dependência nova.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Bootstrap incondicional (sem branch): executa tanto como módulo de pacote
# (pytest) quanto como script direto (python scripts/auto_correction_trigger.py).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from scripts.auto_correction import (
    HarnessState,
    RefinementHistoryEntry,
    RefinementPlan,
    RefinementResult,
    apply_refinement_proposal,
    plan_refinement,
    rollback,
)
from scripts.llm_providers import DEFAULT_PROVIDERS, LLMRouter

VALID_KINDS = ("gate-rejected", "blocked", "incident")
ALLOWED_EDIT_KINDS = frozenset({"skill", "config", "script"})
USER_INSTRUCTION = "propose minimal corrections"

TRIGGER_SYSTEM_PROMPT = """You are a corrective engineer for the agent squad runtime.
A failure was detected (rejected gate decision, blocked work item, or incident report)
and you must propose MINIMAL corrections grounded ONLY in the failure evidence provided.

Anti-fabrication rules:
- Propose only edits strictly justified by the failure evidence below.
- Never invent files, APIs, symbols, commands or metrics that are not evidenced.
- If no correction is justified, return an empty edits array with a clear rationale.

Hard limits (violations are detected after apply and rolled back):
- Edit kinds restricted to: "skill", "config", "script".
- Every optional "path" must stay INSIDE the runtime root informed in the task.
- NEVER propose deploy, push, credential changes, production data access or any
  external action.

Return ONLY a valid JSON object:
{"summary": "...", "rationale": "...", "expected_outcome": "...",
 "edits": [{"action": "create"|"update"|"delete", "kind": "skill"|"config"|"script",
            "id": "...", "title": "...", "content": "...", "path": "...", "reason": "..."}]}
"""


# ---------------------------------------------------------------------------
# Loaders por kind
# ---------------------------------------------------------------------------


def _load_gate_failure(path: Path) -> Dict[str, Any]:
    """Extrai decisão e critérios reprovados de um YAML de gate-decision.

    O que é: loader do kind ``gate-rejected`` seguindo
    ``contracts/gate-decision.schema.json`` (``decision`` + ``criteria[{name,result}]``).

    Responsabilidade: coletar o campo ``decision`` e APENAS os critérios com
    ``result == "fail"``, mantendo nome/nota para contexto do LLM.

    Pra que serve: dar à correção a evidência mínima real — nada de critérios
    aprovados poluindo o resumo da falha.

    Comportamento em falha: YAML ilegível/malformado levanta a exceção de leitura/
    parsing; arquivo sem chave ``criteria`` rende lista vazia; entrada sem contrato
    (critério não-dicionário) levanta AttributeError do ``.get``.

    Conexões: chamado por ``_load_failure``; saída vai para ``_build_user_prompt``.

    Dependências & Imports: ``yaml.safe_load``, ``pathlib.Path.read_text``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    failed_criteria: List[Dict[str, Any]] = []
    for crit in data.get("criteria", []):
        if crit.get("result") == "fail":
            failed_criteria.append({"name": crit.get("name"), "note": crit.get("note")})
    return {
        "decision_id": data.get("decision_id"),
        "gate_id": data.get("gate_id"),
        "work_item_id": data.get("work_item_id"),
        "decision": data.get("decision"),
        "failed_criteria": failed_criteria,
    }


def _load_blocked_status(path: Path) -> Dict[str, Any]:
    """Extrai ``id``/``state``/``next_action`` de um status.yaml bloqueado.

    O que é: loader do kind ``blocked`` sobre o ``status.yaml`` do work item.

    Responsabilidade: ler só os três campos governados que descrevem o bloqueio.

    Pra que serve: alimentar a proposta de correção com o estado real do work item.

    Comportamento em falha: I/O/YAML malformado propaga exceção; campos ausentes
    viram ``null`` explícito no payload (nada inventado).

    Conexões: chamado por ``_load_failure``; saída vai para ``_build_user_prompt``.

    Dependências & Imports: ``yaml.safe_load``, ``pathlib.Path.read_text``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        "id": data.get("id"),
        "state": data.get("state"),
        "next_action": data.get("next_action"),
    }


def _load_incident_text(path: Path) -> str:
    """Lê o relatório de incidente como texto bruto.

    O que é: loader do kind ``incident``; não interpreta estrutura alguma.

    Responsabilidade: devolver o conteúdo integral do arquivo como string.

    Pra que serve: incidentes são narrativas livres; qualquer parsing inventaria
    estrutura que o relatório não garante.

    Comportamento em falha: arquivo ausente/ilegível levanta OSError.

    Conexões: chamado por ``_load_failure``; saída vai para ``_build_user_prompt``.

    Dependências & Imports: ``pathlib.Path.read_text``.
    """
    return path.read_text(encoding="utf-8")


def _load_failure(kind: str, source_path: str) -> Any:
    """Despacha o loader correto para o kind informado.

    O que é: ponto único de despacho dos três kinds suportados.

    Responsabilidade: mapear kind -> loader e rejeitar kind desconhecido com
    ``ValueError`` antes de tocar o filesystem.

    Pra que serve: manter ``correct_from_failure`` enxuto e o contrato de kinds
    em uma única tabela (``VALID_KINDS``).

    Comportamento em falha: kind fora de ``VALID_KINDS`` -> ``ValueError`` imediato;
    erros dos loaders propagam sem alteração.

    Conexões: chamada por ``correct_from_failure``; delega aos três loaders.

    Dependências & Imports: nenhuma além do próprio módulo.
    """
    path = Path(source_path)
    if kind == "gate-rejected":
        return _load_gate_failure(path)
    if kind == "blocked":
        return _load_blocked_status(path)
    if kind == "incident":
        return _load_incident_text(path)
    raise ValueError(
        f"kind inválido: {kind!r}; esperado um de: {', '.join(VALID_KINDS)}"
    )


# ---------------------------------------------------------------------------
# Prompt da conversa de correção
# ---------------------------------------------------------------------------


def _build_user_prompt(kind: str, payload: Any, runtime_root: str) -> str:
    """Monta a mensagem do usuário com a evidência e a instrução fixa.

    O que é: serialização determinística do resumo da falha + instrução
    ``propose minimal corrections`` + limite de diretório.

    Responsabilidade: representar a evidência sem embelezamento — dicts em JSON
    ordenado, texto de incidente cru.

    Pra que serve: garantir que o LLM receba exatamente os fatos carregados e o
    runtime root contra o qual paths serão validados.

    Comportamento em falha: payload não-dict e não-str nunca ocorre (despacho
    fechado em ``_load_failure``); serialização JSON de tipos básicos não falha.

    Conexões: consumida por ``correct_from_failure`` ao montar ``messages``.

    Dependências & Imports: ``json.dumps``.
    """
    if isinstance(payload, dict):
        evidence = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        evidence = payload
    return "\n".join(
        [
            f"Failure kind: {kind}",
            "Evidence:",
            evidence,
            f"Runtime root: {runtime_root}",
            "",
            USER_INSTRUCTION,
        ]
    )


def _make_llm_call(router: Any):
    """Adapta ``router.complete`` à assinatura ``llm_call`` da biblioteca.

    O que é: closure que traduz ``llm_call(system_prompt, user_prompt, signal=None)``
    para ``router.complete(system_prompt, user_prompt, expect_json=True)["content"]``.

    Responsabilidade: devolver SEMPRE a string JSON crua do modelo (o parse é
    responsabilidade de ``auto_correction._parse_proposal``).

    Pra que serve: desacoplar o gatilho do provedor — qualquer objeto duck-typed
    com ``.complete`` serve (em testes, um fake sem rede).

    Comportamento em falha: erros do provedor (ex.: ``ProviderError``) propagam e
    são reempacotados pela biblioteca como ``RuntimeError("Refinement LLM call failed")``.

    Conexões: passada como ``llm_call`` para ``plan_refinement``.

    Dependências & Imports: nenhuma além do ``router`` injetado.
    """

    def llm_call(system_prompt: str, user_prompt: str, signal: Optional[str] = None) -> str:
        return router.complete(system_prompt, user_prompt, expect_json=True)["content"]

    return llm_call


# ---------------------------------------------------------------------------
# Limites duros pós-apply
# ---------------------------------------------------------------------------


def _escapes_runtime_root(path: Optional[str], root: Path) -> bool:
    """Verifica se um path informado foge do runtime root.

    O que é: guarda de contenção de caminho para edições já aplicadas.

    Responsabilidade: paths relativos são, por convenção, relativos ao root
    (permitidos); paths absolutos precisam resolver DENTRO do root.

    Pra que serve: impedir que uma proposta do LLM aponte para arquivos fora da
    instalação governada mesmo quando o kind está na lista permitida.

    Comportamento em falha: path vazio/ausente -> permitido (não há como violar);
    path absoluto fora do root (ou em outra unidade, caso de ``ValueError`` do
    ``relative_to``) -> considerado escape.

    Conexões: chamada por ``_hard_limit_violation``.

    Dependências & Imports: ``os.path.normcase``, ``pathlib.Path``.
    """
    if not path:
        return False
    candidate = Path(path)
    if not candidate.is_absolute():
        return False
    try:
        resolved = Path(os.path.normcase(str(candidate.resolve())))
        root_normalized = Path(os.path.normcase(str(root)))
        resolved.relative_to(root_normalized)
    except ValueError:
        return True
    return False


def _hard_limit_violation(applied_edits: List[Any], runtime_root: Path) -> Optional[str]:
    """Encontra a primeira edição aplicada que viole os limites duros do gatilho.

    O que é: verificação pós-apply (defense in depth) sobre ``result.applied_edits``.

    Responsabilidade: rejeitar edições aplicadas com kind fora de
    skill/config/script ou com path absoluto fora do runtime root.

    Pra que serve: o system prompt limita o LLM, mas limites são ENFORÇADOS aqui —
    a biblioteca aceita kinds como "file"/"decision", então o gatilho detecta e
    dispara rollback.

    Comportamento em falha: devolve mensagem descritiva da primeira violação ou
    ``None`` se todas as edições aplicadas estão conformes; edições não-aplicadas
    são ignoradas (já falharam na validação da biblioteca).

    Conexões: chamada por ``correct_from_failure`` dentro do bloco de apply.

    Dependências & Imports: ``typing.Optional``.
    """
    root = runtime_root.resolve()
    for record in applied_edits:
        if not record.applied:
            continue
        if record.kind not in ALLOWED_EDIT_KINDS:
            return f"edit '{record.edit_id}' usa kind fora da política: {record.kind}"
        if _escapes_runtime_root(record.path, root):
            return f"edit '{record.edit_id}' escapa do runtime root: {record.path}"
    return None


def _partial_result_from_plan(plan: RefinementPlan) -> RefinementResult:
    """Sintetiza um ``RefinementResult`` vazio a partir do plano, para rollback seguro.

    O que é: adaptador usado quando ``apply_refinement_proposal`` levanta exceção
    ANTES de devolver um resultado.

    Responsabilidade: produzir um resultado com ``applied_edits=[]`` e os metadados
    do plano, pois ``rollback`` espera um ``RefinementResult`` (um ``RefinementPlan``
    bruto crasharia: não tem ``applied_edits``).

    Pra que serve: honrar "qualquer exceção no apply -> rollback" sem inventar
    edições que não ocorreram — o rollback de uma lista vazia é um no-op seguro
    que ainda registra o evento no histórico.

    Comportamento em falha: nenhum (construção pura de dataclass).

    Conexões: chamada por ``correct_from_failure`` no ramo de exceção sem resultado.

    Dependências & Imports: ``scripts.auto_correction.RefinementResult``.
    """
    return RefinementResult(
        id=plan.id,
        summary=plan.proposal.summary,
        rationale=plan.proposal.rationale,
        expected_outcome=plan.proposal.expected_outcome,
        applied_edits=[],
        rollback_of=plan.rollback_of,
        scope=plan.rollback_scope,
    )


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------


def correct_from_failure(
    kind: str,
    source_path: str,
    runtime_root: str,
    router: Any,
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """Transforma uma falha do squad em proposta de correção (opcionalmente aplicada).

    O que é: função central do gatilho — carrega a evidência, conversa com o LLM
    da cascata via ``plan_refinement`` e devolve ditado de status.

    Responsabilidade: para ``apply=False`` devolver
    ``{"status": "proposed", "summary", "edits_count", "proposal"}``; para
    ``apply=True`` aplicar via ``apply_refinement_proposal(plan, state, history)``,
    checar limites duros e devolver ``{"status": "applied", "applied_edits_count": n}``;
    QUALQUER exceção nesse caminho aciona ``rollback(target, state, history,
    baseline_state=plan.baseline_state)`` (com resultado real se houver, ou
    sintetizado do plano) e devolve ``{"status": "rolled-back", "error": str(exc),
    "id", "summary", "rollback": {...}}``.

    Pra que serve: ponto único para coordenadores transformarem gate rejeitado
    (YAML), work item bloqueado (status.yaml) ou incidente (texto) em refinamento
    Prime-style sem tocar em produção.

    Comportamento em falha: kind inválido -> ``ValueError``; fonte ilegível ->
    exceção de I/O; LLM falho -> exceção da biblioteca; tudo no caminho de apply
    converte para rollback auditável (nunca meia-aplicação silenciosa).

    Conexões: usa ``_load_failure``, ``_build_user_prompt``, ``_make_llm_call``,
    ``plan_refinement``, ``apply_refinement_proposal``, ``rollback``; consumido
    pela CLI ``main`` e por coordenadores do squad.

    Dependências & Imports: ``HarnessState`` e a tríade plan/apply/rollback de
    ``scripts.auto_correction``; router injetado (duck-typed ``.complete``).
    """
    if kind not in VALID_KINDS:
        raise ValueError(
            f"kind inválido: {kind!r}; esperado um de: {', '.join(VALID_KINDS)}"
        )
    payload = _load_failure(kind, source_path)
    messages = [
        {"role": "system", "content": TRIGGER_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(kind, payload, runtime_root)},
    ]
    llm_call = _make_llm_call(router)

    state = HarnessState()
    history: List[RefinementHistoryEntry] = []
    plan = plan_refinement(
        messages=messages,
        state=state,
        history=history,
        llm_call=llm_call,
        scope="local",
    )

    if not apply:
        return {
            "status": "proposed",
            "summary": plan.proposal.summary,
            "edits_count": len(plan.proposal.edits),
            "proposal": plan.proposal,
        }

    result: Optional[RefinementResult] = None
    try:
        result = apply_refinement_proposal(plan, state, history)
        violation = _hard_limit_violation(result.applied_edits, Path(runtime_root))
        if violation is not None:
            raise RuntimeError(violation)
    except Exception as exc:
        target = result if result is not None else _partial_result_from_plan(plan)
        rollback_info: Dict[str, Any] = {"performed": True}
        try:
            rolled = rollback(target, state, history, baseline_state=plan.baseline_state)
            rollback_info["rollback_id"] = getattr(rolled, "id", None)
        except Exception as rb_exc:
            rollback_info["performed"] = False
            rollback_info["rollback_error"] = str(rb_exc)
        return {
            "status": "rolled-back",
            "error": str(exc),
            "id": plan.id,
            "summary": plan.proposal.summary,
            "rollback": rollback_info,
        }

    applied_count = 0
    for record in result.applied_edits:
        if record.applied:
            applied_count += 1
    return {
        "status": "applied",
        "applied_edits_count": applied_count,
        "id": result.id,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None, router: Any = None) -> int:
    """CLI: transforma falha em proposta/aplicação com códigos de saída 0/1/2.

    O que é: interface de linha de comando do gatilho
    (``--kind --source --root [--apply] [--timeout]``).

    Responsabilidade: montar o ``LLMRouter(DEFAULT_PROVIDERS())`` por padrão
    (``--timeout`` ajusta o timeout de cada provedor padrão), chamar
    ``correct_from_failure`` e imprimir exatamente um marcador:
    ``CORRECTION_PROPOSED`` | ``CORRECTION_APPLIED edits=<n>`` |
    ``CORRECTION_ROLLED_BACK`` | ``CORRECTION_ERROR: <msg>``.

    Pra que serve: operação manual/agendada pelo coordenador sem escrever código.

    Comportamento em falha: argumentos inválidos -> argparse sai 2; rolled-back ou
    exceção qualquer -> 1 (com ``CORRECTION_ERROR:`` no caso de exceção);
    proposed/applied -> 0. Router injetado ignora ``--timeout`` (útil em testes).

    Conexões: chama ``correct_from_failure``; constrói router via
    ``DEFAULT_PROVIDERS``/``LLMRouter`` de ``scripts.llm_providers`` (ambos
    substituíveis por monkeypatch).

    Dependências & Imports: ``argparse``, ``sys`` (guarda ``__main__``).
    """
    parser = argparse.ArgumentParser(
        prog="auto_correction_trigger",
        description=(
            "Transforma falhas do squad (gate rejeitado, bloqueio, incidente) em "
            "proposta de correção Prime-style."
        ),
    )
    parser.add_argument("--kind", required=True, choices=list(VALID_KINDS))
    parser.add_argument("--source", required=True, help="arquivo-fonte da falha")
    parser.add_argument("--root", required=True, help="runtime root do squad")
    parser.add_argument("--apply", action="store_true", help="aplica a proposta (com rollback)")
    parser.add_argument("--timeout", type=float, default=None, help="timeout por provedor")
    args = parser.parse_args(argv)

    if router is None:
        providers = DEFAULT_PROVIDERS()
        if args.timeout is not None:
            for provider in providers:
                provider.config.timeout = args.timeout
        router = LLMRouter(providers)

    try:
        outcome = correct_from_failure(
            args.kind, args.source, args.root, router, apply=args.apply
        )
    except Exception as exc:
        print(f"CORRECTION_ERROR: {exc}")
        return 1

    status = outcome["status"]
    if status == "proposed":
        print("CORRECTION_PROPOSED")
        return 0
    if status == "applied":
        print(f"CORRECTION_APPLIED edits={outcome['applied_edits_count']}")
        return 0
    print("CORRECTION_ROLLED_BACK")
    return 1


if __name__ == "__main__":
    sys.exit(main())
