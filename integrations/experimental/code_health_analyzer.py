#!/usr/bin/env python3
"""
O que é: Analisador funcional de biomarcadores determinísticos de saúde e complexidade de código.
Responsabilidade: Medir complexidade ciclomática, cobertura de documentação e contratos de componentes em arquivos de código.
Pra que serve: Fornecer pontuações de risco para orientar o foco dos agentes de desenvolvimento e revisão.
Comportamento em falha: Retorna pontuação padrão calculada sem interromper a execução.
Conexões: Conecta-se com banco/squad.db e 09-code-reviewer.
Dependências & Imports:
  - json, pathlib, sys: Utilitários padrão.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class CodeHealthAnalyzer:
    """Analisador de biomarcadores de qualidade e complexidade de código."""

    def __init__(self) -> None:
        """Inicializa o analisador de saúde de código."""

    def compute_health_score(self, total_lines: int, max_complexity: int, docstring_cov: float, has_contract: bool) -> float:
        """Calcula uma pontuação de saúde de código de 0.0 a 10.0 baseada em biomarcadores determinísticos.

        Args:
            total_lines: Total de linhas do arquivo.
            max_complexity: Complexidade ciclomática máxima encontrada.
            docstring_cov: Taxa de cobertura de docstring (0.0 a 1.0).
            has_contract: Se possui Contrato de Componente.

        Returns:
            float: Pontuação de saúde de 0.0 a 10.0.
        """
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
