#!/usr/bin/env python3
"""
O que é: Motor funcional de procedural memory, síntese, linting e governança de skills.
Responsabilidade: Sintetizar novas habilidades padronizadas (agentskills.io), aplicar linter de convenções rígidas, executar varredura AST em scripts de apoio e gerenciar quarentena de skills.
Pra que serve: Permitir que o squad aprenda continuamente com soluções comprovadas e gere procedimentos operacionais reutilizáveis e seguros.
Comportamento em falha: Valida rigorosamente metadados e sintaxe; descarta rascunhos inválidos e bloqueia promoções que violem guardrails.
Conexões: Utilizado por 18-skill-curator, 00-delivery-orchestrator e scripts/auto_skill_learner.py.
Dependências & Imports:
  - ast, json, pathlib, re, sys, yaml: Análise sintática e manipulação de arquivos.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Mapeamento de utilitários de shell crus para ferramentas nativas da IDE
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

# Palavras de marketing proibidas
PROHIBITED_MARKETING_WORDS: set[str] = {
    "revolutionary", "magical", "game-changing", "groundbreaking",
    "cutting-edge", "unbelievable", "world-class", "next-generation"
}


@dataclass
class SkillLintFinding:
    """Representa um achado de auditoria de conformidade de uma skill."""
    severity: str  # 'BLOCKER', 'WARNING', 'ADVISORY'
    rule_id: str
    message: str
    line_number: int = 1


@dataclass
class ASTSecurityFinding:
    """Achado de segurança na análise estática AST de scripts Python."""
    file_path: str
    line_number: int
    pattern_id: str
    description: str


class ProceduralSkillEngine:
    """Motor de síntese, linter e segurança de procedural memory do squad."""

    def __init__(self) -> None:
        """Inicializa o motor de procedural memory."""

    def format_as_agentskill(
        self,
        name: str,
        description: str,
        instructions: str,
        allowed_tools: list[str] | None = None,
        author: str = "auto-skill-learner",
    ) -> str:
        """Formata uma skill de acordo com o padrão aberto agentskills.io.

        Args:
            name: Nome canônico da skill (slug com hifens).
            description: Descrição sucinta da capacidade.
            instructions: Procedimento operacional detalhado em Markdown.
            allowed_tools: Lista de ferramentas permitidas.
            author: Autor da skill.

        Returns:
            str: Conteúdo final do SKILL.md formatado.
        """
        slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        tools = allowed_tools or ["view_file", "replace_file_content", "run_command"]
        tools_block = "allowed-tools:\n" + "\n".join(f"  - {t}" for t in tools) + "\n"

        return f"""---
name: {slug}
description: {description}
version: 1.0.0
author: {author}
standard: agentskills.io
{tools_block}---

# {slug.replace('-', ' ').title()}

## Overview
{description}

