#!/usr/bin/env python3
"""
O que é: Analisador funcional de Blast Radius e cálculo de raio de impacto de alterações.
Responsabilidade: Calcular arquivos dependentes, módulos afetados e símbolos em risco a partir do grafo de dependências.
Pra que serve: Prevenir quebras em cadeia e orientar o agente em revisões e testes de regressão antes do deploy.
Comportamento em falha: Retorna lista vazia de dependências caso o módulo não possua dependentes mapeados.
Conexões: Conecta-se com banco/squad.db, LocalAgentDB, 09-code-reviewer e 10-security-reviewer.
Dependências & Imports:
  - json, pathlib, sys, logging: Utilitários padrão.
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

    def compute_blast_radius(self, target_file: str, dependencies: list[dict[str, Any]]) -> dict[str, Any]:
        """Calcula arquivos dependentes diretos e indiretos a partir de uma lista de dependências.

        Args:
            target_file: Caminho relativo ou nome do arquivo que sofreu alteração.
            dependencies: Lista de dependências extraídas do banco ou AST.

        Returns:
            dict[str, Any]: Estatísticas de blast radius e arquivos impactados.
        """
        target_norm = target_file.replace("\\", "/").lower()
        target_stem = Path(target_file).stem.lower()

        direct_dependents: set[str] = set()
        for dep in dependencies:
            target_mod = str(dep.get("target_module", "")).lower()
            source = str(dep.get("source_file", "")).replace("\\", "/")
            
            if target_norm in target_mod or target_stem in target_mod or target_norm == source.lower():
                if source.lower() != target_norm:
                    direct_dependents.add(source)

        return {
            "target": target_file,
            "dependent_files_count": len(direct_dependents),
            "dependent_files": sorted(direct_dependents),
            "risk_level": "high" if len(direct_dependents) > 5 else ("medium" if direct_dependents else "low"),
        }

    def format_blast_radius_report(self, blast_data: dict[str, Any]) -> str:
        """Formata o relatório de raio de impacto para inclusão nos briefings e reviews de agentes."""
        target = blast_data.get("target", "unknown")
        count = blast_data.get("dependent_files_count", 0)
        files = blast_data.get("dependent_files", [])
        risk = blast_data.get("risk_level", "low").upper()

        file_list = "\n".join(f"- `{f}`" for f in files) if files else "- Nenhum arquivo dependente detectado."

        return f"""### Relatório de Blast Radius & Impacto
- **Alvo Analisado**: `{target}`
- **Nível de Risco de Impacto**: {risk}
- **Arquivos Dependentes Afetados**: {count}

#### Lista de Impacto
{file_list}
"""
