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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agent_squad import AgentSquad, SquadError, read_yaml


_render_cache: dict[tuple[Any, ...], str] = {}


def _get_mtime(path: Path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _build_cache_key(
    agent: str,
    work_item: str | None,
    assigned: tuple[str, ...],
    discovered: tuple[str, ...],
    auto_select_skills: bool,
    max_discovered: int,
    project_name: str | None,
    root_dir: Path,
    squad: AgentSquad,
) -> tuple:
    key: list[Any] = [
        agent,
        work_item,
        assigned,
        discovered,
        auto_select_skills,
        max_discovered,
        project_name,
    ]
    try:
        prompt_file = root_dir / squad.agents[agent]["prompt"]
        key.append(_get_mtime(prompt_file))
    except (KeyError, OSError):
        key.append(0.0)
    for skill_path in assigned + discovered:
        key.append(_get_mtime(root_dir / skill_path))
    if work_item:
        item_path = Path(work_item)
        if not item_path.is_absolute():
            item_path = root_dir / work_item
        key.append(_get_mtime(item_path / "status.yaml"))
        key.append(_get_mtime(item_path / "epic.md"))
    else:
        key.extend([0.0, 0.0])
    return tuple(key)


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


def _build_azure_devops_section(packet: dict[str, Any], squad: Any) -> str:
    """Injeta contexto operacional do Azure DevOps no prompt do subagente.

    Carrega config ADO do projeto e monta seção instrucional completa com:
    - Org, Projeto, Team, Area Path, Iteration ativa
    - Conta ADO vinculada ao papel do agente (squads@ vs arthemis@)
    - Guia de uso das MCP tools @azure-devops/mcp
    - Regra ADO-first (proibição de artefatos locais de backlog)
    """
    # Tentar carregar devops.yaml do projeto
    try:
        from pathlib import Path
        import yaml as _yaml

        if hasattr(squad, "root"):
            root = Path(squad.root)
        elif isinstance(squad, dict):
            root = Path(squad.get("root_path", squad.get("root", ".")))
        else:
            root = Path(".")

        devops_cfg_path = root / "config" / "devops.yaml"
        if not devops_cfg_path.exists():
            devops_cfg_path = root / ".agents_squad" / "config" / "devops.yaml"
        if not devops_cfg_path.exists():
            devops_cfg_path = root / "templates" / "devops.yaml"

        if not devops_cfg_path.exists():
            return ""

        cfg = _yaml.safe_load(devops_cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""

    org = cfg.get("org", "cbvgas")
    project = cfg.get("project", "Arthemis")
    team = cfg.get("team", "agent-squad")
    if team == "<product-name>":
        team = "agent-squad"
    area_path = cfg.get("area_path", f"Arthemis\\{team}")
    if "<product-name>" in area_path:
        area_path = f"Arthemis\\{team}"

    # Determinar conta ADO baseada no papel do agente e modelo SoD (4 contas de automação + human_master)
    agent_id = packet.get("agent_id") or packet.get("agent", "")
    identities = cfg.get("identities", {})
    service_accounts = cfg.get("service_accounts", {})
    dev_team = identities.get("development_team", {})
    reviewer = identities.get("pr_and_card_approver", {})
    cyber_acc = service_accounts.get("cyber_red", {})
    pii_acc = service_accounts.get("customer_data_pii", {})

    dev_personas = dev_team.get("used_by", [])
    review_personas = reviewer.get("used_by", [])
    cyber_personas = cyber_acc.get("used_by", ["offensive-cyber-operator", "34-offensive-cyber-operator"])

    dev_email = dev_team.get("email", "squads@michellaurindooutlook812.onmicrosoft.com")
    review_email = reviewer.get("email", "arthemis@michellaurindooutlook812.onmicrosoft.com")
    cyber_email = cyber_acc.get("email", "cyber-red@michellaurindooutlook812.onmicrosoft.com")
    pii_email = pii_acc.get("email", "customer_data_pii@michellaurindooutlook812.onmicrosoft.com")

    numeric_id = agent_id.split("-")[0] if "-" in agent_id else ""
    clean_id = agent_id.split("-", 1)[1] if "-" in agent_id else agent_id

    if any(p == agent_id or p == clean_id or (numeric_id and numeric_id in p) for p in cyber_personas):
        assigned_account = cyber_email
        account_role = "SEGURANÇA OFENSIVA / RED TEAM"
    elif any(p == agent_id or p == clean_id or (numeric_id and numeric_id in p) for p in review_personas):
        assigned_account = review_email
        account_role = "APROVAÇÃO / REVISÃO (SoD)"
    else:
        assigned_account = dev_email
        account_role = "EXECUÇÃO / DESENVOLVIMENTO"

    lines = [
        "## Azure DevOps — Contexto Operacional e Modelo de Contas (SoD)",
        "",
        "| Parâmetro | Valor |",
        "|---|---|",
        f"| Organização | `{org}` |",
        f"| Projeto Container | `{project}` |",
        f"| Team | `{team}` |",
        f"| Area Path | `{area_path}` |",
        f"| **Conta ADO Atribuída** | `{assigned_account}` ({account_role}) |",
        "",
        "### As 4 Contas de Automação Azure DevOps (Segregação de Funções - SoD)",
        "",
        f"- `squads@michellaurindooutlook812.onmicrosoft.com` — **Execução Técnica**: 38 personas construtoras/analistas.",
        f"- `arthemis@michellaurindooutlook812.onmicrosoft.com` — **Revisão / Aprovação**: 5 personas revisoras (`code-reviewer`, `security-reviewer`, `qa-engineer`, `performance-engineer`, `governance-auditor`).",
        f"- `cyber-red@michellaurindooutlook812.onmicrosoft.com` — **Segurança Ofensiva / Red Team**: dedicada do `34-offensive-cyber-operator` (duplo sign-off em auth/crypto/iac).",
        f"- `customer_data_pii@michellaurindooutlook812.onmicrosoft.com` — **Dados Sensíveis / PII**: leitura de datasets/pipelines confidenciais (sem voto em PR).",
        "- `human_master` (`michel.laurindo@outlook.com`) — **Supervisão Humana**: Gates humanos G1/G6, CAB, deploy.",
        "",
        "### Regra ADO-First (Inegociável)",
        "",
        "Se este projeto tem Azure DevOps configurado:",
        "- **PROIBIDO** criar `product-goal.md`, `backlog.md`, `board.yaml`, `task_plan.md` locais",
        "- **TODO backlog e planejamento** = Work Items no Azure Boards (Epic→Feature→Story→Task)",
        "",
        "### MCP Tools Disponíveis (`@azure-devops/mcp`)",
        "",
        "```",
        "wit_work_item_write  → criar/atualizar card (Epic, Feature, User Story, Task)",
        "wit_work_item        → ler card por ID",
        "wit_query            → buscar cards com WIQL",
        "repo_pull_request_write → criar PR com reviewers obrigatórios",
        "```",
        "",
        "### Hierarquia obrigatória de Work Items",
        "",
        "```",
        "🔶 Epic → 🟣 Feature → 🔷 User Story (≤8 pts Fibonacci) → 🟡 Task",
        "```",
        "",
        "### 7 Colunas SDLC — quando mover o card",
        "",
        "| Fase | Coluna ADO | Estado | Conta |",
        "|---|---|---|---|",
        "| Blueprint | Blueprint | New | squads@ |",
        "| Scaffolding | Scaffolding | Active | squads@ |",
        "| Implementation | Implementation | Active | squads@ |",
        "| Code Security Review | Code Security Review | Active | arthemis@ |",
        "| Quality Validation | Quality Validation | Resolved | arthemis@ |",
        "| Governance Release | Governance Release | Resolved | arthemis@ |",
        "| Done | Done | Closed | arthemis@ |",
    ]

    return "\n".join(lines)


def _build_cognitive_contract_section() -> str:
    """Gera as diretrizes de Contrato Cognitivo, Anti-Alucinação e Protocolo de Execução do Agente."""
    return (
        "\n---\n"
        "# CONTRATO COGNITIVO, ANTI-ALUCINAÇÃO & ENGENHARIA DE PROMPT\n\n"
        "Todo agente do squad opera sob regras cognitivas estritas e inegociáveis:\n\n"
        "### 1. Ordem Mandatória de Carga do Subagente (5 Passos Inegociáveis)\n"
        "1. **Persona**: Ler e incorporar `agents/<id>/PROMPT.md` (identidade, axiomas, arquétipo, frameworks).\n"
        "2. **Manifesto**: Ler `agents/<id>/skills/manifest.yaml` (delimitação formal de competências).\n"
        "3. **Skills**: Ler os `SKILL.md` das skills atribuídas (`native` e `assigned`).\n"
        "4. **Pesquisa Técnica Externa Obrigatória**: Pesquisar documentação oficial e referências técnicas atualizadas na web sobre os temas/APIs/libs antes de implementar, evitando inventar padrões ou usar convenções obsoletas.\n"
        "5. **DevOps**: Identificar e usar prioritariamente MCP `@azure-devops/mcp` para operações de Boards/PRs.\n\n"
        "### 2. Frameworks de Raciocínio (CoT, ToT e Self-Reflection)\n"
        "- **Chain-of-Thought (CoT)**: Decomposição analítica passo a passo antes de propor arquiteturas, planos ou modificações de código.\n"
        "- **Tree-of-Thoughts (ToT)**: Para decisões arquiteturais, de design ou bugfixes não triviais, explorar e ponderar explicitamente pelo menos 2 caminhos alternativos antes de convergir na solução ótima.\n"
        "- **Self-Reflection (Autocrítica e Validação)**: Antes de considerar qualquer entrega concluída, rodar auto-verificação rigorosa contra testes, linters, types e critérios de aceitação, corrigindo desvios imediatamente.\n\n"
        "### 3. Anti-Alucinação Estrito\n"
        "- Proibição absoluta de inventar bibliotecas, APIs, parâmetros, arquivos inexistentes, comandos CLI ou IDs de agentes.\n"
        "- Na ausência de dados, dados ambíguos ou impossibilidade de verificação direta, emita explicitamente: `UNVERIFIED` (não verificado), `NOT FOUND` (não localizado) ou `EMPTY` (vazio). Nunca adivinhe ou fabrique fatos.\n"
    )


def _build_hive_mind_section() -> str:
    """Gera as diretrizes de cognição em duas camadas: Memória Primária do Projeto e Segundo Cérebro Global."""
    return (
        "\n---\n"
        "# ARQUITETURA DE MEMÓRIA EM DUAS CAMADAS (PROJETO + HIVE-MIND)\n\n"
        "O agente opera sob cognição estruturada em duas camadas complementares:\n\n"
        "### Camada 1 — Memória Primária do Projeto (Local / Workspace)\n"
        "- **Banco do Projeto (`banco/squad.db`)**: SQLite WAL local com tabelas de símbolos AST, traces, quóruns e métricas.\n"
        "- **Grafo de Conhecimento / Graphify (`integrations/codebase_knowledge_graph.py`)**: AST, dependências de código e cálculo de Blast Radius.\n"
        "- **Memória do Work Item (`work/<project_id>/memory/`)**: `shared/summary.md` (fatos consolidados), checkpoints privados por agente e deltas `MEM-*.yaml`.\n\n"
        "### Camada 2 — Segundo Cérebro Global (Hive-Mind: `D:\\Hive-Mind`)\n"
        "O Hive-Mind é a memória persistente universal cross-squad / cross-projeto. "
        "Não substitui o banco do projeto, mas armazena decisões arquiteturais duradouras, padrões e aprendizados acumulados.\n\n"
        "**Acesso canônico:**\n"
        "- Vault humano/agente-legível: `D:\\Hive-Mind\\cerebro`\n"
        "- claude-mem (memória temporal/observações): `D:\\Hive-Mind\\claude-mem`\n"
        "- Servidor MCP sinapse: `D:\\Hive-Mind\\scripts\\services\\sinapse-mcp.py`\n"
        "- Tools MCP: `sinapse_query`, `sinapse_save_decision`, `sinapse_save_learning`, `sinapse_health`, `sinapse_session_end`\n\n"
        "**Regra Obrigatória do Passo 0 (Memória)**:\n"
        "1. **Antes de iniciar a tarefa**: Consultar primeiro a Memória do Projeto (`summary.md`, checkpoints, grafo AST) e, em seguida, consultar o Hive-Mind via `sinapse_query('<tema>')` para recuperar decisões corporativas prévias.\n"
        "2. **Durante e ao concluir**: Gravar fatos e deltas no projeto (`memory/shared/summary.md`) e promover aprendizados e decisões arquiteturais duradouras ao Hive-Mind com `sinapse_save_decision`.\n"
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
    if agent not in squad.agents:
        for aid, entry in squad.agents.items():
            entry_path = entry.get("path", "")
            if aid == agent or entry_path.endswith(f"/{agent}") or entry_path.endswith(f"\\{agent}"):
                agent = aid
                break
            if "-" in agent and agent.split("-", 1)[1] == aid:
                agent = aid
                break

    if agent not in squad.dispatchable_agent_ids and output_path is not None:
        raise SquadError(f"provider-primary host não pode ser despachado: {agent}")

    if discovered is None:
        discovered = []

    cache_key = _build_cache_key(
        agent=agent,
        work_item=work_item,
        assigned=tuple(assigned or []),
        discovered=tuple(discovered or []),
        auto_select_skills=auto_select_skills,
        max_discovered=max_discovered,
        project_name=project_name,
        root_dir=root_dir,
        squad=squad,
    )

    cached = _render_cache.get(cache_key)
    if cached is not None:
        if output_path:
            out_file = Path(output_path).resolve()
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(cached, encoding="utf-8")
        return cached

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
    sections.append(_build_cognitive_contract_section())
    sections.append(_build_skills_section(packet, squad))

    work_item_ctx = _build_work_item_context(packet, squad)
    if work_item_ctx:
        sections.append(work_item_ctx)

    ado_section = _build_azure_devops_section(packet, squad)
    if ado_section:
        sections.append(ado_section)

    sections.append(_build_hive_mind_section())

    engines_section = _build_engines_section(squad)
    if engines_section:
        sections.append(engines_section)

    rendered = "\n".join(sections)
    _render_cache[cache_key] = rendered

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
