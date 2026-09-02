#!/usr/bin/env python3
"""Gatilho de aprendizado pós-gate/pós-handoff do squad (porta do Hermes /learn ao ciclo governado).

O que é: módulo que pergunta a um LLM se um artefato de governança recém-entregue
(gate decision ``GD-*`` ou handoff ``HANDOFF-*``) produziu um aprendizado
reutilizável e, quando sim, deposita um rascunho de skill em
``skills/discovery/intake/`` para curadoria do 18-skill-curator — nunca promove
nada ao catálogo ativo (``deposit_in_intake`` é o único efeito de escrita).

Responsabilidade: carregar o YAML do artefato, montar um resumo textual compacto
(gate: id/gate/decisão/decisor/critérios/evidências; handoff:
id/de/para/resumo/memory_delta), chamar ``provider.complete(..., expect_json=True)``
com contrato JSON estrito, tratar JSON inválido com UM retry mais estrito e
converter ``learning=true`` em um ``LearnedSkillDraft`` depositado via
``AutoSkillLearner(runtime_root).deposit_in_intake(draft)``.

Pra que serve: fechar o loop de autoaprendizagem do squad depois dos gates e
handoffs, no espírito do ``background_review`` do Hermes (replay do contexto da
sessão + "deve alguma skill ser salva?"), porém de forma determinística e
auditável: a entrada é o artefato YAML (não a conversa), a saída é JSON
estritamente contratado e o destino é a quarentena de intake — a promoção
continua exclusiva do 18-skill-curator via ``auto_skill_learner promote``.

Comportamento em falha: artefato ilegível ou não-mapeamento YAML ->
``{"status": "error", "error": "unreadable-artifact: ..."}``; provedor LLM que
lança exceção -> ``{"status": "error", "error": "provider-failure: ..."}``;
JSON inválido após 1 retry com lembrete estrito -> ``{"status": "error",
"error": "invalid-json"}``; ``kind`` fora de ``{"gate", "handoff"}`` levanta
``ValueError`` (erro de chamador). A CLI traduz cada status em
``REVIEW_ERROR: <msg>`` com saída 1 (2 para argumentos inválidos).

Conexões: scripts/auto_skill_learner.py (``AutoSkillLearner.deposit_in_intake`` e
dataclass ``LearnedSkillDraft``), scripts/llm_providers.py (fábrica ``Ollama`` e
``LLMProvider.complete``), schemas contracts/gate-decision.schema.json e
contracts/handoff.schema.json, e o fluxo de gates G1–G6/handoffs do AGENTS.md
(seções 6, 7 e 11 — intake é estágio de curadoria, nunca executado).

Dependências & Imports: apenas biblioteca padrão (``argparse``, ``json``, ``re``,
``sys``, ``pathlib``, ``typing``) + PyYAML; ``llm_providers`` e
``auto_skill_learner`` são importados do pacote ``scripts`` após normalização de
``sys.path`` (mesmo padrão dos módulos irmãos). Sem rede implícita: o provedor é
sempre injetado (os testes usam fakes duck-typed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import llm_providers
from scripts.auto_skill_learner import AutoSkillLearner, LearnedSkillDraft

#: Modelo Ollama padrão da revisão (volume, barato — keep_alive 0 na camada de provedor).
DEFAULT_MODEL = "granite4.1:3b"

#: Tipos de artefato de governança aceitos pelo gatilho.
VALID_KINDS = ("gate", "handoff")

#: Persona destinatária do rascunho em intake (dono da curadoria no squad).
TARGET_PERSONA = "18-skill-curator"

SYSTEM_PROMPT = (
    "Você é o gatilho de aprendizado do squad. Recebe o resumo de um artefato de "
    "governança (gate decision ou handoff) e decide se ele contém um aprendizado "
    "reutilizável que mereça virar uma skill.\n"
    "Responda EXATAMENTE com um objeto JSON válido, sem nenhum texto antes ou "
    "depois, no formato:\n"
    '{"learning": true|false, "skill_name": "<slug>"|null, '
    '"description": "<texto>"|null, "rationale": "<texto>", '
    '"markdown": "<SKILL.md>"|null}\n'
    "Regras:\n"
    "- learning=true somente se o artefato revelar técnica, correção, armadilha ou "
    "padrão reutilizável em uma classe futura de tarefa; detalhes de sessão, "
    "falhas ambientais transitórias e narrativas pontuais NÃO são aprendizado.\n"
    "- Quando learning=true: skill_name é um slug com hífens em nível de classe "
    "(nunca número de tarefa, string de erro ou codinome de sessão); description "
    "resume quando aplicar a skill; markdown é o corpo completo de um SKILL.md "
    "com frontmatter YAML contendo name e description.\n"
    "- Quando learning=false: skill_name, description e markdown são null e "
    "rationale explica a ausência de aprendizado.\n"
    "Nada além desse objeto JSON deve ser emitido."
)

STRICTER_REMINDER = (
    "\n\nLEMBRETE ESTRITO: sua resposta anterior não era um objeto JSON puro. "
    "Responda agora com EXATAMENTE um objeto JSON válido no formato "
    '{"learning": ..., "skill_name": ..., "description": ..., "rationale": ..., '
    '"markdown": ...} e nenhum outro texto.'
)


def _summarize_gate(artifact: dict[str, Any]) -> str:
    """Monta o resumo compacto de uma gate decision (id/gate/decisão/decisor/critérios/evidências)."""
    lines = [
        "tipo: gate-decision",
        f"id: {artifact.get('decision_id', '')}",
        f"gate: {artifact.get('gate_id', '')}",
        f"decisao: {artifact.get('decision', '')}",
        f"decisor: {artifact.get('decider', '')}",
        "criterios:",
    ]
    lines.extend(
        f"  - {criterion.get('name', '')}: {criterion.get('result', '')}"
        for criterion in artifact.get("criteria") or []
    )
    lines.append("evidencias:")
    lines.extend(
        f"  - {item}" for item in artifact.get("evidence") or []
    )
    return "\n".join(lines)


def _summarize_handoff(artifact: dict[str, Any]) -> str:
    """Monta o resumo compacto de um handoff (id/de/para/resumo/memory_delta)."""
    return "\n".join(
        [
            "tipo: handoff",
            f"id: {artifact.get('id', '')}",
            f"de: {artifact.get('from', '')}",
            f"para: {artifact.get('to', '')}",
            f"resumo: {artifact.get('summary', '')}",
            f"memory_delta: {artifact.get('memory_delta', '')}",
        ]
    )


def _artifact_summary(kind: str, artifact: dict[str, Any]) -> tuple[str, str]:
    """Devolve ``(resumo textual, id do artefato)`` conforme o tipo ``kind``."""
    if kind == "gate":
        return _summarize_gate(artifact), str(artifact.get("decision_id", ""))
    return _summarize_handoff(artifact), str(artifact.get("id", ""))


def _slugify_skill_name(raw_name: str, artifact_id: str) -> str:
    """Normaliza o nome vindo do LLM em slug; sem nome, deriva do id do artefato."""
    slug = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower()).strip("-")
    return slug or f"learned-from-{artifact_id.lower()}"


def _steps_from_markdown(markdown: str) -> list[str]:
    """Extrai os passos do rascunho a partir dos títulos H2 do markdown."""
    steps = [
        line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")
    ]
    return steps or ["Seguir o procedimento descrito no corpo da skill."]


def _build_draft(kind: str, artifact_id: str, payload: dict[str, Any]) -> LearnedSkillDraft:
    """Constrói o ``LearnedSkillDraft`` a partir do veredito do LLM, com fallbacks mínimos.

    O que é: tradução do JSON do LLM para o dataclass exigido por
    ``AutoSkillLearner.deposit_in_intake``.
    Responsabilidade: slugificar o nome, garantir descrição e markdown mínimos
    (frontmatter ``name``/``description``) e derivar steps dos H2 do corpo.
    Pra que serve: garantir que o rascunho depositado em intake seja parseável
    pelo linter Hermes mesmo quando o LLM omite campos opcionais.
    Comportamento em falha: campos ausentes viram fallbacks derivados do id do
    artefato — nunca de conteúdo inventado.
    Conexões: ``LearnedSkillDraft`` (scripts/auto_skill_learner.py).
    Dependências & Imports: ``re`` (via ``_slugify_skill_name``) e stdlib.
    """
    skill_name = _slugify_skill_name(str(payload.get("skill_name") or ""), artifact_id)
    description = (
        str(payload.get("description") or "").strip()
        or f"Aprendizado extraído do artefato {artifact_id}."
    )
    markdown = str(payload.get("markdown") or "")
    if not markdown:
        markdown = (
            f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
            f"# {skill_name}\n\n{description}\n"
        )
    return LearnedSkillDraft(
        skill_name=skill_name,
        description=description,
        target_persona=TARGET_PERSONA,
        trigger_conditions=[
            f"revisão pós-entrega de artefato {kind} ({artifact_id})"
        ],
        steps=_steps_from_markdown(markdown),
        source_work_item=artifact_id,
        raw_markdown=markdown,
    )


def review_artifact(
    kind: str,
    artifact_path: str | Path,
    runtime_root: str | Path,
    provider: Any,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Pergunta ao LLM se o artefato de governança gerou aprendizado e deposita o rascunho.

    O que é: API principal do gatilho de aprendizado pós-gate/pós-handoff.
    Responsabilidade: carregar o YAML do artefato, resumi-lo, consultar o
    provedor com ``expect_json=True`` sob contrato estrito, retentar UMA vez com
    lembrete mais estrito em caso de JSON inválido e, quando ``learning=true``,
    depositar o rascunho em ``skills/discovery/intake/`` via
    ``AutoSkillLearner(runtime_root).deposit_in_intake`` (nunca promover).
    Pra que serve: converter entregas de governança em candidatos a skill,
    deixando a promoção sob curadoria exclusiva do 18-skill-curator.
    Comportamento em falha: retorna ``{"status": "error", "error": ...}`` para
    artefato ilegível (``unreadable-artifact:``), provedor que lança exceção
    (``provider-failure:``) e JSON inválido após 1 retry (``invalid-json``);
    ``kind`` inválido levanta ``ValueError``. Com ``dry_run=True`` e
    ``learning=true`` devolve ``would-learn`` sem escrever nada.
    Conexões: chamado por ``main`` (CLI) e por qualquer persona pós-gate;
    consome ``_artifact_summary``, ``_build_draft`` e ``deposit_in_intake``.
    Dependências & Imports: ``yaml.safe_load``, ``json.loads``, stdlib.
    """
    if kind not in VALID_KINDS:
        raise ValueError(
            f"kind inválido: {kind!r} (esperado um de {VALID_KINDS})"
        )

    try:
        loaded = yaml.safe_load(Path(artifact_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {"status": "error", "error": f"unreadable-artifact: {exc}"}
    if not isinstance(loaded, dict):
        return {
            "status": "error",
            "error": "unreadable-artifact: conteúdo YAML não é um mapeamento",
        }

    summary, artifact_id = _artifact_summary(kind, loaded)
    user_prompt = (
        "Avalie o artefato de governança a seguir e decida se ele produziu um "
        "aprendizado reutilizável:\n\n" + summary
    )

    payload: Any = None
    prompt = user_prompt
    for _attempt in range(2):
        try:
            response = provider.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                expect_json=True,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error": f"provider-failure: {type(exc).__name__}: {exc}",
            }
        try:
            payload = json.loads(response["content"])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            break
        prompt = user_prompt + STRICTER_REMINDER
    if not isinstance(payload, dict):
        return {"status": "error", "error": "invalid-json"}

    if not payload.get("learning"):
        return {
            "status": "no-learning",
            "rationale": str(payload.get("rationale", "")),
        }

    skill_name = _slugify_skill_name(
        str(payload.get("skill_name") or ""), artifact_id
    )
    if dry_run:
        return {
            "status": "would-learn",
            "skill_name": skill_name,
            "rationale": str(payload.get("rationale", "")),
        }

    draft = _build_draft(kind, artifact_id, payload)
    intake_path = AutoSkillLearner(runtime_root).deposit_in_intake(draft)
    return {
        "status": "learned",
        "intake_path": intake_path,
        "skill_name": draft.skill_name,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI do gatilho: revisa um artefato e reporta o resultado.

    O que é: ponto de entrada ``python scripts/background_review.py``.
    Responsabilidade: interpretar ``--kind/--artifact/--root/--dry-run/--model``,
    montar o provedor via ``llm_providers.Ollama(model=args.model)`` e imprimir
    ``REVIEW_LEARNED <skill> -> <path>``, ``REVIEW_WOULD_LEARN <skill>``,
    ``REVIEW_NO_LEARNING`` ou ``REVIEW_ERROR: <msg>``.
    Pra que serve: permitir disparar a revisão pós-gate/pós-handoff em shell
    ou por outra persona, com código de saída machine-readable.
    Comportamento em falha: sai 1 para qualquer ``status=error``, 2 para
    argumentos inválidos (SystemExit do argparse convertido em retorno) e 0
    para learned/would-learn/no-learning.
    Conexões: delega a ``review_artifact``; provedor de ``scripts.llm_providers``.
    Dependências & Imports: ``argparse``, ``sys``; sem rede própria (provedor
    injetado internamente pela fábrica Ollama).
    """
    parser = argparse.ArgumentParser(
        description="Gatilho de aprendizado pós-gate/pós-handoff: pergunta ao LLM se o artefato gerou aprendizado e deposita rascunho no intake do 18-skill-curator (nunca promove)."
    )
    parser.add_argument("--kind", required=True, choices=list(VALID_KINDS),
                        help="Tipo do artefato: gate (GD-*) ou handoff (HANDOFF-*)")
    parser.add_argument("--artifact", required=True,
                        help="Caminho do arquivo YAML do artefato")
    parser.add_argument("--root", default=str(ROOT),
                        help="Raiz do runtime do squad (default: raiz deste repositório)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula o aprendizado sem depositar no intake")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Modelo Ollama da revisão (default: {DEFAULT_MODEL})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    provider = llm_providers.Ollama(model=args.model)
    result = review_artifact(
        args.kind, args.artifact, args.root, provider, dry_run=args.dry_run
    )
    status = result.get("status")

    if status == "learned":
        print(f"REVIEW_LEARNED {result['skill_name']} -> {result['intake_path']}")
        return 0
    if status == "would-learn":
        print(f"REVIEW_WOULD_LEARN {result.get('skill_name', '')}")
        return 0
    if status == "no-learning":
        print("REVIEW_NO_LEARNING")
        return 0
    print(f"REVIEW_ERROR: {result.get('error', 'unknown')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
