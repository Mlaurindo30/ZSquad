"""Composição de briefings de comando a partir dos overlays governados (Tarefa T3).

``render_command(stage, context) -> str`` compõe o briefing completo do agente
para um dos sete comandos SDD governados (constitution, specify, clarify, plan,
tasks, implement, analyze), embutindo o overlay markdown do comando com os
valores de contexto do work item.

Contrato (plano documentation/plans/2026-09-11-spec-kit-integration.md §6):
- Estágio desconhecido levanta ``ValueError``.
- Contexto ausente/vazio para as chaves obrigatórias do estágio levanta
  ``ValueError`` listando o que falta (fail-closed).
- Placeholder ``{{chave}}`` não resolvido após a substituição levanta
  ``ValueError`` (não há saída parcial).
- Mesma entrada -> mesma saída (substituição e listagem em ordem ordenada).

Este módulo entrega APENAS composição de prompts. A autorização real de
subetapas/gates pertence a T4 (``policy.py``/``authorize``) e T5 (integração
no CLI Squad); nenhum output deste módulo concede autorização.

O pacote pai contém hífen no nome e não é importável diretamente; carregue
este módulo via ``importlib.util.spec_from_file_location`` conforme
``integrations/spec-kit/tests/test_rendering.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

OVERLAYS_DIR = Path(__file__).resolve().parent.parent / "overlays" / "commands"

_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")

# Chaves de contexto obrigatórias por comando (ordem ordenada).
# Fluxo do plano §5: Constitution -> Specify -> Clarify -> G1 -> Plan -> G2
# -> Tasks/analyze -> G3 -> Implement. 'plan' exige spec + clarifications +
# evidência de G1; 'implement' exige plan + tasks + evidências G1-G3.
# As chaves cobrem TODOS os placeholders usados pelo overlay correspondente
# (a revisão do code-reviewer exigiu incluir 'plan_path' em 'plan' e
# 'tasks_path' em 'tasks', que os overlays citam como pré-condições).
REQUIRED_CONTEXT: dict[str, tuple[str, ...]] = {
    "analyze": ("g2_evidence", "plan_path", "project_id", "tasks_path", "work_id"),
    "clarify": ("clarifications_path", "project_id", "spec_path", "work_id"),
    "constitution": ("constitution_path", "project_id", "work_id"),
    "implement": (
        "g1_evidence",
        "g2_evidence",
        "g3_evidence",
        "plan_path",
        "project_id",
        "tasks_path",
        "work_id",
    ),
    "plan": (
        "clarifications_path",
        "g1_evidence",
        "plan_path",
        "project_id",
        "spec_path",
        "work_id",
    ),
    "specify": ("project_id", "spec_path", "work_id"),
    "tasks": ("g2_evidence", "plan_path", "project_id", "tasks_path", "work_id"),
}

# Persona responsável por comando (plano §7-T3: "papel do agente").
# Mapeamento coerente com o roteamento do Squad (AGENTS.md §3):
# fase de produto (constitution/specify/clarify) -> requirements-analyst;
# planejamento (plan) -> solution-architect; coordenação do ciclo e dos
# gates de blueprint (tasks/analyze) -> delivery-orchestrator;
# execução (implement) -> software-engineer.
STAGE_PERSONA: dict[str, str] = {
    "analyze": "delivery-orchestrator",
    "clarify": "requirements-analyst",
    "constitution": "requirements-analyst",
    "implement": "software-engineer",
    "plan": "solution-architect",
    "specify": "requirements-analyst",
    "tasks": "delivery-orchestrator",
}


def _missing_keys(stage: str, context: dict) -> list[str]:
    return sorted(
        key
        for key in REQUIRED_CONTEXT[stage]
        if not isinstance(context.get(key), str) or not context.get(key).strip()
    )


def _load_overlay(stage: str) -> str:
    path = OVERLAYS_DIR / f"{stage}.md"
    if not path.is_file():
        raise ValueError(f"Overlay do comando '{stage}' não encontrado: {path}")
    return path.read_text(encoding="utf-8")


def _substitute_once(template: str, context: dict) -> tuple[str, set[str], list[str]]:
    """Substituição em PASSADA ÚNICA sobre o template.

    Um valor inserido nunca é re-escaneado: um valor contendo ``{{chave}}``
    permanece literal (a saída nunca interpreta o interior de um valor).
    Retorna (texto renderizado, chaves referenciadas pelo template,
    chaves de placeholder não resolvidas).
    """
    referenced: set[str] = set()
    unresolved: list[str] = []

    def _resolve(match: re.Match) -> str:
        key = match.group(1)
        referenced.add(key)
        if key in context:
            return str(context[key])
        unresolved.append(key)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_resolve, template), referenced, unresolved


def render_command(stage: str, context: dict) -> str:
    """Renderiza o briefing do agente para o comando governado ``stage``.

    ``context`` deve conter, no mínimo, as chaves obrigatórias do comando:
    ``work_id``, ``project_id``, caminhos dos artefatos SDD do work item
    (``spec_path``, ``clarifications_path``, ``plan_path``, ``tasks_path``,
    ``constitution_path``) e referências de evidência de gate
    (``g1_evidence``, ``g2_evidence``, ``g3_evidence``).
    """
    if stage not in REQUIRED_CONTEXT:
        raise ValueError(
            f"Comando desconhecido: '{stage}'. "
            f"Comandos válidos: {', '.join(sorted(REQUIRED_CONTEXT))}"
        )
    if not isinstance(context, dict):
        raise ValueError(
            "context deve ser um dict com work_id, project_id, caminhos dos "
            "artefatos SDD e referências de evidência de gate."
        )

    missing = _missing_keys(stage, context)
    if missing:
        raise ValueError(
            f"Contexto incompleto para o comando '{stage}'; "
            f"faltando: {', '.join(missing)}"
        )

    overlay = _load_overlay(stage)

    body, referenced, unresolved = _substitute_once(overlay, context)
    if unresolved:
        raise ValueError(
            f"Placeholders não resolvidos no comando '{stage}': "
            f"{', '.join(sorted(set(unresolved)))}"
        )

    header = [
        f"# Briefing de agente — comando governado: {stage}",
        f"Persona responsável: {STAGE_PERSONA[stage]}",
        f"Work item: {context['work_id']} (projeto: {context['project_id']})",
        "",
        "## Contexto resolvido (ordem determinística)",
    ]
    context_lines = [f"- {key}: {context[key]}" for key in sorted(context)]
    final = "\n".join(header + context_lines) + "\n\n---\n\n" + body.strip() + "\n"

    # Varredura da SAÍDA FINAL: nenhum placeholder pode sobrar, exceto quando
    # faz parte do valor literal de uma chave efetivamente referenciada pelo
    # template (valores são embutidos verbatim e nunca re-interpretados).
    allowed = [str(context[key]) for key in referenced]
    stray = sorted(
        {
            match.group(0)
            for match in _PLACEHOLDER_RE.finditer(final)
            if not any(match.group(0) in value for value in allowed)
        }
    )
    if stray:
        raise ValueError(
            f"Placeholder não resolvido na saída do comando '{stage}' "
            f"(valor de chave não referenciada contém placeholder): "
            f"{', '.join(stray)}"
        )
    return final


__all__ = ["OVERLAYS_DIR", "REQUIRED_CONTEXT", "STAGE_PERSONA", "render_command"]
