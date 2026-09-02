"""
O que é: CLI de instalação, migração e verificação dos 36 Subagents nativos ZCode.
Responsabilidade: resolver escopo, encaminhar configuração host-only e emitir evidência JSON reproduzível.
Pra que serve: gerenciar perfis User ou Project com sync idempotente, backup externo e rollback seletivo.
Comportamento em falha: retorna código 1, não sobrescreve perfil alheio e não afirma execução pelo host.
Conexões: integrations/experimental/zcode_subagents.py e config/zcode-agents.yaml.
Dependências & Imports: argparse, json, pathlib e o adaptador local.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from zcode_subagents import ZCodeProfileError, remove_managed_profiles, sync_profiles

DEFAULT_CONFIG = ROOT / "config/zcode-agents.yaml"


def resolve_destination(scope: str, project_root: Path, user_root: Path | None = None) -> Path:
    """Resolve diretório oficial de agentes no escopo User ou Project sem criá-lo."""
    if scope == "user":
        base = (user_root or Path.home() / ".zcode").expanduser().absolute()
        return base / "agents/agents-squad"
    return project_root.expanduser().absolute() / ".zcode/agents/agents-squad"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Manage the exact 36 Agents Squad native ZCode profiles")
    value.add_argument("--scope", choices=("user", "project"), default="user")
    value.add_argument("--project-root", type=Path, default=ROOT)
    value.add_argument("--user-root", type=Path)
    value.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    value.add_argument("--check", action="store_true")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--remove-managed", action="store_true")
    value.add_argument("--migrate", action="store_true", help="Back up an existing managed installation before expanding it")
    value.add_argument("--backup-root", type=Path, help="External directory for migration backups")
    value.add_argument("--authorization", type=Path, help="Approval record required for real mutations")
    return value


def _require_authorization(path: Path | None, scope: str, destination: Path, operation: str) -> None:
    """Exige registro explícito, vigente e vinculado ao alvo para qualquer mutação real."""
    if path is None:
        raise ZCodeProfileError("missing mutation authorization")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ZCodeProfileError(f"invalid mutation authorization: {exc}") from exc
    if not isinstance(record, dict):
        raise ZCodeProfileError("invalid mutation authorization")
    expected = {
        "schema_version": 1,
        "work_item": "EVOL-ZCODE-ALL-AGENTS-20260824",
        "scope": scope,
        "destination": str(destination),
        "operation": operation,
    }
    missing = [name for name, value in expected.items() if record.get(name) != value]
    approvals = record.get("approvals")
    required_approvals = {
        "G1": "product-owner",
        "G2": "solution-architect",
        "G3": "delivery-orchestrator",
        "G4": "independent-reviewer",
        "G5": "qa-engineer",
        "human_authorization": "human-authorizer",
    }
    if not isinstance(approvals, dict):
        missing.extend(required_approvals)
    else:
        missing.extend(
            name for name, role in required_approvals.items()
            if not isinstance(approvals.get(name), dict)
            or approvals[name].get("approved") is not True
            or approvals[name].get("role") != role
            or not isinstance(approvals[name].get("authorizer"), str)
            or not approvals[name]["authorizer"]
        )
    if record.get("rollback_verified") is not True:
        missing.append("rollback_verified")
    expires_at = record.get("expires_at")
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if isinstance(expires_at, str) else None
    except ValueError:
        expires = None
    if expires is None or expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        missing.append("unexpired expires_at")
    if missing:
        raise ZCodeProfileError(f"mutation authorization missing or invalid: {', '.join(dict.fromkeys(missing))}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    destination = resolve_destination(args.scope, args.project_root, args.user_root)
    try:
        mutates = args.remove_managed or (not args.check and not args.dry_run)
        operation = "remove-managed" if args.remove_managed else "migrate" if args.migrate else "sync"
        if mutates:
            _require_authorization(args.authorization, args.scope, destination, operation)
        if args.remove_managed:
            if args.check or args.migrate or args.backup_root is not None:
                raise ZCodeProfileError("--remove-managed cannot be combined with --check or migration options")
            result: dict[str, object] = {
                "removed": remove_managed_profiles(destination, dry_run=args.dry_run),
                "host_execution_verified": False,
            }
        else:
            if args.backup_root is not None and not args.migrate:
                raise ZCodeProfileError("--backup-root requires --migrate")
            result = sync_profiles(
                ROOT,
                destination,
                config_path=args.config,
                check=args.check,
                dry_run=args.dry_run,
                migrate=args.migrate,
                backup_root=args.backup_root,
            )
        result = {"destination": str(destination), **result}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ZCodeProfileError, OSError, UnicodeError) as exc:
        print(f"ZCODE_SUBAGENTS_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

