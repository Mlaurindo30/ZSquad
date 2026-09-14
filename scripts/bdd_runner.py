#!/usr/bin/env python3
"""
O que é: Executor e gerador determinístico de evidências BDD para o Agents Squad.
Responsabilidade: Validar especificações Gherkin em specs/ e emitir a evidência canônica evaluation/bdd.json.
Pra que serve: Permitir que o Gate G5 (acceptance-bdd-executed) seja aprovado com base em evidência criptográfica válida.
Comportamento em falha: Retorna exit code 1 e gera evidência com passed=False em caso de features inválidas.
Conexões: Utiliza bdd_validator.py e verification-evidence.schema.json; consumido por gate_validators.py.
Dependências & Imports:
  - json, hashlib, time, datetime: Utilitários padrão.
  - pathlib, sys, argparse: Utilitários de sistema e CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from bdd_validator import validate_features  # noqa: E402
from project_context import ProjectContext  # noqa: E402


class WorkItemResolutionError(ValueError):
    """Indica uma referência de work item inválida ou fora do runtime."""


def _is_within(path: Path, root: Path) -> bool:
    """Retorna se ``path`` está contido em ``root`` depois de resolver links."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validated_item(candidate: Path, allowed_root: Path) -> Path:
    """Confere contenção, diretório e marcador antes de aceitar um candidato."""
    resolved = candidate.resolve()
    if not _is_within(resolved, allowed_root):
        raise WorkItemResolutionError("referência fora da raiz de work autorizada")
    if not resolved.is_dir() or not (resolved / "status.yaml").is_file():
        raise WorkItemResolutionError("work item ausente ou sem status.yaml")
    return resolved


def resolve_work_item_reference(
    raw: str, context: ProjectContext, *, legacy_root: Path | None = None
) -> Path:
    """Resolve uma referência de work item sem permitir traversal ou escapes.

    IDs simples priorizam o namespace do projeto. A forma explícita ``work/<id>``
    é reservada para itens legados existentes sob ``runtime/work``.
    """
    value = raw.strip()
    if not value:
        raise WorkItemResolutionError("referência vazia")

    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise WorkItemResolutionError("referência relativa inválida")

    windows_drive_relative = len(value) >= 2 and value[1] == ":" and not Path(value).is_absolute()
    rooted_without_drive = value.startswith(("/", "\\"))
    if windows_drive_relative or rooted_without_drive and not Path(value).is_absolute():
        raise WorkItemResolutionError("referência de drive ou raiz inválida")

    runtime_work = (context.runtime_root / "work").resolve()
    namespaced_root = context.work_dir.resolve()
    selected_legacy = (legacy_root or runtime_work).resolve()
    if not _is_within(selected_legacy, runtime_work):
        raise WorkItemResolutionError("raiz legada fora de runtime/work")

    raw_path = Path(value)
    if raw_path.is_absolute():
        return _validated_item(raw_path, namespaced_root)

    if parts[0] == "work":
        if len(parts) == 3 and parts[1] == context.project_id:
            return _validated_item(runtime_work.joinpath(*parts[1:]), namespaced_root)
        if len(parts) == 2:
            return _validated_item(selected_legacy / parts[1], selected_legacy)
        raise WorkItemResolutionError("referência work inválida")

    if len(parts) == 2 and parts[0] == context.project_id:
        return _validated_item(namespaced_root / parts[1], namespaced_root)
    if len(parts) != 1:
        raise WorkItemResolutionError("referência relativa inválida")

    try:
        return _validated_item(namespaced_root / parts[0], namespaced_root)
    except WorkItemResolutionError:
        return _validated_item(selected_legacy / parts[0], selected_legacy)


def now_iso() -> str:
    """Retorna timestamp UTC atual no formato ISO 8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_file_hashes(work_item: Path, feature_files: list[Path]) -> dict[str, str]:
    """Calcula o SHA-256 de todos os arquivos de especificação analisados."""
    hashes: dict[str, str] = {}
    for path in feature_files:
        try:
            rel = str(path.relative_to(work_item)).replace("\\", "/")
        except ValueError:
            rel = path.name
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def run_bdd_evaluation(work_item: Path, *, allowed_root: Path | None = None) -> dict[str, Any]:
    """Executa a validação das especificações BDD e grava a evidência oficial em evaluation/bdd.json."""
    work_item = work_item.resolve()
    if allowed_root is not None:
        _validated_item(work_item, allowed_root)
    specs_dir = work_item / "specs"
    if not specs_dir.exists():
        specs_dir = work_item / "discovery"
    
    feature_files = sorted(specs_dir.rglob("*.feature")) if specs_dir.exists() else []
    
    if not feature_files:
        val_result = {"approved": False, "errors": ["Nenhum arquivo .feature encontrado em specs/ ou discovery/"]}
    else:
        val_result = validate_features(specs_dir)

    passed = bool(val_result.get("approved", False))
    exit_code = 0 if passed else 1
    
    file_hashes = generate_file_hashes(work_item, feature_files)
    if not file_hashes and (work_item / "status.yaml").is_file():
        file_hashes["status.yaml"] = hashlib.sha256((work_item / "status.yaml").read_bytes()).hexdigest()

    evidence = {
        "schema_version": 1,
        "verifier": "bdd",
        "work_item": work_item.name,
        "passed": passed,
        "exit_code": exit_code,
        "stdout": json.dumps(val_result, indent=2, ensure_ascii=False),
        "stderr": "\n".join(val_result.get("errors", [])),
        "command": f"bdd_runner.py --work-item {work_item.name}",
        "file_hashes": file_hashes,
        "timestamp": now_iso(),
    }

    eval_dir = work_item / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    if allowed_root is not None and not _is_within(eval_dir, allowed_root):
        raise WorkItemResolutionError("diretório de evidência fora da raiz autorizada")
    evidence_path = eval_dir / "bdd.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Executor de Validação e Evidência BDD")
    parser.add_argument("--work-item", required=True, help="Caminho ou ID do work item")
    args = parser.parse_args(argv)

    context = ProjectContext(ROOT.resolve(), ROOT.resolve(), "agent_squad")
    try:
        work_item = resolve_work_item_reference(args.work_item, context)
        evidence = run_bdd_evaluation(work_item, allowed_root=context.work_dir)
    except (WorkItemResolutionError, OSError, RuntimeError, ValueError) as exc:
        print(f"Erro: work item inválido: {exc}", file=sys.stderr)
        return 1

    print(f"Evidência BDD gerada em: {work_item}/evaluation/bdd.json (passed={evidence['passed']})")
    return evidence["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
