"""Specialist Instruction Compiler for Milestone R9.

Assembles the authoritative instruction payload with full hierarchical context,
resolved skills, DevOps boundaries, and fail-closed integrity guarantees.
Strictly stdlib + yaml only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml

from scripts.domain.delegation import AncestorSnapshot, WorkContext
from .errors import CompilationError
from .skills import ResolvedSkills


try:
    from scripts.render_agent_prompt import (
        _build_cognitive_contract_section as _canonical_cognitive_contract,
        _build_hive_mind_section as _canonical_hive_mind,
    )
except ImportError:
    _canonical_cognitive_contract = None
    _canonical_hive_mind = None


class SpecialistInstructionCompiler:
    """Authoritative compiler producing deterministic specialist execution instructions."""

    def __init__(self, runtime_root: Union[str, Path]):
        self.runtime_root = Path(runtime_root).resolve()

    def _find_agent_prompt_file(self, agent_id: str) -> Path:
        direct = self.runtime_root / "agents" / agent_id / "PROMPT.md"
        if direct.is_file():
            return direct

        agents_dir = self.runtime_root / "agents"
        if agents_dir.is_dir():
            for child in agents_dir.iterdir():
                if child.is_dir():
                    c_name = child.name
                    if c_name == agent_id or (c_name.split("-", 1)[-1] == agent_id):
                        p_file = child / "PROMPT.md"
                        if p_file.is_file():
                            return p_file
        raise CompilationError(f"System prompt file not found for agent '{agent_id}' under {agents_dir}")

    def _build_environment_section(self, work_context: WorkContext) -> str:
        current_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        work_path = f"work/{work_context.project_id}/{work_context.work_item_id}"
        return (
            f"<environment_details>\n"
            f"Current time: {current_time}\n"
            f"Working directory: %SQUAD_RUNTIME%/{work_path}\n"
            f"Workspace root folder: %SQUAD_RUNTIME%/work/{work_context.project_id}\n"
            f"</environment_details>\n"
        )

    def _build_cognitive_contract_section(self) -> str:
        if _canonical_cognitive_contract:
            return _canonical_cognitive_contract()
        return (
            "\n---\n"
            "# CONTRATO COGNITIVO, ANTI-ALUCINAÇÃO & QUALIDADE DE EXECUÇÃO\n\n"
            "Todo agente do squad opera sob regras cognitivas e de engenharia estritas:\n\n"
            "### 1. Ordem Mandatória de Carga do Subagente (5 Passos)\n"
            "1. **Persona**: Incorporar `agents/<id>/PROMPT.md` (identidade, axiomas, frameworks e limites).\n"
            "2. **Manifesto**: Respeitar `agents/<id>/skills/manifest.yaml` (delimitação formal de competências).\n"
            "3. **Skills**: Consultar skills de controle `%SQUAD_RUNTIME%/skills/agent-squad-mcp/SKILL.md` "
            "e `%SQUAD_RUNTIME%/skills/azure-devops-mcp/SKILL.md`, e as skills atribuídas.\n"
            "4. **Pesquisa Técnica Externa**: Pesquisar documentação oficial antes de propor código ou arquitetura.\n"
            "5. **DevOps**: Operar Boards e PRs via ferramentas MCP `@azure-devops/mcp` sob segregação de funções.\n\n"
            "### 2. Requisitos de Qualidade Orientados a Resultado\n"
            "- **Inspeção de Evidências Concretas**: Analisar código real, logs e artefatos de entrada antes de propor alterações.\n"
            "- **Avaliação de Alternativas Técnicas**: Avaliar e justificar formalmente ao menos 2 caminhos viáveis.\n"
            "- **Verificação Pré-Conclusão**: Executar testes, linters, tipos e critérios de aceitação antes da conclusão.\n"
            "- **Evidência Comprovável**: Apresentar comandos exatos executados, saídas completas e diffs verificáveis.\n\n"
            "### 3. Anti-Alucinação Estrito\n"
            "- Proibição absoluta de inventar bibliotecas, APIs, parâmetros, caminhos de arquivo ou comandos inexistentes.\n"
            "- Na ausência de dados, emita: `UNVERIFIED`, `NOT FOUND` ou `EMPTY`.\n"
        )

    def _build_skills_section(self, resolved_skills: ResolvedSkills) -> str:
        sections = ["# HABILIDADES E CONHECIMENTOS CARREGADOS (SKILLS)\n"]
        for relative_skill_file in resolved_skills.load_order:
            skill_path = self.runtime_root / relative_skill_file
            if not skill_path.is_file():
                raise CompilationError(f"Skill file missing during compilation: {relative_skill_file}")

            try:
                content = skill_path.read_text(encoding="utf-8")
                sections.append(f"## SKILL: {relative_skill_file}\n\n{content}\n")

                openai_meta = skill_path.parent / "agents" / "openai.yaml"
                if openai_meta.is_file():
                    meta_text = openai_meta.read_text(encoding="utf-8")
                    sections.append(f"\n### METADADOS RUNTIME (openai.yaml)\n```yaml\n{meta_text}\n```\n")

                sections.append("\n---\n")
            except Exception as err:
                raise CompilationError(f"Error reading skill '{relative_skill_file}': {err}") from err

        return "\n".join(sections)

    def _build_work_context_section(self, context: WorkContext) -> str:
        lines = [
            f"# CONTEXTO DO WORK ITEM ({context.work_item_id})",
            "",
            f"**Título:** {context.title}",
            f"**Estágio Atual:** {context.current_stage}",
            f"**Projeto:** {context.project_id}",
            "",
            "## DESCRIÇÃO",
            context.description or "Sem descrição adicional informada.",
            "",
        ]

        if context.definition_of_done:
            lines.append("## DEFINITION OF DONE")
            for item in context.definition_of_done:
                lines.append(f"- [ ] {item}")
            lines.append("")

        if context.acceptance_criteria:
            lines.append("## CRITÉRIOS DE ACEITAÇÃO (GHERKIN)")
            for ac in context.acceptance_criteria:
                status_mark = "[x]" if ac.is_verified else "[ ]"
                lines.append(f"### {status_mark} {ac.id}: {ac.scenario}")
                lines.append(f"- **Dado**: {ac.given}")
                lines.append(f"- **Quando**: {ac.when}")
                lines.append(f"- **Então**: {ac.then}")
            lines.append("")

        # Ancestors section
        lines.append("## LINHAGEM E ESPECIFICAÇÕES ANCESTRAIS")
        if not context.ancestors:
            lines.append("- Item raiz de escopo (sem ancestrais adicionais).")
        else:
            icons = {
                "EPIC": "🔶",
                "FEATURE": "🟣",
                "STORY": "🔷",
                "TASK": "🟡",
            }
            for anc in context.ancestors:
                kind_str = anc.kind.value if hasattr(anc.kind, "value") else str(anc.kind).upper()
                icon = icons.get(kind_str, "🔹")
                lines.append(f"- {icon} **{kind_str}**: {anc.work_item_id} — {anc.title} (Estágio: {anc.stage})")
                if anc.spec_summary:
                    lines.append(f"  * Especificação resumida: {anc.spec_summary}")

        if context.ancestor_artifacts:
            lines.append("")
            lines.append("## ARTEFATOS ANCESTRAIS DISPONÍVEIS")
            for art_name, art_content in context.ancestor_artifacts.items():
                lines.append(f"### Artefato: {art_name}")
                lines.append("```markdown")
                # Truncate very large artifacts for context budget safety
                lines.append(art_content[:2500])
                if len(art_content) > 2500:
                    lines.append("\n... [conteúdo truncado para compilação] ...")
                lines.append("```")

        return "\n".join(lines) + "\n\n---\n"

    def _build_azure_devops_section(self, agent_id: str) -> str:
        cfg_path = self.runtime_root / "config" / "devops.yaml"
        if not cfg_path.is_file():
            return ""

        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return ""

        org = cfg.get("org", "cbvgas")
        project = cfg.get("project", "Arthemis")
        team = cfg.get("team", "agent-squad")
        area_path = cfg.get("area_path", f"{project}\\{team}")

        identities = cfg.get("identities", {})
        service_accounts = cfg.get("service_accounts", {})
        dev_email = identities.get("development_team", {}).get("email", "squads@michellaurindooutlook812.onmicrosoft.com")
        review_email = identities.get("pr_and_card_approver", {}).get("email", "arthemis@michellaurindooutlook812.onmicrosoft.com")
        cyber_email = service_accounts.get("cyber_red", {}).get("email", "cyber-red@michellaurindooutlook812.onmicrosoft.com")

        review_personas = identities.get("pr_and_card_approver", {}).get("used_by", [])
        cyber_personas = service_accounts.get("cyber_red", {}).get("used_by", ["offensive-cyber-operator"])

        if any(p in agent_id for p in cyber_personas):
            assigned_account = cyber_email
            role_desc = "SEGURANÇA OFENSIVA / RED TEAM"
        elif any(p in agent_id for p in review_personas):
            assigned_account = review_email
            role_desc = "APROVAÇÃO / REVISÃO (SoD)"
        else:
            assigned_account = dev_email
            role_desc = "EXECUÇÃO / DESENVOLVIMENTO"

        lines = [
            "## Azure DevOps — Contexto Operacional e Modelo de Contas (SoD)",
            "",
            "| Parâmetro | Valor |",
            "|---|---|",
            f"| Organização | `{org}` |",
            f"| Projeto Container | `{project}` |",
            f"| Team | `{team}` |",
            f"| Area Path | `{area_path}` |",
            f"| **Conta ADO Atribuída** | `{assigned_account}` ({role_desc}) |",
            "",
            "### Regra ADO-First (Inegociável)",
            "- Todo planejamento e backlog reside em Azure Boards (Epic → Feature → Story → Task).",
            "- Proibido criar artefatos de backlog locais substitutos.",
            "",
            "### MCP Tools Disponíveis (`@azure-devops/mcp`)",
            "```",
            "wit_work_item_write  → criar/atualizar card",
            "wit_work_item        → ler card por ID",
            "wit_query            → buscar cards com WIQL",
            "repo_pull_request_write → criar PR com reviewers obrigatórios",
            "```",
            "",
            "---",
        ]
        return "\n".join(lines)

    def _build_hive_mind_section(self) -> str:
        if _canonical_hive_mind:
            return _canonical_hive_mind()
        hive_path = os.environ.get("HIVE_MIND_PATH", "D:/Hive-Mind")
        return (
            "\n# ARQUITETURA CANÔNICA DE MEMÓRIA EM 3 PILARES\n\n"
            "### Pilar 1 — Memória Primária do Projeto (Local / Canônica)\n"
            "- SQLite WAL autoritativo (`banco/squad.db`).\n"
            "- AST / Grafo de dependências (`integrations/codebase_knowledge_graph.py`).\n\n"
            "### Pilar 2 — Colaboração e Rastreabilidade (Azure DevOps)\n"
            "- Work item discussions, PR review signs e wiki.\n\n"
            "### Pilar 3 — Segundo Cérebro Global (Hive-Mind)\n"
            f"- Vault corporativo em `{hive_path}`.\n"
            "- Operações via MCP: `sinapse_query`, `sinapse_save_decision`.\n"
        )

    def compile_instruction(
        self,
        agent_id: str,
        work_context: WorkContext,
        resolved_skills: ResolvedSkills,
    ) -> Tuple[str, str]:
        """Compiles the complete instruction payload and derives its canonical SHA-256 hash.

        Returns:
            Tuple[compiled_instruction, instruction_hash]

        Raises:
            CompilationError: On any failure (missing files, invalid formatting).
        """
        try:
            prompt_file = self._find_agent_prompt_file(agent_id)
            prompt_content = prompt_file.read_text(encoding="utf-8")
        except Exception as err:
            raise CompilationError(f"Failed to load prompt for agent '{agent_id}': {err}") from err

        sections: List[str] = [
            self._build_environment_section(work_context),
            f"# AGENT SYSTEM PROMPT: {agent_id}\n\n{prompt_content}\n",
            self._build_cognitive_contract_section(),
            self._build_skills_section(resolved_skills),
            self._build_work_context_section(work_context),
            self._build_azure_devops_section(agent_id),
            self._build_hive_mind_section(),
        ]

        compiled = "\n".join(sections).strip() + "\n"
        instr_hash = hashlib.sha256(compiled.encode("utf-8")).hexdigest()
        return compiled, instr_hash
