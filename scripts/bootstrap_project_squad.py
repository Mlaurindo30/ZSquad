#!/usr/bin/env python3
"""
O que é: utilitário de vínculo entre um projeto consumidor e o runtime central do squad.
Responsabilidade: criar apenas o marcador local, sem copiar runtime, work items ou banco.
Pra que serve: permitir que vários providers e projetos compartilhem uma única instalação.
Comportamento em falha: não sobrescreve marcador existente e não remove dados legados.
Conexões: project_context.py, agent_squad.py e prompts dos providers.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

import yaml

from project_context import ProjectContextError, load_project_context, validate_project_id


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _legacy_paths(destination: Path) -> list[Path]:
    return [path for path in (destination / "work", destination / "banco") if path.exists()]


def bootstrap(
    target_root: Path,
    runtime_root: Path,
    project_name: str | None = None,
    force: bool = False,
) -> Path:
    """Cria o marcador mínimo que referencia o runtime compartilhado."""
    target_root = target_root.resolve()
    runtime_root = runtime_root.resolve()
    project_id = validate_project_id(project_name or target_root.name)
    destination = target_root / ".agents_squad"
    config = destination / "config"
    marker = config / "project.yaml"
    provenance = destination / "PROVENANCE.yaml"

    if not runtime_root.is_dir():
        raise SystemExit(f"runtime inexistente: {runtime_root}")
    legacy = _legacy_paths(destination)
    if legacy:
        joined = ", ".join(path.as_posix() for path in legacy)
        raise SystemExit(f"dados locais legados detectados; migre antes do bootstrap: {joined}")
    if destination.exists() and not force:
        raise SystemExit(f"marcador já existe em {destination} — use --force para atualizar")

    config.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "runtime": runtime_root.as_posix(),
        "project_id": project_id,
        "project_name": project_id,
        "project_root": target_root.as_posix(),
        "work_dir": (runtime_root / "work" / project_id).as_posix(),
        "db_path": (runtime_root / "banco" / "squad.db").as_posix(),
        "overrides": {},
    }
    marker.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    provenance.write_text(
        yaml.safe_dump(
            {
                "runtime": runtime_root.as_posix(),
                "project_id": project_id,
                "project_root": target_root.as_posix(),
                "bootstrapped_at": _now(),
                "model": "shared-runtime-pointer",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    agents_md = target_root / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(
            "<!-- managed-by: agents-squad-bootstrap -->\n"
            "# Agents Squad\n\n"
            f"This project is linked to the shared Agents Squad runtime: {runtime_root.as_posix()}\n\n"
            f"Load and follow {runtime_root.as_posix()}/AGENTS.md before any work. "
            "You are the `delivery-orchestrator` (`00`); assume this role at session start. "
            "Classify type/risk/domains, pick Consult/Light/Full, and never hand-scaffold a work item — "
            "use `python <SQUAD_RUNTIME>/scripts/agent_squad.py`. "
            "Identify the request's function and follow its work cycle from "
            f"{runtime_root.as_posix()}/config/cycles.yaml.\n",
            encoding="utf-8",
        )
    return destination


def check(target_root: Path) -> int:
    """Valida o marcador, o runtime e a ausência de dados locais contraditórios."""
    destination = target_root.resolve() / ".agents_squad"
    if not destination.is_dir():
        print(f"AUSENTE: {destination}")
        return 1
    legacy = _legacy_paths(destination)
    if legacy:
        print("INVÁLIDO: dados locais legados: " + ", ".join(path.as_posix() for path in legacy))
        return 1
    try:
        context = load_project_context(target_root)
    except ProjectContextError as exc:
        print(f"INVÁLIDO: {exc}")
        return 1
    print(
        f"PRESENTE: {destination}\n"
        f"runtime: {context.runtime_root.as_posix()}\n"
        f"project_id: {context.project_id}\n"
        f"work_dir: {context.work_dir.as_posix()}\n"
        f"db_path: {context.db_path.as_posix()}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Executa bootstrap mínimo ou verificação do vínculo compartilhado."""
    parser = argparse.ArgumentParser(description="Cria o marcador mínimo para o runtime compartilhado.")
    parser.add_argument("--target", default=".", help="raiz do projeto consumidor")
    parser.add_argument("--runtime", type=Path, default=None, help="raiz central do runtime")
    parser.add_argument("--project-name", type=str, default=None, help="identificador central do projeto")
    parser.add_argument("--force", action="store_true", help="atualiza somente o marcador existente")
    parser.add_argument("--check", action="store_true", help="valida o marcador sem alterar arquivos")
    args = parser.parse_args(argv)

    target_root = Path(args.target).resolve()
    if not target_root.is_dir():
        raise SystemExit(f"alvo inexistente: {target_root}")
    if args.check:
        return check(target_root)

    runtime = args.runtime.resolve() if args.runtime else Path(__file__).resolve().parents[1]
    destination = bootstrap(target_root, runtime, args.project_name, args.force)
    print(
        f"PROJECT_LINKED {destination} runtime={runtime.as_posix()} "
        f"project_id={args.project_name or target_root.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
