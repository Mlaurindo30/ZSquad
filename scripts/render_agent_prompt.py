#!/usr/bin/env python3
"""
O que é: Script compilador e adaptador de runtime de system prompts para agentes do squad.
Responsabilidade: Concatenar PROMPT.md, skills nativas, atribuídas e descobertas na ordem canônica e injetar contexto de work item.
Pra que serve: Gerar o payload textual completo para inicialização de agentes em runtimes externos (ex: Claude, Codex, Gemini).
Comportamento em falha: Lança SquadError caso algum arquivo de prompt ou skill obrigatória esteja ausente.
Conexões: Utiliza agent_squad.py e alimenta runtimes de execução.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agent_squad import AgentSquad, SquadError, read_yaml


def _extract_work_item_text(work_item_path: Path) -> str:
    """Extrai texto descritivo de um work item para usar como query de seleção de skills."""
    parts: list[str] = []

    status_file = work_item_path / "status.yaml"
    if status_file.exists():
        try:
            data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                title = data.get("title", "")
                description = data.get("description", "")
                if title:
                    parts.append(str(title))
                if description:
                    parts.append(str(description))
        except (OSError, yaml.YAMLError):
            pass

    epic_file = work_item_path / "epic.md"
    if epic_file.exists():
        try:
            text = epic_file.read_text(encoding="utf-8")
            parts.append(text[:2000])
        except (OSError, yaml.YAMLError):
            pass

    return " ".join(parts)


def _build_environment_section(packet: dict[str, Any], squad: AgentSquad) -> str:
    """Gera a seção com timestamps ISO-8601 e caminhos de execução do agente."""
    work_item_path = squad._work_base() / packet["work_item"]
    current_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (
        f"<environment_details>\n"
        f"Current time: {current_time}\n"
        f"Working directory: {work_item_path}\n"
        f"Workspace root folder: {squad._work_base()}\n"
        f"</environment_details>\n"
    )


def _build_prompt_section(agent: str, packet: dict[str, Any], squad: AgentSquad) -> str:
    """Carrega e formata o PROMPT.md principal do agente."""
    prompt_file = squad.root / packet["prompt"]
    if not prompt_file.exists():
        raise SquadError(f"Arquivo de prompt ausente: {packet['prompt']}")
    return f"# AGENT SYSTEM PROMPT: {agent}\n\n{prompt_file.read_text(encoding='utf-8')}\n\n---\n"


def _build_skills_section(packet: dict[str, Any], squad: AgentSquad) -> str:
    """Monta a seção de skills (nativas, assigned e discovered) carregadas em ordem canônica."""
    sections = ["# HABILIDADES E CONHECIMENTOS CARREGADOS (SKILLS)\n"]
    for relative_skill_file in packet["load_order"]:
        skill_path = squad.root / relative_skill_file
        skill_dir = skill_path.parent
        skill_content = skill_path.read_text(encoding="utf-8")

        sections.append(f"## SKILL: {relative_skill_file}\n\n{skill_content}\n")

        openai_meta = skill_dir / "agents" / "openai.yaml"
        if openai_meta.exists():
            meta_text = openai_meta.read_text(encoding="utf-8")
            sections.append(f"\n### METADADOS RUNTIME (openai.yaml)\n```yaml\n{meta_text}\n```\n")

        sections.append("\n---\n")
    return "\n".join(sections)


def _build_work_item_context(packet: dict[str, Any], squad: AgentSquad) -> str | None:
    """Constrói o bloco de contexto contendo o status.yaml do work item."""
    if "work_item" not in packet:
        return None
    item_dir = squad._work_base() / packet["work_item"]
    status_file = item_dir / "status.yaml"
    if not status_file.exists():
        return None
    return (
        f"# CONTEXTO DO WORK ITEM ({packet['work_item']})\n\n"
        f"## STATUS DO WORK ITEM\n```yaml\n"
        f"{status_file.read_text(encoding='utf-8')}\n"
        f"```\n"
    )


def _build_hive_mind_section() -> str:
    """Gera as diretrizes de governança de memória compartilhada do Hive-Mind."""
    return (
        "\n---\n"
        "# SEGUNDO CÉREBRO — HIVE-MIND (D:\\Hive-Mind)\n\n"
        "O agent_squad usa `D:\\Hive-Mind` como memória persistente compartilhada. "
        "Ele NÃO é um banco paralelo: é a camada de memória universal onde todos os agentes "
        "consolidam estado, decisões, aprendizados e trajetória entre sessões.\n\n"
        "**Acesso canônico:**\n"
        "- Vault humano/agente-legível: `D:\\Hive-Mind\\cerebro`\n"
        "- claude-mem (memória temporal/observações): `D:\\Hive-Mind\\claude-mem`\n"
        "- Servidor MCP sinapse: `D:\\Hive-Mind\\scripts\\services\\sinapse-mcp.py`\n"
        "- Tools expostas via MCP: 16 tools (sinapse_query, sinapse_save_decision, "
        "sinapse_save_learning, sinapse_health, sinapse_session_end, "
        "sinapse_temporal_search, sinapse_temporal_timeline, "
        "sinapse_temporal_get_observations, sinapse_temporal_save, "
        "sinapse_zettelkasten_split, sinapse_capture_screen, "
        "sinapse_plan_goal, sinapse_promote_knowledge, "
        "sinapse_temporal_graph_search, sinapse_rag_query, search_memories)\n\n"
        "**Regra obrigatória:**\n"
         "1. **Antes de agir**: chamar `sinapse_health()` + `sinapse_query('<tópico>')` "
        "para recuperar estado/histórico/decisões anteriores. Nunca afirmar estado do projeto "
        "sem consultar primeiro.\n"
        "2. **Durante o trabalho**: registrar decisões com `sinapse_save_decision` e aprendizados "
        "com `sinapse_save_learning`. Capturar apenas eventos realmente relevantes com "
        "`sinapse_temporal_save` (não em loop).\n"
        "3. **Ao final da sessão/work-item**: chamar `sinapse_session_end(summary)` para atualizar "
        "`Current State.md` e fechar a observação na UMC.\n"
        "4. **Nunca chamar backends raw** (`nmem`, `claude-mem`, `graphify`, `falkordb`): "
        "sempre via `sinapse_query` (Context Fusion com circuit breaker e timeout 8s).\n\n"
        "**Nota:** O MCP config (`config/mcp_config.json`) expõe todas as 16 tools do "
        "sinapse-hivemind.\n"
    )


def _build_engines_section(squad: AgentSquad) -> str | None:
    """Gera o catálogo de motores de integração do diretório integrations/."""
    engine_rows = []
    for entry in squad.skills_catalog.get("catalog", []):
        if entry.get("domain") == "integration-engines":
            path = entry.get("path", "")
            name = entry.get("name", Path(path).name)
            desc = entry.get("description", "")
            engine_rows.append((name, desc))

    if not engine_rows:
        return None

    lines = [
        "# MOTORES DE INTEGRAÇÃO (`integrations/`)\n",
        "Os motores abaixo são ferramentas de código que você **deve executar** durante "
        "tarefas de engenharia. Eles não são chamados pelo orchestrator automaticamente: "
        "cada agente responsável deve invocá-los no momento apropriado do fluxo.\n",
        "| Engine | Quando usar |\n",
        "|--------|-------------|\n",
    ]
    for name, desc in engine_rows:
        first_line = desc.splitlines()[0] if desc else name
        lines.append(f"| `{name}` | {first_line} |\n")
    lines.append(
        "\n**Regra obrigatória:**\n"
        "- **`blast_radius_analyzer` é OBRIGATÓRIO** antes de qualquer alteração em produção, "
        "schema, credencial ou dado sensível. Sem essa chamada, a mudança não pode prosseguir.\n"
        "- Os resultados dos engines devem ser registrados como evidência nos achados/handoffs "
        "correspondentes e, quando relevante, salvos no Hive-Mind via `sinapse_save_decision`.\n"
    )
    return "\n---\n" + "".join(lines)


def _resolve_project_name(work_item: str | None, project_name: str | None) -> str | None:
    """Resolve o projeto sem confundir ``work/<WORK-ID>`` com modo multi-projeto."""
    inferred = None
    if work_item:
        parts = Path(work_item).parts
        if len(parts) >= 3 and parts[0] == "work":
            inferred = parts[1]
    if project_name and inferred and project_name != inferred:
        raise SquadError(
            f"project_name conflitante: argumento={project_name}, work_item={inferred}"
        )
    return project_name or inferred


def render_agent_prompt(
    agent: str,
    work_item: str | None = None,
    assigned: list[str] | None = None,
    discovered: list[str] | None = None,
    output_path: str | None = None,
    auto_select_skills: bool = False,
    max_discovered: int = 3,
    project_name: str | None = None,
) -> str:
    """Compila o prompt de sistema completo de um agente com suas skills e contexto de work item."""
    root_dir = Path(__file__).resolve().parent.parent

    effective_project_name = _resolve_project_name(work_item, project_name)
    squad = AgentSquad(root=root_dir, project_name=effective_project_name)

    if discovered is None:
        discovered = []

    if auto_select_skills and work_item and not discovered:
        try:
            from scripts.skill_selector import suggest_and_load
            item_path = Path(work_item)
            if not item_path.is_absolute():
                item_path = root_dir / item_path
            task_text = _extract_work_item_text(item_path)
            if task_text.strip():
                suggestions = suggest_and_load(
                    task=task_text,
                    skills_dir=root_dir / "skills",
                    max_results=max_discovered,
                    cache=True,
                )
                discovered = [s.ranked.skill.path for s in suggestions if s.loaded]
        except (ImportError, OSError, ValueError, SquadError):
            discovered = discovered or []

    try:
        packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
    except SquadError:
        if auto_select_skills and discovered:
            try:
                manifest = read_yaml(root_dir / squad.agents[agent]["manifest"])
                native_count = len(manifest.get("native", []))
                assigned_count = len(assigned or [])
                budget = max(0, 7 - native_count - assigned_count)
                reduced = discovered[:budget]
                if reduced != discovered:
                    packet = squad.activation_packet(agent, assigned=assigned, discovered=reduced, item=work_item)
                else:
                    discovered = []
                    packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
            except (KeyError, OSError, SquadError):
                discovered = []
                packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
        else:
            raise

    sections: list[str] = []

    if "work_item" in packet:
        sections.append(_build_environment_section(packet, squad))

    sections.append(_build_prompt_section(agent, packet, squad))
    sections.append(_build_skills_section(packet, squad))

    work_item_ctx = _build_work_item_context(packet, squad)
    if work_item_ctx:
        sections.append(work_item_ctx)

    sections.append(_build_hive_mind_section())

    engines_section = _build_engines_section(squad)
    if engines_section:
        sections.append(engines_section)

    rendered = "\n".join(sections)

    if output_path:
        out_file = Path(output_path).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(rendered, encoding="utf-8")

    return rendered


def main():
    """Ponto de entrada CLI para renderização de prompts de agentes."""
    parser = argparse.ArgumentParser(description="Renderiza o system prompt completo de um agente.")
    parser.add_argument("--agent", required=True, help="ID do agente (ex: product-owner)")
    parser.add_argument("--work-item", help="Caminho do work item (opcional, ex: work/EPIC-000)")
    parser.add_argument("--project-name", help="Nome do projeto. Quando omitido, é inferido de --work-item (work/<project_name>/...) ou usa modo legado.")
    parser.add_argument("--assigned", nargs="*", help="Skills assigned opcionais a carregar")
    parser.add_argument("--discovered", nargs="*", help="Skills discovered opcionais a carregar")
    parser.add_argument("--output", help="Caminho do arquivo de saída (opcional)")
    parser.add_argument("--auto-select-skills", action="store_true", help="Seleciona skills relevantes automaticamente via selector")
    parser.add_argument("--max-discovered", type=int, default=3, help="Máximo de skills descobertas auto-selecionadas")

    args = parser.parse_args()

    try:
        rendered = render_agent_prompt(
            agent=args.agent,
            work_item=args.work_item,
            assigned=args.assigned,
            discovered=args.discovered,
            output_path=args.output,
            auto_select_skills=args.auto_select_skills,
            max_discovered=args.max_discovered,
            project_name=args.project_name,
        )
        if args.output:
            print(f"PROMPT_RENDERED_OK agent={args.agent} output={args.output} bytes={len(rendered.encode('utf-8'))}")
        else:
            print(rendered)
    except SquadError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
