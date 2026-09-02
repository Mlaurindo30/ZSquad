#!/usr/bin/env python3
"""
O que é: Motor institucional de autoaprendizagem autônoma de skills e refinamento de prompts (inspirado em NousResearch Hermes Agent /learn, Prime Intellect Agent /refine e BoostPrompt).
Responsabilidade: Sintetizar novas habilidades padronizadas (agentskills.io), executar linter estrutural rígido, destilar heurísticas de autorreparo de trajetórias e avaliar a qualidade de prompts e briefings.
Pra que serve: Permitir que o squad aprenda continuamente com tarefas resolvidas, converta soluções em skills reutilizáveis e elimine regressões através de autorrefinamento.
Comportamento em falha: Valida rigorosamente metadados e sintaxe; descarta rascunhos inválidos, bloqueia promoções que violem guardrails e gera relatórios detalhados de auditoria.
Conexões: Utilizado por 18-skill-curator, 00-delivery-orchestrator, banco/squad.db e os adapters em integrations/.
Dependências & Imports:
  - json, re, pathlib, sys, time, yaml: Manipulação de arquivos, parsing textual e estruturado.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_discovery_ast import DiscoveredSkill, DiscoveredTool, discover_skills, discover_tools


# ── Hermes Skill Authoring & Linter Rules ─────────────────────────────────────

SHELL_UTIL_TO_TOOL: dict[str, str] = {
    "grep": "grep_search",
    "rg": "grep_search",
    "cat": "view_file",
    "head": "view_file",
    "tail": "view_file",
    "sed": "replace_file_content",
    "awk": "replace_file_content",
    "find": "list_dir",
    "ls": "list_dir",
}

PROHIBITED_MARKETING_WORDS: set[str] = {
    "revolutionary", "magical", "game-changing", "groundbreaking",
    "cutting-edge", "unbelievable", "world-class", "next-generation"
}


@dataclass
class LintFinding:
    """Representa um achado de conformidade do linter de skills no padrão Hermes."""
    severity: str  # 'BLOCKER', 'WARNING', 'ADVISORY'
    rule_id: str
    message: str
    line_number: int = 1


@dataclass
class LearnedSkillDraft:
    """Representa um rascunho de skill sintetizado pelo motor de autoaprendizagem."""
    skill_name: str
    description: str
    target_persona: str
    trigger_conditions: list[str]
    steps: list[str]
    source_work_item: str
    raw_markdown: str


@dataclass
class PromptQualityReport:
    """Relatório de qualidade de prompt e briefing no padrão BoostPrompt."""
    overall_score: float  # 0 a 100
    objective_clarity: float
    ground_truth_score: float
    anti_fabrication_score: float
    boundary_score: float
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers para catálogo de skills
# ---------------------------------------------------------------------------

def _compute_folder_sha256(folder: Path) -> str:
    """Calcula o folder_sha256 no mesmo formato do verify.ps1."""
    import hashlib
    files = [p for p in folder.rglob("*") if p.is_file()]
    lines = []
    for file in sorted(files, key=lambda p: p.relative_to(folder).as_posix()):
        relative = file.relative_to(folder).as_posix()
        lines.append(f"{relative}={hashlib.sha256(file.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _extract_description_from_skill_md(skill_md: Path) -> str:
    """Extrai a descrição do frontmatter YAML de um SKILL.md."""
    try:
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("\ufeff"):
            content = content[1:]
        if not content.startswith("---"):
            return ""
        end = content.find("\n---", 3)
        if end == -1:
            return ""
        frontmatter_text = content[3:end]
        data = yaml.safe_load(frontmatter_text) or {}
        if isinstance(data, dict):
            return data.get("description", "")
    except Exception:
        pass
    return ""


def _update_skills_catalog(root: Path, skill_name: str, target_domain: str, target_dir: Path) -> None:
    """Atualiza config/skills-catalog.yaml adicionando a skill promovida."""
    catalog_path = root / "config" / "skills-catalog.yaml"
    if not catalog_path.exists():
        return

    try:
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return

    if not isinstance(catalog, dict):
        return

    catalog.setdefault("catalog", [])

    relative_path = target_dir.relative_to(root).as_posix()
    folder_sha256 = _compute_folder_sha256(target_dir)
    description = _extract_description_from_skill_md(target_dir / "SKILL.md")

    entry = {
        "name": skill_name,
        "path": relative_path,
        "domain": target_domain,
        "specialization": skill_name,
        "source": "auto-synthesized",
        "provenance": "auto_skill_learner",
        "license_status": "internal-use",
        "assigned_to": [],
        "load": "on-demand",
        "folder_sha256": folder_sha256,
        "description": description or f"Auto-synthesized skill from work item: {skill_name}",
    }

    existing_paths = {e["path"] for e in catalog["catalog"] if isinstance(e, dict) and "path" in e}
    if relative_path not in existing_paths:
        catalog["catalog"].append(entry)

    catalog["active_skill_count"] = len(catalog["catalog"])
    catalog["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S.0000000%z")

    try:
        catalog_path.write_text(yaml.dump(catalog, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding="utf-8")
    except Exception:
        pass


class AutoSkillLearner:
    """Sintetizador, linter e refinador autônomo de habilidades e prompts do squad."""

    def __init__(self, squad_root: Path | str | None = None):
        """Inicializa o sintetizador com a raiz do squad.

        Args:
            squad_root: Caminho da raiz do squad.
        """
        self.root = Path(squad_root) if squad_root else ROOT

    # ── 1. Hermes /learn Synthesis ───────────────────────────────────────────

    def synthesize_skill_from_work_item(
        self,
        work_item_id: str,
        skill_name: str,
        target_persona: str = "software-engineer",
        summary_text: str = "",
        allowed_tools: list[str] | None = None,
    ) -> LearnedSkillDraft:
        """Sintetiza um rascunho de SKILL.md a partir dos artefatos de um work item entregue.

        Args:
            work_item_id: ID do work item (ex: TASK-001).
            skill_name: Nome canônico da nova skill (slug com hifens).
            target_persona: Persona prioritária de atribuição.
            summary_text: Resumo do aprendizado ou solução.
            allowed_tools: Ferramentas permitidas.

        Returns:
            LearnedSkillDraft: Objeto com o rascunho da skill e conteúdo markdown.
        """
        slug = re.sub(r"[^a-z0-9-]+", "-", skill_name.lower()).strip("-")
        work_path = self.root / "work" / work_item_id

        # Tenta ler notas ou ledger
        ledger_path = work_path / "documentation" / "delivery-ledger.md"
        context_notes = summary_text
        if ledger_path.is_file() and not context_notes:
            context_notes = ledger_path.read_text(encoding="utf-8", errors="replace")

        tools_list = allowed_tools or ["view_file", "replace_file_content", "run_command"]
        tools_yaml = "\n".join(f"  - {t}" for t in tools_list)

        description = f"Autonomously synthesized skill from {work_item_id} for {slug.replace('-', ' ')}."
        md_content = f"""---
