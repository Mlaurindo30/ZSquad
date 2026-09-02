"""
O que é: validador estrutural de especificações BDD em Gherkin.
Responsabilidade: exigir Feature, Scenario e passos Given/When/Then vinculados a critérios.
Pra que serve: impedir avanço para build com BDD vazio ou apenas declarativo.
Comportamento em falha: retorna erros explícitos e aprovação falsa.
Conexões: specs/features dos work items, pytest-bdd e gates G1/G3/G5.
"""
from __future__ import annotations

import re
from pathlib import Path


def validate_feature(path: Path) -> dict[str, object]:
    """Valida estrutura mínima e vínculo de critérios de um arquivo feature."""
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not re.search(r"(?mi)^\s*(Feature|Funcionalidade):\s*\S", text):
        errors.append("Feature ausente")
    matches = list(re.finditer(
        r"(?mi)^(?:\s*@[^\n]+\n)*\s*(?:Scenario(?: Outline)?|Cenário|Esquema do Cenário):",
        text,
    ))
    scenarios = [
        text[matches[index - 1].start(): matches[index].start() if index < len(matches) else len(text)]
        for index in range(1, len(matches) + 1)
    ]
    if not scenarios:
        errors.append("Scenario ausente")
    for index, scenario in enumerate(scenarios, 1):
        for keyword, alternatives in {
            "Given": r"(?mi)^\s*(Given|Dado|Dada|Dados|Dadas)\s+",
            "When": r"(?mi)^\s*(When|Quando)\s+",
            "Then": r"(?mi)^\s*(Then|Então|Entao)\s+",
        }.items():
            if not re.search(alternatives, scenario):
                errors.append(f"Scenario {index} sem {keyword}")
        if not re.search(r"(?i)@(AC|CRITERION)[_-][A-Z0-9_-]+", scenario):
            errors.append(f"Scenario {index} sem tag de critério")
    return {"approved": not errors, "errors": errors, "scenario_count": len(scenarios)}


def validate_features(directory: Path) -> dict[str, object]:
    """Valida todos os arquivos .feature de um diretório de especificação."""
    files = sorted(directory.rglob("*.feature")) if directory.is_dir() else ([directory] if directory.is_file() else [])
    if not files:
        return {"approved": False, "errors": ["nenhum arquivo .feature"], "features": {}}
    results = {path.name: validate_feature(path) for path in files}
    errors = [f"{name}: {error}" for name, result in results.items() for error in result["errors"]]
    return {"approved": not errors, "errors": errors, "features": results}
