#!/usr/bin/env python3
"""
O que é: Construtor determinístico de grafo de conhecimento de código (Knowledge Graph baseado em AST).
Responsabilidade: Mapear símbolos, funções, classes, módulos e dependências em nós e arestas navegáveis.
Pra que serve: Prover visão holística da arquitetura e das conexões do código para arquitetos e engenheiros.
Comportamento em falha: Retorna grafo vazio com contagens zeradas caso não haja símbolos processados.
Conexões: Conecta-se com banco/squad.db, LocalAgentDB e 04-solution-architect.
Dependências & Imports:
  - argparse, json, pathlib, sys, logging, collections: Utilitários padrão.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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
        nodes = [{"id": s.get("name", ""), "kind": s.get("kind", "symbol"), "file": s.get("file_path", "")} for s in symbols]
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


def main(argv: list[str] | None = None) -> int:
    """Interface CLI para consultar squad.db e emitir o grafo de conhecimento estruturado."""
    parser = argparse.ArgumentParser(
        prog="codebase_knowledge_graph",
        description="Gera representação em grafo (nós e arestas) a partir de símbolos e dependências do squad.db.",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=os.environ.get("SQUAD_PROJECT_ID", "agent_squad"),
        help="Namespace do projeto no squad.db (padrão: $SQUAD_PROJECT_ID ou 'agent_squad').",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Caminho opcional do arquivo squad.db.",
    )
    parser.add_argument(
        "--file-filter",
        type=str,
        default=None,
        help="Filtro opcional por caminho de arquivo.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Caminho do arquivo para salvar a saída JSON (se omitido, imprime no stdout).",
    )

    args = parser.parse_args(argv)

    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from local_agent_db import LocalAgentDB

    try:
        db = LocalAgentDB(db_path=args.db_path, project_id=args.project_id)
        symbols = db.get_project_symbols(file_path=args.file_filter)
        dependencies = db.get_project_dependencies(source_file=args.file_filter)

        ckg = CodebaseKnowledgeGraph()
        graph = ckg.build_graph_representation(symbols, dependencies)
        graph["project_id"] = args.project_id

        output_json = json.dumps(graph, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output_json, encoding="utf-8")
        else:
            print(output_json)

        return 0
    except Exception as exc:
        logger.exception("Erro ao gerar grafo de conhecimento: %s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

