#!/usr/bin/env python3
"""
O que é: Construtor determinístico de grafo de conhecimento de código (Knowledge Graph baseado em AST).
Responsabilidade: Mapear símbolos, funções, classes, módulos e dependências em nós e arestas navegáveis.
Pra que serve: Prover visão holística da arquitetura e das conexões do código para arquitetos e engenheiros.
Comportamento em falha: Retorna grafo vazio com contagens zeradas caso não haja símbolos processados.
Conexões: Conecta-se com banco/squad.db, LocalAgentDB e 04-solution-architect.
Dependências & Imports:
  - json, pathlib, sys, logging, collections: Utilitários padrão.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class CodebaseKnowledgeGraph:
    """Construtor e navegador de grafo de conhecimento de código."""

    def __init__(self) -> None:
        """Inicializa o construtor de grafos de conhecimento."""

    def build_graph_representation(self, symbols: list[dict[str, Any]], dependencies: list[dict[str, Any]]) -> dict[str, Any]:
        """Constrói uma representação em grafo (nós e arestas) com métricas de acoplamento e centralidade.

        Args:
            symbols: Lista de símbolos (funções, classes).
            dependencies: Lista de importações e chamadas.

        Returns:
            dict[str, Any]: Grafo formatado com nós, arestas e nós mais acoplados (hotspots).
        """
        nodes = [{"id": s["name"], "kind": s.get("kind", "symbol"), "file": s.get("file_path", "")} for s in symbols]
        edges = [
            {"source": d.get("source_file", ""), "target": d.get("target_module", ""), "kind": d.get("kind", "dependency")}
            for d in dependencies
        ]

        # Cálculo de acoplamento (In-Degree / Out-Degree)
        in_degree: dict[str, int] = defaultdict(int)
        out_degree: dict[str, int] = defaultdict(int)

        for edge in edges:
            src = edge["source"]
            tgt = edge["target"]
            if src:
                out_degree[src] += 1
            if tgt:
                in_degree[tgt] += 1

        top_dependencies = sorted(in_degree.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "top_depended_modules": [{"module": mod, "dependents_count": count} for mod, count in top_dependencies],
        }
