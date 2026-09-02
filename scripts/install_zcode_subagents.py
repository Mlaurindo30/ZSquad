"""
O que é: CLI de instalação e verificação dos Subagents do piloto ZCode.
Responsabilidade: resolver escopo, chamar o adaptador e imprimir resultado reproduzível.
Pra que serve: criar cinco perfis User ou Project com sync idempotente e rollback seletivo.
Comportamento em falha: retorna código 1 e não sobrescreve perfil não gerenciado.
Conexões: integrations/zcode_subagents.py e config/zcode-pilot-agents.yaml.
Dependências & Imports: argparse, json, pathlib e o adaptador local.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from zcode_subagents import ZCodeProfileError, remove_managed_profiles, sync_profiles


def resolve_destination(scope: str, project_root: Path, user_root: Path | None = None) -> Path:
    """Resolve o diretório nativo que o loader do ZCode percorre recursivamente."""
    if scope == "project":
        return project_root.resolve() / ".zcode/agents/agents-squad"
    storage_root = user_root or Path.home() / ".zcode"
    return storage_root.resolve() / "agents/agents-squad"


def build_parser() -> argparse.ArgumentParser:
    """Cria o parser sem executar operações de filesystem."""
    parser = argparse.ArgumentParser(description="Gerencia o piloto Agents Squad no ZCode")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--user-root", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove-managed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Executa sync, check ou remoção seletiva e retorna código compatível com automação."""
    args = build_parser().parse_args(argv)
    destination = resolve_destination(args.scope, args.project_root, args.user_root)
    try:
        if args.remove_managed:
            result = {"removed": remove_managed_profiles(destination, dry_run=args.dry_run)}
        else:
            result = sync_profiles(ROOT, destination, check=args.check, dry_run=args.dry_run)
    except ZCodeProfileError as exc:
        print(f"ZCODE_SUBAGENTS_ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"destination": str(destination), "scope": args.scope, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
