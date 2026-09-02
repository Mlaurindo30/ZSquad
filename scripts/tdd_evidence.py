"""
O que é: validador de evidência do ciclo TDD Red-Green-Refactor.
Responsabilidade: comprovar ordem, resultados, identidade do teste e encadeamento de hashes.
Pra que serve: bloquear gates quando TDD foi apenas declarado, não executado.
Comportamento em falha: rejeita estágio ausente, stale, adulterado ou com resultado incompatível.
Conexões: quality_gate_runner.py, work/<ID>/evaluation e gates G4/G5.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

STAGES = ("red", "green", "refactor")


def evidence_digest(evidence: dict[str, Any]) -> str:
    """Calcula digest determinístico de uma evidência."""
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_tdd_cycle(directory: Path, current_hashes: dict[str, str] | None = None) -> dict[str, Any]:
    """Valida os três estágios TDD e retorna resumo fail-closed."""
    evidence = {
        stage: json.loads((directory / f"{stage}.json").read_text(encoding="utf-8"))
        for stage in STAGES
    }
    red, green, refactor = (evidence[stage] for stage in STAGES)
    errors: list[str] = []
    if red.get("passed") is not False or red.get("exit_code") == 0:
        errors.append("RED deve falhar")
    if green.get("passed") is not True or green.get("exit_code") != 0:
        errors.append("GREEN deve passar")
    if refactor.get("passed") is not True or refactor.get("exit_code") != 0:
        errors.append("REFACTOR deve passar")
    identities = {(item.get("results") or {}).get("test_id") for item in evidence.values()}
    criteria = {(item.get("results") or {}).get("criterion_id") for item in evidence.values()}
    if None in identities or len(identities) != 1:
        errors.append("test_id deve ser único e igual nos três estágios")
    if None in criteria or len(criteria) != 1:
        errors.append("criterion_id deve ser único e igual nos três estágios")
    if (green.get("results") or {}).get("previous_digest") != evidence_digest(red):
        errors.append("GREEN não referencia o digest de RED")
    if (refactor.get("results") or {}).get("previous_digest") != evidence_digest(green):
        errors.append("REFACTOR não referencia o digest de GREEN")
    if current_hashes is not None and refactor.get("file_hashes") != current_hashes:
        errors.append("evidência REFACTOR está desatualizada")
    return {"approved": not errors, "errors": errors, "test_id": next(iter(identities - {None}), None)}
