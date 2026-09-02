#!/usr/bin/env python3
"""
O que é: Analisador funcional de biomarcadores determinísticos de saúde e complexidade de código.
Responsabilidade: Medir complexidade ciclomática, cobertura de documentação e contratos de componentes em arquivos de código.
Pra que serve: Fornecer pontuações de risco para orientar o foco dos agentes de desenvolvimento e revisão.
Comportamento em falha: Retorna pontuação padrão calculada sem interromper a execução.
Conexões: Conecta-se com banco/squad.db, LocalAgentDB e 09-code-reviewer.
Dependências & Imports:
  - ast, json, pathlib, sys, logging: Utilitários padrão.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class CodeHealthAnalyzer:
    """Analisador de biomarcadores de qualidade e complexidade de código."""

    def __init__(self) -> None:
        """Inicializa o analisador de saúde de código."""

    def analyze_python_file(self, file_path: Path) -> dict[str, Any]:
        """Extrai métricas determinísticas e calcula a nota de saúde de um arquivo Python.

        Args:
            file_path: Caminho do arquivo a ser analisado.

        Returns:
            dict[str, Any]: Dicionário com linhas, complexidade, docstrings, contrato e score.
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"file": str(file_path), "error": str(e), "health_score": 0.0}

        lines = content.splitlines()
        total_lines = len(lines)

        has_contract = "O que é:" in content and "Responsabilidade:" in content

        try:
            tree = ast.parse(content)
        except Exception:
            # Erro de sintaxe resulta em health_score zero
            return {
                "file": str(file_path),
                "total_lines": total_lines,
                "syntax_valid": False,
                "health_score": 0.0,
            }

        func_count = 0
        docstring_count = 0
        max_complexity = 1

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_count += 1
                if ast.get_docstring(node):
                    docstring_count += 1
                
                # Complexidade ciclomática básica (contagem de ramos)
                complexity = 1
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With, ast.Assert)):
                        complexity += 1
                if complexity > max_complexity:
                    max_complexity = complexity

        docstring_cov = (docstring_count / func_count) if func_count > 0 else 1.0
        score = self.compute_health_score(total_lines, max_complexity, docstring_cov, has_contract)

        return {
            "file": str(file_path),
            "total_lines": total_lines,
            "functions_count": func_count,
            "max_complexity": max_complexity,
            "docstring_coverage": round(docstring_cov, 2),
            "has_component_contract": has_contract,
            "health_score": score,
            "status": "HEALTHY" if score >= 7.0 else ("WARNING" if score >= 5.0 else "CRITICAL"),
        }

    def compute_health_score(self, total_lines: int, max_complexity: int, docstring_cov: float, has_contract: bool) -> float:
        """Calcula uma pontuação de saúde de código de 0.0 a 10.0 baseada em biomarcadores determinísticos."""
        score = 10.0

        # Penalidade por tamanho excessivo
        if total_lines > 500:
            score -= 2.0
        elif total_lines > 200:
            score -= 1.0

        # Penalidade por complexidade alta
        if max_complexity > 10:
            score -= 3.0
        elif max_complexity > 6:
            score -= 1.5

        # Penalidade por falta de docstrings
        score -= (1.0 - docstring_cov) * 2.0

        # Penalidade por ausência de contrato em arquivos não triviais
        if total_lines > 20 and not has_contract:
            score -= 2.0

        return max(0.0, min(10.0, round(score, 1)))
