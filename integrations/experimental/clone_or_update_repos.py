#!/usr/bin/env python3
"""
O que é: Gerenciador de download, clonagem e atualização de repositórios externos integrados ao Agents Squad.
Responsabilidade: Clonar e sincronizar os repositórios oficiais upstream em integrations/vendor/ (BoostPrompt, SDLC-Agents, Graphify, Trace-MCP, Codebase-Memory, ChunkHound, Repowise).
Pra que serve: Prover código-fonte real e dependências upstream para os adapters de inteligência e autoaprendizagem do squad.
Comportamento em falha: Se algum repositório falhar na rede, reporta o erro e continua para os demais sem interromper a execução.
Conexões: Alimenta os adapters em integrations/*_adapter.py e o catálogo de ferramentas MCP.
Dependências & Imports:
  - subprocess, sys, pathlib, argparse: Operações de execução de comandos git e controle de arquivos.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "integrations" / "vendor"
logger = logging.getLogger(__name__)


class RepoSpec(NamedTuple):
    """Especificação de repositório upstream com nome, URL e descrição."""
    name: str
    url: str
    description: str


UPSTREAM_REPOSITORIES = [
    RepoSpec("boostprompt", "https://github.com/AirtonLira/boostprompt.git", "BoostPrompt - Prompt optimization and reinforcement"),
    RepoSpec("sdlc-agents", "https://github.com/cmwen/sdlc-agents.git", "SDLC Agents - Lightweight role-based SDLC handoffs"),
    RepoSpec("graphify", "https://github.com/Graphify-Labs/graphify.git", "Graphify - Local AST Knowledge Graph for codebases"),
    RepoSpec("trace-mcp", "https://github.com/nikolai-vysotskyi/trace-mcp.git", "Trace MCP - Framework-aware blast radius and impact analysis"),
    RepoSpec("codebase-memory-mcp", "https://github.com/DeusData/codebase-memory-mcp.git", "Codebase Memory MCP - Structural symbol graph"),
    RepoSpec("chunkhound", "https://github.com/chunkhound/chunkhound.git", "ChunkHound - Context-aware semantic cAST chunking"),
    RepoSpec("repowise", "https://github.com/repowise-dev/repowise.git", "Repowise - Code health biomarkers and complexity analysis"),
]


def clone_or_update(repo: RepoSpec, vendor_root: Path, shallow: bool = True) -> tuple[str, bool, str]:
    """Clona ou atualiza um repositório git no diretório de vendor.

    Args:
        repo: Especificação do repositório (nome, url, descrição).
        vendor_root: Diretório raiz de vendor.
        shallow: Se True, usa --depth 1 para otimizar tempo e espaço de disco.

    Returns:
        tuple[str, bool, str]: (Nome do repo, Sucesso, Mensagem descritiva).
    """
    target_dir = vendor_root / repo.name
    vendor_root.mkdir(parents=True, exist_ok=True)

    if target_dir.is_dir() and (target_dir / ".git").is_dir():
        try:
            res = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if res.returncode == 0:
                return (repo.name, True, f"Atualizado com sucesso ({res.stdout.strip() or 'up to date'})")
            else:
                return (repo.name, False, f"Falha no git pull: {res.stderr.strip()}")
        except Exception as exc:
            return (repo.name, False, f"Exceção ao atualizar: {exc}")
    else:
        cmd = ["git", "clone"]
        if shallow:
            cmd.extend(["--depth", "1"])
        cmd.extend([repo.url, str(target_dir)])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if res.returncode == 0:
                return (repo.name, True, f"Clonado com sucesso em '{target_dir.name}'")
            else:
                return (repo.name, False, f"Falha no git clone: {res.stderr.strip()}")
        except Exception as exc:
            return (repo.name, False, f"Exceção ao clonar: {exc}")


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para sincronização dos repositórios de vendor."""
    parser = argparse.ArgumentParser(description="Gerenciador de Integrações e Repositórios Upstream")
    parser.add_argument("--vendor-dir", default=str(VENDOR_DIR), help="Diretório de destino dos clones")
    parser.add_argument("--repo", help="Nome de um repositório específico para clonar/atualizar")
    parser.add_argument("--no-shallow", action="store_true", help="Clonar histórico completo (sem --depth 1)")
    args = parser.parse_args(argv or sys.argv[1:])

    vendor_path = Path(args.vendor_dir)
    shallow = not args.no_shallow

    targets = UPSTREAM_REPOSITORIES
    if args.repo:
        targets = [r for r in UPSTREAM_REPOSITORIES if r.name == args.repo]
        if not targets:
            print(f"ERRO: Repositório '{args.repo}' não encontrado no catálogo de integrações.")
            return 1

    print(f"Sincronizando {len(targets)} repositórios upstream em '{vendor_path}'...")
    logger.info("Sincronizando %d repositórios upstream em '%s'.", len(targets), vendor_path)
    success_count = 0

    for spec in targets:
        name, success, msg = clone_or_update(spec, vendor_path, shallow=shallow)
        status_tag = "[OK]" if success else "[FALHA]"
        logger.info("%s %s: %s", status_tag, name, msg)
        if success:
            success_count += 1

    logger.info("Resumo: %d/%d repositórios sincronizados.", success_count, len(targets))
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