name: {slug}
description: {description}
version: 1.0.0
author: auto-skill-learner
source_work_item: {work_item_id}
target_persona: {target_persona}
standard: agentskills.io
allowed-tools:
{tools_yaml}
---

# {slug.replace('-', ' ').title()}

## Overview
{description}

## Trigger Conditions
- Applied when encountering domain tasks related to `{slug}`.
- Triggered by `{target_persona}` during implementation or investigation.

## Procedure
1. **Analyze Context**: Review incoming requirements and constraints from the work item.
2. **Execute Steps**:
   - Apply the canonical implementation pattern established in `{work_item_id}`.
   - Use native IDE tools (`view_file`, `replace_file_content`) instead of raw shell commands.
   - Enforce Clean Code and Component Contract standards.
3. **Verify Outcome**: Run automated tests and static linters before claiming completion.

## Evidence & Verification
- Verify against tests: `python -m pytest scripts/tests/`
- Check structure: `python scripts/validate_structure.py`

## Notes & Learned Patterns
{context_notes or 'Pattern validated through standard SDLC lifecycle.'}
"""
        return LearnedSkillDraft(
            skill_name=slug,
            description=description,
            target_persona=target_persona,
            trigger_conditions=[f"tasks matching {slug}"],
            steps=["Analyze Context", "Execute Steps", "Verify Outcome"],
            source_work_item=work_item_id,
            raw_markdown=md_content,
        )

    # ── 2. Hermes Hardline Skill Linter ──────────────────────────────────────

    def lint_skill_markdown(self, markdown_text: str, expected_slug: str | None = None) -> list[LintFinding]:
        """Executa linter estrutural e de convenções em um arquivo SKILL.md (estilo Hermes skill_linter.py).

        Args:
            markdown_text: Conteúdo em Markdown do SKILL.md.
            expected_slug: Nome esperado da pasta para verificar alinhamento.

        Returns:
            list[LintFinding]: Lista de achados e violações do padrão.
        """
        findings: list[LintFinding] = []

        # 1. Checa presença de frontmatter YAML
        fm_match = re.match(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|\Z)", markdown_text, re.DOTALL)
        if not fm_match:
            findings.append(LintFinding(
                severity="BLOCKER",
                rule_id="MISSING_FRONTMATTER",
                message="Frontmatter YAML ausente ou malformatado no início do arquivo.",
                line_number=1,
            ))
            return findings

        try:
            meta = yaml.safe_load(fm_match.group(1))
            if not isinstance(meta, dict):
                findings.append(LintFinding(severity="BLOCKER", rule_id="INVALID_YAML", message="Frontmatter não é um dicionário YAML."))
                return findings
        except Exception as exc:
            findings.append(LintFinding(severity="BLOCKER", rule_id="YAML_PARSE_ERROR", message=f"Erro ao parsear YAML: {exc}"))
            return findings

        # 2. Validação de campos obrigatórios
        for req in ("name", "description"):
            if req not in meta or not str(meta[req]).strip():
                findings.append(LintFinding(severity="BLOCKER", rule_id=f"MISSING_{req.upper()}", message=f"Campo obrigatório '{req}' ausente."))

        # 3. Checa alinhamento do nome da pasta com o slug
        if expected_slug and meta.get("name") != expected_slug:
            findings.append(LintFinding(
                severity="BLOCKER",
                rule_id="NAME_DIRECTORY_MISMATCH",
                message=f"Nome no frontmatter '{meta.get('name')}' difere do diretório '{expected_slug}'.",
            ))

        # 4. Checa palavras de marketing proibidas
        desc = str(meta.get("description", "")).lower()
        for word in PROHIBITED_MARKETING_WORDS:
            if word in desc:
                findings.append(LintFinding(
                    severity="WARNING",
                    rule_id="MARKETING_LANGUAGE",
                    message=f"Palavra de marketing proibida '{word}' encontrada na descrição.",
                ))

        # 5. Checa referências a utilitários de shell no corpo ao invés de ferramentas nativas
        body = markdown_text[fm_match.end():]
        lines = body.splitlines()
        for idx, line in enumerate(lines, start=fm_match.group(0).count("\n") + 1):
            for util, tool in SHELL_UTIL_TO_TOOL.items():
                pattern = rf"\b{util}\b"
                if re.search(pattern, line) and not line.strip().startswith("```") and not line.strip().startswith("#"):
                    if "instead of" not in line.lower() and "tool" not in line.lower():
                        findings.append(LintFinding(
                            severity="ADVISORY",
                            rule_id="SHELL_UTIL_PROSE",
                            message=f"Mencionado utilitário de shell '{util}'. Prefira referenciar a ferramenta nativa '{tool}'.",
                            line_number=idx,
                        ))

        return findings

    # ── 3. Prime Agent /refine Trajectory Refinement ─────────────────────────

    def refine_persona_heuristics(self, work_item_id: str, agent_id: str) -> list[str]:
        """Destila heurísticas corretivas a partir do histórico de execuções do work item (estilo Prime /refine).

        Args:
            work_item_id: ID do work item a analisar.
            agent_id: ID da persona analisada.

        Returns:
            list[str]: Lista de novas heurísticas e aprendizados destilados.
        """
        work_path = self.root / "work" / work_item_id
        heuristics: list[str] = []

        # Analisa status.yaml e ledger
        status_path = work_path / "status.yaml"
        ledger_path = work_path / "documentation" / "delivery-ledger.md"

        if status_path.is_file():
            try:
                status_data = yaml.safe_load(status_path.read_text(encoding="utf-8"))
                gates = status_data.get("gates", {})
                for g_id, g_info in gates.items():
                    if g_info.get("status") == "rejected":
                        heuristics.append(f"Em {g_id}: Validar previamente os critérios antes de solicitar transição.")
            except Exception:
                pass

        if ledger_path.is_file():
            ledger_text = ledger_path.read_text(encoding="utf-8", errors="replace")
            if "fail" in ledger_text.lower() or "error" in ledger_text.lower():
                heuristics.append(f"Registrado incidente em {work_item_id}: Reforçar checagem estática antes de submeter artefato.")

        if not heuristics:
            heuristics.append(f"Trajetória de {agent_id} em {work_item_id} convergiu com sucesso; manter padrão estabelecido.")

        return heuristics

    # ── 4. BoostPrompt Prompt Quality Evaluation ──────────────────────────────

    def evaluate_prompt_quality(self, prompt_text: str) -> PromptQualityReport:
        """Avalia a qualidade de um prompt ou briefing segundo as 4 dimensões do BoostPrompt.

        Args:
            prompt_text: Texto do prompt ou briefing a inspecionar.

        Returns:
            PromptQualityReport: Pontuação e sugestões de otimização.
        """
        text = prompt_text.strip()
        suggestions: list[str] = []

        # 1. Clareza e Objetivo (25 pts)
        obj_score = 25.0
        if "objective" not in text.lower() and "missão" not in text.lower() and "mission" not in text.lower():
            obj_score -= 15.0
            suggestions.append("Defina explicitamente a seção 'Objective' com um resultado em uma frase.")
        if len(text) < 100:
            obj_score -= 5.0

        # 2. Ground Truth & Fontes Canônicas (25 pts)
        gt_score = 25.0
        if "ground truth" not in text.lower() and "docs/" not in text and "standards" not in text.lower():
            gt_score -= 15.0
            suggestions.append("Inclua a definição canônica inline com a fonte citada (Ground Truth).")

        # 3. Antifabricação (25 pts)
        anti_score = 25.0
        if "invent" not in text.lower() and "empty" not in text.lower() and "anti-fabrication" not in text.lower():
            anti_score -= 15.0
            suggestions.append("Adicione regra explícita de antifabricação (EMPTY se vazio, NOT FOUND se ausente).")

        # 4. Fronteiras e Ferramentas (25 pts)
        bound_score = 25.0
        if "boundaries" not in text.lower() and "read-only" not in text.lower() and "scope" not in text.lower():
            bound_score -= 10.0
            suggestions.append("Especifique as fronteiras de leitura/escrita e o orçamento de tentativas.")

        total = max(0.0, min(100.0, round(obj_score + gt_score + anti_score + bound_score, 1)))

        return PromptQualityReport(
            overall_score=total,
            objective_clarity=obj_score,
            ground_truth_score=gt_score,
            anti_fabrication_score=anti_score,
            boundary_score=bound_score,
            suggestions=suggestions,
        )

    # ── 5. Intake & Promotion Lifecycle ───────────────────────────────────────

    def deposit_in_intake(self, draft: LearnedSkillDraft) -> Path:
        """Deposita o rascunho de skill em skills/discovery/intake/ para quarentena do Skill Curator.

        Args:
            draft: Rascunho da skill sintetizada.

        Returns:
            Path: Caminho do arquivo SKILL.md gravado.
        """
        intake_dir = self.root / "skills" / "discovery" / "intake" / draft.skill_name
        intake_dir.mkdir(parents=True, exist_ok=True)
        skill_file = intake_dir / "SKILL.md"
        skill_file.write_text(draft.raw_markdown, encoding="utf-8")
        return skill_file

    def promote_skill(self, skill_name: str, target_domain: str = "engineering") -> tuple[bool, str]:
        """Promove uma skill da quarentena de intake para o catálogo ativo (18-skill-curator).

        Args:
            skill_name: Nome da skill em intake.
            target_domain: Domínio de destino (engineering, security, data, ai, delivery).

        Returns:
            tuple[bool, str]: (Sucesso, Mensagem descritiva).
        """
        from scripts.skill_curator import review_intake_skill

        intake_dir = self.root / "skills" / "discovery" / "intake" / skill_name
        skill_file = intake_dir / "SKILL.md"

        if not skill_file.is_file():
            return False, f"Skill '{skill_name}' não encontrada em skills/discovery/intake/."

        # Gate de curadoria antes da promoção
        report = review_intake_skill(intake_dir)
        blockers = [f for f in report.findings if f.severity == "BLOCKER"]
        if blockers:
            reasons = "; ".join(f"[{f.gate}] {f.message}" for f in blockers)
            return False, f"Promoção bloqueada pelo 18-skill-curator: {reasons}"

        content = skill_file.read_text(encoding="utf-8")
        findings = self.lint_skill_markdown(content, expected_slug=skill_name)
        blockers = [f for f in findings if f.severity == "BLOCKER"]

        if blockers:
            reasons = "; ".join(b.message for b in blockers)
            return False, f"Promoção bloqueada pelo linter Hermes: {reasons}"

        # Destino final
        target_dir = self.root / "skills" / target_domain / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(skill_file), str(target_dir / "SKILL.md"))

        # Remove do intake
        shutil.rmtree(str(intake_dir), ignore_errors=True)

        # Atualiza catálogo central
        _update_skills_catalog(self.root, skill_name, target_domain, target_dir)

        return True, f"Skill '{skill_name}' promovida com sucesso para 'skills/{target_domain}/{skill_name}'."

    # ── 6. AST-based Local Discovery (Hermes pattern) ─────────────────────────

    def discover_local_skills(self, skills_dir: Optional[Path] = None) -> list[DiscoveredSkill]:
        """Descobre skills locais escaneando ``skills/**/SKILL.md`` via AST.

        Usa cache disco por ``(mtime_ns, size)`` para não repetir scan.

        Args:
            skills_dir: diretório base de skills. Se ``None``, usa ``self.root / "skills"``.

        Returns:
            Lista de ``DiscoveredSkill``.
        """
        return discover_skills(skills_dir or (self.root / "skills"))

    def discover_local_tools(self, tools_dir: Optional[Path] = None) -> list[DiscoveredTool]:
        """Descobre tools auto-registradas escaneando módulos Python.

        Usa AST scan para encontrar ``registry.register(...)`` no top-level.

        Args:
            tools_dir: diretório base de tools. Se ``None``, usa ``self.root / "tools"``.

        Returns:
            Lista de ``DiscoveredTool``.
        """
        return discover_tools(tools_dir or (self.root / "tools"))


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para o motor de autoaprendizagem, linter e refinamento."""
    parser = argparse.ArgumentParser(description="Motor de Autoaprendizagem de Skills e Refinamento do Squad")
    subparsers = parser.add_subparsers(dest="command", help="Comando a executar")

    # Subcomando learn
    learn_p = subparsers.add_parser("learn", help="Sintetizar nova skill a partir de work item (/learn)")
    learn_p.add_argument("--work-item", required=True, help="ID do work item de origem")
    learn_p.add_argument("--name", required=True, help="Nome canônico da skill")
    learn_p.add_argument("--persona", default="software-engineer", help="Persona atribuída")
    learn_p.add_argument("--summary", default="", help="Resumo do aprendizado")

    # Subcomando lint
    lint_p = subparsers.add_parser("lint", help="Executar linter de convenções Hermes em uma skill")
    lint_p.add_argument("--path", required=True, help="Caminho para o arquivo SKILL.md")

    # Subcomando refine
    refine_p = subparsers.add_parser("refine", help="Destilar regras de autorreparo de trajetória (/refine)")
    refine_p.add_argument("--work-item", required=True, help="ID do work item")
    refine_p.add_argument("--agent", default="software-engineer", help="ID do agente")

    # Subcomando eval-prompt
    eval_p = subparsers.add_parser("eval-prompt", help="Avaliar qualidade de prompt no padrão BoostPrompt")
    eval_p.add_argument("--path", required=True, help="Caminho para o arquivo de prompt ou briefing")

    # Subcomando promote
    prom_p = subparsers.add_parser("promote", help="Promover skill da quarentena de intake para catálogo ativo")
    prom_p.add_argument("--name", required=True, help="Nome da skill")
    prom_p.add_argument("--domain", default="engineering", help="Domínio de destino")

    # Subcomando discover-skills
    disc_p = subparsers.add_parser("discover-skills", help="Descobrir skills locais via AST scan")
    disc_p.add_argument("--dir", default=None, help="Diretório de skills (padrão: skills/)")

    # Subcomando discover-tools
    dtools_p = subparsers.add_parser("discover-tools", help="Descobrir tools locais via AST scan")
    dtools_p.add_argument("--dir", default=None, help="Diretório de tools (padrão: tools/)")

    # Subcomando select-skills
    sel_p = subparsers.add_parser("select-skills", help="Rankear skills relevantes para uma tarefa")
    sel_p.add_argument("--task", required=True, help="Descrição da tarefa")
    sel_p.add_argument("--persona", default=None, help="Persona atribuída ao agente")
    sel_p.add_argument("--dir", default=None, help="Diretório de skills (padrão: skills/)")
    sel_p.add_argument("--max", type=int, default=3, help="Máximo de sugestões")

    args = parser.parse_args(argv or sys.argv[1:])
    learner = AutoSkillLearner()

    if args.command == "learn" or (args.command is None and hasattr(args, "work_item")):
        draft = learner.synthesize_skill_from_work_item(
            work_item_id=getattr(args, "work_item", "TASK-001"),
            skill_name=getattr(args, "name", "new-skill"),
            target_persona=getattr(args, "persona", "software-engineer"),
            summary_text=getattr(args, "summary", ""),
        )
        path = learner.deposit_in_intake(draft)
        print(f"AUTO_SKILL_CREATED: Skill depositada com sucesso em '{path}' para quarentena do 18-skill-curator.")
        return 0

    elif args.command == "lint":
        path = Path(args.path)
        if not path.is_file():
            print(f"ERRO: Arquivo não encontrado: {path}")
            return 1
        content = path.read_text(encoding="utf-8")
        findings = learner.lint_skill_markdown(content, expected_slug=path.parent.name)
        if not findings:
            print("SUCESSO: SKILL.md 100% conforme com o padrão Hermes e agentskills.io.")
            return 0
        print(f"LINTER FINDINGS ({len(findings)}):")
        for f in findings:
            print(f"  [{f.severity}] {f.rule_id} (L{f.line_number}): {f.message}")
        return 1 if any(f.severity == "BLOCKER" for f in findings) else 0

    elif args.command == "refine":
        heuristics = learner.refine_persona_heuristics(args.work_item, args.agent)
        print(f"PRIME_REFINE_RESULTS para {args.agent} em {args.work_item}:")
        for h in heuristics:
            print(f"  - {h}")
        return 0

    elif args.command == "eval-prompt":
        path = Path(args.path)
        if not path.is_file():
            print(f"ERRO: Arquivo não encontrado: {path}")
            return 1
        content = path.read_text(encoding="utf-8")
        report = learner.evaluate_prompt_quality(content)
        print(f"BOOSTPROMPT QUALITY INDEX: {report.overall_score}/100")
        print(f"  - Clareza do Objetivo: {report.objective_clarity}/25")
        print(f"  - Ground Truth Inlined: {report.ground_truth_score}/25")
        print(f"  - Antifabricação: {report.anti_fabrication_score}/25")
        print(f"  - Fronteiras & Escopo: {report.boundary_score}/25")
        if report.suggestions:
            print("Sugestões de Refinamento:")
            for s in report.suggestions:
                print(f"  * {s}")
        return 0

    elif args.command == "promote":
        success, msg = learner.promote_skill(args.name, args.domain)
        print(msg)
        return 0 if success else 1

    elif args.command == "discover-skills":
        skills_dir = Path(args.dir) if args.dir else None
        skills = learner.discover_local_skills(skills_dir)
        if not skills:
            print("Nenhuma skill local descoberta.")
            return 0
        print(f"Skills descobertas ({len(skills)}):")
        for s in skills:
            print(f"  - {s.name}: {s.path}")
        return 0

    elif args.command == "discover-tools":
        tools_dir = Path(args.dir) if args.dir else None
        tools = learner.discover_local_tools(tools_dir)
        if not tools:
            print("Nenhuma tool local descoberta.")
            return 0
        print(f"Tools descobertas ({len(tools)}):")
        for t in tools:
            print(f"  - {t.name}: {t.module}")
        return 0

    elif args.command == "select-skills":
        from scripts.skill_selector import suggest_and_load

        skills_dir = Path(args.dir) if args.dir else learner.root / "skills"
        suggestions = suggest_and_load(
            task=args.task,
            skills_dir=skills_dir,
            persona=args.persona,
            max_results=args.max,
        )
        if not suggestions:
            print("Nenhuma skill relevante encontrada para a tarefa.")
            return 0
        print(f"Skills sugeridas para: {args.task!r}")
        for i, s in enumerate(suggestions, 1):
            r = s.ranked
            status = "carregada" if s.loaded else (s.error or "erro desconhecido")
            print(f"\n{i}. {r.skill.name} (score={r.score:.2f})")
            print(f"   path: {r.skill.path}")
            print(f"   status: {status}")
            print(f"   motivos: {', '.join(r.match_reasons)}")
            if s.content:
                preview = s.content[:200].replace("\n", " ")
                print(f"   preview: {preview}...")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
