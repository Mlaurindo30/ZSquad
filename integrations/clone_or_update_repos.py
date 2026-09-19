#!/usr/bin/env python3
"""
O que é: Gerenciador de download, clonagem e atualização de repositórios externos integrados ao Agents Squad.
Responsabilidade: Clonar e sincronizar os 7 repositórios oficiais upstream em integrations/vendor/ (BoostPrompt, SDLC-Agents, Graphify, Trace-MCP, Codebase-Memory, ChunkHound, Repowise).
Pra que serve: Prover código-fonte real e dependências upstream para os adapters de inteligência e autoaprendizagem do squad.
Comportamento em falha: Se algum repositório falhar, relata a falha e retorna código de saída 1 (fail-closed).
Conexões: Alimenta os adapters em integrations/*_adapter.py e o catálogo de ferramentas MCP.
Dependências & Imports:
  - subprocess, sys, pathlib, argparse, logging: Operações de execução de comandos git e controle de arquivos.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_VENDOR_DIR = ROOT / "integrations" / "vendor"
VENDOR_DIR = CANONICAL_VENDOR_DIR
logger = logging.getLogger(__name__)


class RepoSpec(NamedTuple):
    """Especificação de repositório upstream com nome, URL, descrição e classificação de provisionamento."""
    name: str
    url: str
    description: str
    classification: str = "UPSTREAM_REQUIRED"


UPSTREAM_REPOSITORIES = [
    RepoSpec("boostprompt", "https://github.com/AirtonLira/boostprompt.git", "BoostPrompt - Prompt optimization and reinforcement", "UPSTREAM_REQUIRED"),
    RepoSpec("sdlc-agents", "https://github.com/cmwen/sdlc-agents.git", "SDLC Agents - Lightweight role-based SDLC handoffs", "UPSTREAM_REQUIRED"),
    RepoSpec("graphify", "https://github.com/Graphify-Labs/graphify.git", "Graphify - Local AST Knowledge Graph for codebases", "UPSTREAM_REQUIRED"),
    RepoSpec("trace-mcp", "https://github.com/nikolai-vysotskyi/trace-mcp.git", "Trace MCP - Framework-aware blast radius and impact analysis", "UPSTREAM_REQUIRED"),
    RepoSpec("codebase-memory-mcp", "https://github.com/DeusData/codebase-memory-mcp.git", "Codebase Memory MCP - Structural symbol graph", "UPSTREAM_REQUIRED"),
    RepoSpec("chunkhound", "https://github.com/chunkhound/chunkhound.git", "ChunkHound - Context-aware semantic cAST chunking", "UPSTREAM_REQUIRED"),
    RepoSpec("repowise", "https://github.com/repowise-dev/repowise.git", "Repowise - Code health biomarkers and complexity analysis", "UPSTREAM_REQUIRED"),
]


def validate_vendor_root(vendor_root: Path) -> Path:
    """Valida se o caminho de vendor é seguro e não aninhado.

    Enforce canonical root e impede caminhos aninhados sob integrations/vendor
    ou caminhos com múltiplos segmentos 'vendor'.
    """
    resolved = vendor_root.resolve()
    canonical_resolved = CANONICAL_VENDOR_DIR.resolve()

    # Proíbe diretórios aninhados sob o canonical vendor
    if canonical_resolved in resolved.parents:
        raise ValueError(
            f"Nested vendor path not allowed: '{resolved}' cannot be nested inside '{canonical_resolved}'"
        )

    # Proíbe múltiplos segmentos 'vendor' no caminho
    vendor_segments = [p.lower() for p in resolved.parts if p.lower() == "vendor"]
    if len(vendor_segments) > 1:
        raise ValueError(f"Nested vendor path not allowed: multiple 'vendor' segments in '{resolved}'")

    return resolved


def normalize_repo_url(url: str) -> str:
    """Normaliza URL do repositório para comparação insensível a protocolo e sufixo .git."""
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    cleaned = cleaned.lower()
    if cleaned.startswith("git@"):
        parts = cleaned[4:].split(":", 1)
        if len(parts) == 2:
            cleaned = f"https://{parts[0]}/{parts[1]}"
    elif cleaned.startswith("ssh://git@"):
        cleaned = "https://" + cleaned[len("ssh://git@"):]
    return cleaned


def clone_or_update(
    repo: RepoSpec,
    vendor_root: Path,
    shallow: bool = True,
) -> tuple[str, bool, str]:
    """Clona ou atualiza um repositório git no diretório de vendor.

    Contrato Seguro:
    - Se ausente: executa git clone <url> <target>.
    - Se existe e tem .git: valida remote origin via git remote get-url origin.
      Em caso de compatibilidade, executa git pull --ff-only.
      Nunca executa git reset --hard, git clean -fd ou delete.
    - Se existe mas não é repositório git: FALHA.
    - Se existe e é repositório git mas origin diverge: FALHA.

    Args:
        repo: Especificação do repositório (nome, url, descrição, classificação).
        vendor_root: Diretório raiz de vendor.
        shallow: Se True, usa --depth 1 para otimizar tempo e espaço de disco.

    Returns:
        tuple[str, bool, str]: (Nome do repo, Sucesso, Mensagem descritiva).
    """
    try:
        validated_root = validate_vendor_root(vendor_root)
    except ValueError as exc:
        return (repo.name, False, f"Diretório vendor inválido: {exc}")

    target_dir = validated_root / repo.name
    validated_root.mkdir(parents=True, exist_ok=True)

    if not target_dir.exists():
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
                err = res.stderr.strip() or res.stdout.strip()
                return (repo.name, False, f"Falha no git clone: {err}")
        except Exception as exc:
            return (repo.name, False, f"Exceção ao clonar: {exc}")

    # Destino existe: valida se é diretório e possui .git
    git_dir = target_dir / ".git"
    if not target_dir.is_dir() or not git_dir.exists():
        return (
            repo.name,
            False,
            f"Destino '{target_dir}' existe mas não é um repositório git válido",
        )

    # Valida remote origin URL
    try:
        remote_res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if remote_res.returncode != 0:
            err = remote_res.stderr.strip() or remote_res.stdout.strip()
            return (
                repo.name,
                False,
                f"Falha ao obter remote origin em '{target_dir}': {err}",
            )
        actual_origin = remote_res.stdout.strip()
        if normalize_repo_url(actual_origin) != normalize_repo_url(repo.url):
            return (
                repo.name,
                False,
                f"Origin mismatch em '{target_dir}': esperado '{repo.url}', encontrado '{actual_origin}'",
            )
    except Exception as exc:
        return (repo.name, False, f"Exceção ao verificar remote origin: {exc}")

    # Atualiza via git pull --ff-only
    try:
        res = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode == 0:
            return (
                repo.name,
                True,
                f"Atualizado com sucesso ({res.stdout.strip() or 'up to date'})",
            )
        else:
            err = res.stderr.strip() or res.stdout.strip()
            return (repo.name, False, f"Falha no git pull: {err}")
    except Exception as exc:
        return (repo.name, False, f"Exceção ao atualizar: {exc}")


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para sincronização dos repositórios de vendor."""
    parser = argparse.ArgumentParser(
        description="Gerenciador de Integrações e Repositórios Upstream"
    )
    parser.add_argument(
        "--vendor-dir",
        default=str(VENDOR_DIR),
        help="Diretório de destino dos clones",
    )
    parser.add_argument(
        "--repo",
        help="Nome de um repositório específico para clonar/atualizar",
    )
    parser.add_argument(
        "--no-shallow",
        action="store_true",
        help="Clonar histórico completo (sem --depth 1)",
    )
    args = parser.parse_args(argv or sys.argv[1:])

    try:
        vendor_path = validate_vendor_root(Path(args.vendor_dir))
    except ValueError as exc:
        print(f"ERRO: Diretório vendor inválido: {exc}", file=sys.stderr)
        logger.error("Diretório vendor inválido: %s", exc)
        return 1

    print(f"Vendor root resolvido: '{vendor_path}'")
    logger.info("Vendor root resolvido: '%s'", vendor_path)

    shallow = not args.no_shallow
    targets = UPSTREAM_REPOSITORIES
    if args.repo:
        targets = [r for r in targets if r.name == args.repo]
        if not targets:
            print(
                f"ERRO: Repositório '{args.repo}' não encontrado no catálogo de integrações."
            )
            return 1

    print(f"Sincronizando {len(targets)} repositórios upstream em '{vendor_path}'...")
    logger.info(
        "Sincronizando %d repositórios upstream em '%s'.", len(targets), vendor_path
    )
    success_count = 0
    failed_repos: list[str] = []

    for spec in targets:
        name, success, msg = clone_or_update(spec, vendor_path, shallow=shallow)
        status_tag = "[OK]" if success else "[FALHA]"
        print(f"  {status_tag} {name} ({spec.classification}): {msg}")
        logger.info("%s %s (%s): %s", status_tag, name, spec.classification, msg)
        if success:
            success_count += 1
        else:
            failed_repos.append(name)

    logger.info(
        "Resumo: %d/%d repositórios sincronizados.", success_count, len(targets)
    )
    if failed_repos or success_count != len(targets) or len(targets) == 0:
        print(
            f"\n[INSTALLATION_FAILED] Falha na sincronização dos seguintes repositórios upstream: {', '.join(failed_repos) if failed_repos else 'nenhum repositório sincronizado'}"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
