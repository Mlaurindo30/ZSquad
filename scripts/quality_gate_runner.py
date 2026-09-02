"""
O que é: executor fail-closed de verificações reproduzíveis do Agents Squad.
Responsabilidade: executar comandos sem shell e persistir evidência tipada e atômica.
Pra que serve: substituir declarações textuais de qualidade por resultados verificáveis.
Comportamento em falha: timeout, ferramenta ausente ou saída inválida geram evidência reprovada.
Conexões: contratos/verification-evidence.schema.json, gates G4/G5 e work items.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def hash_files(root: Path, paths: list[str]) -> dict[str, str]:
    """Calcula SHA-256 de arquivos confinados à raiz informada."""
    hashes: dict[str, str] = {}
    root = root.resolve()
    for raw in paths:
        path = (root / raw).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"arquivo fora da raiz: {raw}")
        if not path.is_file():
            raise FileNotFoundError(raw)
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def validate_evidence(root: Path, evidence: dict[str, Any]) -> None:
    """Valida evidência contra o contrato JSON Schema canônico."""
    schema_path = root / "contracts/verification-evidence.schema.json"
    if not schema_path.is_file():
        schema_path = Path(__file__).resolve().parents[1] / "contracts/verification-evidence.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(evidence), key=lambda error: list(error.path))
    if errors:
        raise ValueError("evidência inválida: " + "; ".join(error.message for error in errors))


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    """Persiste evidência com substituição atômica no mesmo filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _coverage_results(root: Path, report: str, minimum: float) -> dict[str, Any]:
    """Lê cobertura JSON e exige percentuais globais e de branches mínimos."""
    report_path = (root / report).resolve()
    if report_path != root and root not in report_path.parents:
        raise ValueError(f"relatório de cobertura fora da raiz: {report}")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    totals = payload["totals"]
    covered_branches = int(totals["covered_branches"])
    num_branches = int(totals["num_branches"])
    if num_branches <= 0:
        raise ValueError("relatório sem branches mensuráveis")
    branch_percent = covered_branches * 100 / num_branches
    total_percent = float(totals["percent_covered"])
    return {
        "status": "PASS" if total_percent >= minimum and branch_percent >= minimum else "FAIL",
        "minimum_percent": minimum,
        "total_percent": round(total_percent, 4),
        "branch_percent": round(branch_percent, 4),
        "covered_branches": covered_branches,
        "num_branches": num_branches,
        "report": report_path.relative_to(root).as_posix(),
    }


def run_verifier(
    root: Path,
    work_item: str,
    verifier: str,
    persona: str,
    command: list[str],
    files: list[str],
    output: Path,
    timeout: int = 300,
    coverage_report: str | None = None,
    coverage_minimum: float = 80.0,
) -> dict[str, Any]:
    """Executa verificador sem shell, captura resultado e sempre grava evidência válida."""
    started = time.monotonic()
    exit_code = 127
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = f"timeout após {timeout}s"
    except OSError as exc:
        stderr = str(exc)
    results: dict[str, Any] = {"status": "PASS" if exit_code == 0 else "FAIL"}
    if coverage_report is not None:
        try:
            results = _coverage_results(root, coverage_report, coverage_minimum)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            results = {"status": "FAIL", "error": str(exc)}
        if results["status"] != "PASS" and exit_code == 0:
            exit_code = 1
            stderr = f"{stderr}\ncobertura abaixo do mínimo ou relatório inválido".strip()
    evidence = {
        "schema_version": 1,
        "work_item": work_item,
        "verifier": verifier,
        "persona": persona,
        "command": command,
        "exit_code": exit_code,
        "passed": exit_code == 0 and results["status"] == "PASS",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 6),
        "file_hashes": hash_files(root, files),
        "results": results,
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
    }
    validate_evidence(root, evidence)
    write_evidence(output, evidence)
    return evidence


def main(argv: list[str] | None = None) -> int:
    """Executa uma verificação configurada pela CLI e retorna o código observado."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--verifier", required=True)
    parser.add_argument("--persona", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--coverage-report")
    parser.add_argument("--coverage-minimum", type=float, default=80.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("um comando é obrigatório")
    evidence = run_verifier(
        args.root.resolve(), args.work_item, args.verifier, args.persona,
        args.command, args.file, args.output, args.timeout,
        args.coverage_report, args.coverage_minimum,
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
