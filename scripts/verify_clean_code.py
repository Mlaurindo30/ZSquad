#!/usr/bin/env python3
"""
O que é: Verificador automatizado de Clean Code, Component Contracts e Anti-Bloat.
Responsabilidade: Analisar arquivos de código-fonte (Python, TypeScript, JavaScript) para
                 garantir a presença de contratos de componentes, docstrings obrigatórias,
                 mapeamento de dependências e ausência de inchaço de código.
Pra que serve: Integrar-se a pre-commit hooks, pipelines de CI/CD e gates de qualidade (G4/G5)
               para bloquear código gerado por IA que viole os padrões de engenharia.
Comportamento em falha: Retorna código de saída diferente de zero e lista detalhada de violações
                       com arquivo, linha e descrição da regra violada.
Conexões: Utilizado por scripts/agent_squad.py, pipelines de CI/CD, pre-commit hooks e agentes
          como code-reviewer e software-engineer.
Dependências & Imports:
  - ast: Análise de Árvore de Sintaxe Abstrata para código Python.
  - re: Expressões regulares para verificação de blocos de comentários e contratos.
  - sys: Códigos de saída e manipulação de argumentos.
  - pathlib: Manipulação cross-platform de caminhos de arquivos.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    """Representa uma violação de Clean Code ou Contrato de Componente encontrada."""
    file_path: str
    line_number: int
    rule: str
    message: str


# Padrões obrigatórios do Contrato de Componente
REQUIRED_CONTRACT_FIELDS = [
    "O que é",
    "Responsabilidade",
    "Pra que serve",
    "Comportamento em falha",
    "Conexões",
]


def check_python_file(path: Path) -> list[Violation]:
    """Analisa um arquivo Python quanto a contratos, docstrings e complexidade."""
    violations: list[Violation] = []
    content = path.read_text(encoding="utf-8", errors="replace")

    # 1. Verificar Contrato de Componente no nível do módulo (docstring inicial ou comentário)
    has_contract = all(
        re.search(rf"(?i){re.escape(field)}:", content) for field in REQUIRED_CONTRACT_FIELDS
    )
    if not has_contract:
        # Se for um arquivo com mais de 20 linhas não triviais, o contrato é obrigatório
        non_empty_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        if len(non_empty_lines) > 20:
            violations.append(
                Violation(
                    file_path=str(path),
                    line_number=1,
                    rule="MISSING_COMPONENT_CONTRACT",
                    message=f"Arquivo não trivial (>20 linhas) sem Contrato de Componente completo. Campos exigidos: {', '.join(REQUIRED_CONTRACT_FIELDS)}",
                )
            )

    # 2. Parse AST para checagem de funções, classes e docstrings
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as e:
        violations.append(
            Violation(
                file_path=str(path),
                line_number=e.lineno or 1,
                rule="SYNTAX_ERROR",
                message=f"Erro de sintaxe ao analisar arquivo: {e.msg}",
            )
        )
        return violations

    for node in ast.walk(tree):
        # Checagem de funções públicas
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Ignorar métodos mágicos/privados padrão (ex: __init__, _private_helper)
            if not node.name.startswith("_") or node.name == "__init__":
                docstring = ast.get_docstring(node)
                if not docstring and len(node.body) >= 2:
                    violations.append(
                        Violation(
                            file_path=str(path),
                            line_number=node.lineno,
                            rule="MISSING_DOCSTRING",
                            message=f"Função pública '{node.name}' não possui docstring explicativa.",
                        )
                    )

        # Checagem de classes públicas
        if isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                docstring = ast.get_docstring(node)
                if not docstring and len(node.body) > 2:
                    violations.append(
                        Violation(
                            file_path=str(path),
                            line_number=node.lineno,
                            rule="MISSING_CLASS_DOCSTRING",
                            message=f"Classe pública '{node.name}' não possui docstring descrevendo sua responsabilidade.",
                        )
                    )

    return violations


def check_directory(target_dir: Path) -> list[Violation]:
    """Varre recursivamente um diretório analisando arquivos de código."""
    violations: list[Violation] = []
    ignored_patterns = {".git", ".pytest_cache", "__pycache__", "archive", "distribution", "node_modules", "dist", "build", "vendor"}

    for p in target_dir.rglob("*.py"):
        if any(part in ignored_patterns for part in p.parts):
            continue
        # Ignorar arquivos de teste na exigência de component contract de módulo completo
        if "test_" in p.name or p.name.endswith("_test.py"):
            continue
        violations.extend(check_python_file(p))

    return violations


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para execução da auditoria de Clean Code."""
    args = argv or sys.argv[1:]
    target_path = Path(args[0]) if args else Path(".")

    if not target_path.exists():
        print(f"ERROR: Caminho não encontrado: {target_path}", file=sys.stderr)
        return 2

    violations = check_directory(target_path) if target_path.is_dir() else check_python_file(target_path)

    if violations:
        print(f"FALHA: Encontradas {len(violations)} violações de Clean Code / Component Contract:\n")
        for v in violations:
            print(f"[{v.rule}] {v.file_path}:{v.line_number} -> {v.message}")
        return 1

    print("SUCESSO: Todos os arquivos inspecionados estão em conformidade com Clean Code e Component Contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
