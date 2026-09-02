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

from bdd_validator import validate_features


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


def run_bdd_evaluation(work_item: Path) -> dict[str, Any]:
    """Executa a validação das especificações BDD e grava a evidência oficial em evaluation/bdd.json."""
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
    evidence_path = eval_dir / "bdd.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Executor de Validação e Evidência BDD")
    parser.add_argument("--work-item", required=True, help="Caminho ou ID do work item")
    args = parser.parse_args(argv)

    work_item = Path(args.work_item)
    if not work_item.is_absolute():
        work_item = (ROOT / "work" / args.work_item).resolve()

    if not work_item.is_dir():
        print(f"Erro: Work item directory not found: {work_item}", file=sys.stderr)
        return 1

    evidence = run_bdd_evaluation(work_item)
    print(f"Evidência BDD gerada em: {work_item}/evaluation/bdd.json (passed={evidence['passed']})")
    return evidence["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
