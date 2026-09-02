#!/usr/bin/env python3
"""gitingest integration skill.

O que é: skill de integração para compactar repositórios em contexto compacto.
Responsabilidade: expor função Python para gerar representação condensada de um diretório/codebase, usada por agents antes de carregar codebase inteira em contexto.
Pra que serve: reduzir tokens em blast_radius_analyzer, codebase_knowledge_graph, code_health_analyzer.
Comportamento em falha: retorna texto vazio + mensagem de erro; não levanta exceção.
Conexões: integrations/, usado por render_agent_prompt e engines.
Dependências & Imports:
  - pathlib, hashlib, re: caminho, hash, regex.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable


_DEFAULT_EXCLUDES = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules",
    "dist", "build", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def ingest(
    root: str | Path,
    *,
    max_tokens: int = 4000,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
) -> str:
    """Compacta um diretório em texto estruturado para contexto de LLM.

    Args:
        root: raiz do repositório/diretório.
        max_tokens: limite alvo de tokens (aproximado por chars/4).
        include: padrões glob de inclusão.
        exclude: pastas/arquivos adicionais a excluir.
        extensions: filtrar por extensão (ex: .py,.md).

    Returns:
        String compacta com estrutura e hashes dos arquivos relevantes.
    """
    root = Path(root).resolve()
    exclude_set = set(exclude or []) | _DEFAULT_EXCLUDES
    include_list = list(include or [])
    ext_list = [e if e.startswith(".") else "." + e for e in (extensions or [])]

    lines: list[str] = []
    lines.append(f"# gitingest: {root}")
    lines.append(f"ingest_id={_hash(str(root) + str(max_tokens))}")
    lines.append("")

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        parts = list(rel.parts)
        if any(part in exclude_set for part in parts):
            continue
        if include_list and not any(rel.match(p) for p in include_list):
            continue
        if ext_list and path.suffix not in ext_list:
            continue
        files.append(path)

    lines.append(f"files={len(files)}")
    lines.append("")
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            preview = text[:400].replace("\n", " ")
            lines.append(f"- {rel} ({len(text)} bytes, sha256={sha})")
            if preview.strip():
                lines.append(f"  preview: {preview}")
        except Exception:
            continue

    content = "\n".join(lines)
    budget = max_tokens * 4
    if len(content) > budget:
        content = content[:budget] + "\n... [truncated by gitingest]"
    return content
