#!/usr/bin/env python3
"""
O que é: Divisor funcional de código-fonte em chunks semânticos contextuais baseados em nós AST (cAST).
Responsabilidade: Segmentar funções, classes e blocos lógicos preservando limites de sintaxe para busca e indexação.
Pra que serve: Permitir recuperação precisa de trechos de código em buscas semânticas sem quebrar a estrutura lógica.
Comportamento em falha: Retorna chunk do módulo completo caso ocorra erro de sintaxe.
Conexões: Conecta-se com banco/squad.db e os motores de busca e recuperação de memória.
Dependências & Imports:
  - ast, json, pathlib, sys: Utilitários padrão.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] if Path(__file__).resolve().parent.name == "experimental" else Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class ContextualASTChunker:
    """Divisor semântico de código baseado em nós sintáticos da AST."""

    def __init__(self) -> None:
        """Inicializa o chunker contextual."""

    def chunk_python_source(self, source_code: str, file_path: str = "<source>") -> list[dict[str, Any]]:
        """Divide o código-fonte em chunks semânticos baseados em nós AST de funções e classes.

        Args:
            source_code: Código-fonte em string.
            file_path: Caminho de referência do arquivo.

        Returns:
            list[dict[str, Any]]: Lista de chunks com tipo, linhas e conteúdo textual.
        """
        try:
            tree = ast.parse(source_code, filename=file_path)
        except SyntaxError:
            return [{"type": "raw", "start_line": 1, "end_line": len(source_code.splitlines()), "content": source_code}]

        lines = source_code.splitlines()
        chunks: list[dict[str, Any]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", start + 5)
                chunk_lines = lines[start - 1 : end]
                chunks.append({
                    "name": node.name,
                    "type": "class" if isinstance(node, ast.ClassDef) else "function",
                    "start_line": start,
                    "end_line": end,
                    "content": "\n".join(chunk_lines),
                })

        if not chunks:
            chunks.append({"type": "module", "start_line": 1, "end_line": len(lines), "content": source_code})

        return chunks