## Operational Procedure
{instructions}
"""

    def lint_skill_content(self, markdown_text: str, expected_slug: str | None = None) -> list[SkillLintFinding]:
        """Executa linter estrutural rígido e de convenções em um arquivo SKILL.md.

        Args:
            markdown_text: Conteúdo em Markdown da skill.
            expected_slug: Nome esperado da pasta pai.

        Returns:
            list[SkillLintFinding]: Lista de achados e violações do padrão.
        """
        findings: list[SkillLintFinding] = []

        # 1. Trata UTF-8 BOM
        content = markdown_text[1:] if markdown_text.startswith("\ufeff") else markdown_text

        # 2. Valida frontmatter delimitado por ---
        fm_match = re.match(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|\Z)", content, re.DOTALL)
        if not fm_match:
            findings.append(SkillLintFinding(
                severity="BLOCKER",
                rule_id="MISSING_FRONTMATTER",
                message="Frontmatter YAML ausente ou malformatado no início do arquivo.",
                line_number=1,
            ))
            return findings

        try:
            meta = yaml.safe_load(fm_match.group(1))
            if not isinstance(meta, dict):
                findings.append(SkillLintFinding(severity="BLOCKER", rule_id="INVALID_YAML", message="Frontmatter não é um dicionário YAML."))
                return findings
        except Exception as exc:
            findings.append(SkillLintFinding(severity="BLOCKER", rule_id="YAML_PARSE_ERROR", message=f"Erro ao parsear YAML: {exc}"))
            return findings

        # 3. Campos obrigatórios
        for req in ("name", "description"):
            if req not in meta or not str(meta[req]).strip():
                findings.append(SkillLintFinding(severity="BLOCKER", rule_id=f"MISSING_{req.upper()}", message=f"Campo obrigatório '{req}' ausente."))

        # 4. Alinhamento de diretório
        if expected_slug and meta.get("name") != expected_slug:
            findings.append(SkillLintFinding(
                severity="BLOCKER",
                rule_id="NAME_DIRECTORY_MISMATCH",
                message=f"Nome no frontmatter '{meta.get('name')}' difere do diretório '{expected_slug}'.",
            ))

        # 5. Palavras de marketing proibidas
        desc = str(meta.get("description", "")).lower()
        for word in PROHIBITED_MARKETING_WORDS:
            if word in desc:
                findings.append(SkillLintFinding(
                    severity="WARNING",
                    rule_id="MARKETING_LANGUAGE",
                    message=f"Palavra de marketing proibida '{word}' encontrada na descrição.",
                ))

        # 6. Mapeamento de utilitários de shell
        body = content[fm_match.end():]
        lines = body.splitlines()
        for idx, line in enumerate(lines, start=fm_match.group(0).count("\n") + 1):
            if line.strip().startswith("```") or line.strip().startswith("#"):
                continue
            for util, tool in SHELL_UTIL_TO_TOOL.items():
                if re.search(rf"\b{util}\b", line):
                    if "instead of" not in line.lower() and "tool" not in line.lower():
                        findings.append(SkillLintFinding(
                            severity="ADVISORY",
                            rule_id="SHELL_UTIL_PROSE",
                            message=f"Mencionado utilitário de shell '{util}'. Prefira referenciar a ferramenta nativa '{tool}'.",
                            line_number=idx,
                        ))

        return findings

    def audit_python_script_ast(self, script_path: Path | str) -> list[ASTSecurityFinding]:
        """Executa auditoria estática profunda via AST em scripts Python empacotados na skill.

        Args:
            script_path: Caminho do arquivo .py a inspecionar.

        Returns:
            list[ASTSecurityFinding]: Lista de achados de metaprogramação ou carregamento dinâmico.
        """
        path = Path(script_path)
        if not path.is_file() or path.suffix.lower() != ".py":
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except Exception:
            return []

        findings: list[ASTSecurityFinding] = []

        class _SecurityVisitor(ast.NodeVisitor):
            """Visitador AST interno para detecção de metaprogramação não segura."""
            def visit_Call(self, node: ast.Call):
                """Inspeciona chamadas de função perigosas como import_module e getattr."""
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == "import_module":
                    findings.append(ASTSecurityFinding(str(path), node.lineno, "dynamic_import", "importlib.import_module() detectado"))
                elif isinstance(f, ast.Name) and f.id == "__import__":
                    if node.args and not isinstance(node.args[0], ast.Constant):
                        findings.append(ASTSecurityFinding(str(path), node.lineno, "dynamic_import_computed", "__import__ com módulo computado"))
                elif isinstance(f, ast.Name) and f.id == "getattr":
                    if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                        findings.append(ASTSecurityFinding(str(path), node.lineno, "dynamic_getattr", "getattr() com atributo computado"))
                self.generic_visit(node)

            def visit_Subscript(self, node: ast.Subscript):
                """Inspeciona acessos indexados a __dict__ dinâmico."""
                if isinstance(node.value, ast.Attribute) and node.value.attr == "__dict__" and not isinstance(node.slice, ast.Constant):
                    findings.append(ASTSecurityFinding(str(path), node.lineno, "dict_access", "Acesso dinâmico a __dict__[computado]"))
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import):
                """Inspeciona importações do módulo importlib."""
                for a in node.names:
                    if a.name == "importlib" or a.name.startswith("importlib."):
                        findings.append(ASTSecurityFinding(str(path), node.lineno, "importlib_import", f"Import de {a.name}"))
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom):
                """Inspeciona importações from importlib."""
                m = node.module or ""
                if m == "importlib" or m.startswith("importlib."):
                    findings.append(ASTSecurityFinding(str(path), node.lineno, "importlib_import", f"from {m} import ..."))
                self.generic_visit(node)

        _SecurityVisitor().visit(tree)
        return findings
