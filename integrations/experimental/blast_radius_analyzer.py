#!/usr/bin/env python3
"""
O que é: Analisador funcional de Blast Radius e cálculo de raio de impacto de alterações.
Responsabilidade: Calcular arquivos dependentes, módulos afetados e símbolos em risco a partir do grafo de dependências.
Pra que serve: Prevenir leituras redundantes de arquivos pelo agente, reduzindo consumo de tokens e evitando quebras em cadeia.
Comportamento em falha: Retorna lista vazia de dependências caso o módulo não possua dependentes mapeados.
Conexões: Conecta-se com banco/squad.db, 09-code-reviewer e 10-security-reviewer.
Dependências & Imports:
  - json, pathlib, sys: Utilitários padrão.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class BlastRadiusAnalyzer:
    """Analisador determinístico de raio de impacto e dependências."""

    def __init__(self) -> None:
        """Inicializa o analisador de blast radius."""

    def format_blast_radius_report(self, blast_data: dict[str, Any]) -> str:
        """Formata o relatório de raio de impacto para inclusão nos briefings e reviews de agentes.

        Args:
            blast_data: Dados de arquivos dependentes e símbolos em risco.

        Returns:
            str: Relatório formatado em Markdown.
        """
        target = blast_data.get("target", "unknown")
        count = blast_data.get("dependent_files_count", 0)
        files = blast_data.get("dependent_files", [])

        file_list = "\n".join(f"- `{f}`" for f in files) if files else "- Nenhum arquivo dependente detectado."

        return f"""### Relatório de Blast Radius & Impacto
- **Alvo Analisado**: `{target}`
- **Arquivos Dependentes Afetados**: {count}

#### Lista de Impacto
{file_list}
"""
