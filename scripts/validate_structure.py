#!/usr/bin/env python3
"""
O que é: Validador de integridade e estrutura do Agents Squad.
Responsabilidade: Verificar arquivos, schemas, registro de agentes, skills e referências proibidas.
Pra que serve: Garantir conformidade estrutural antes de execução ou distribuição.
Comportamento em falha: Imprime erros em stderr e retorna código de saída 1.
Conexões: Utilizado pela suíte de testes, CI/CD e verificação prévia ao bootstrap.
"""
import json
import re
import sys
from pathlib import Path

import yaml

REQUIRED_FILES = [
    "AGENTS.md", "CLAUDE.md", "CODEX.md", "GEMINI.md", "README.md",
    "config/agent-registry.yaml", "config/workflow.yaml", "config/memory.yaml",
    "config/discovery-policy.yaml", "config/skills-catalog.yaml",
    "contracts/handoff.schema.json", "contracts/gate-decision.schema.json",
    "contracts/memory-delta.schema.json", "contracts/work-item.schema.json",
    "docs/01-proposta-arquitetura.md", "docs/02-metodologia-e-fluxo.md",
    "docs/03-catalogo-de-agentes.md", "docs/04-catalogo-de-skills.md",
    "docs/05-memoria-handoffs-e-specs.md", "docs/06-roadmap-de-implantacao.md",
    "docs/07-operacao-e-integracoes.md", "docs/08-padroes-de-codigo.md",
    "docs/09-governed-vibe-coding-framework.md", "work/README.md",
]


def _load_yaml(path: Path, errors: list[str]):
    """Carrega YAML e registra uma mensagem de validação em caso de falha."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid yaml {path}: {exc}")
        return None


def _validate_required_files(root: Path, errors: list[str]) -> None:
    """Valida a presença dos arquivos estruturais obrigatórios."""
    for relative in REQUIRED_FILES:
        if not (root / relative).exists():
            errors.append(f"missing {relative}")


def _validate_agents(root: Path, errors: list[str]) -> list[dict]:
    """Valida registro, prompts e manifestos e retorna os agentes registrados."""
    registry = _load_yaml(root / "config/agent-registry.yaml", errors) or {}
    agents = registry.get("agents", [])
    ids = [entry.get("id") for entry in agents]
    if len(agents) != 41:
        errors.append(f"expected 41 agents, found {len(agents)}")
    if len(ids) != len(set(ids)):
        errors.append("duplicate agent ids")
    for agent in agents:
        agent_id = agent.get("id", "<missing>")
        prompt = root / str(agent.get("path", "")) / "PROMPT.md"
        manifest = root / str(agent.get("manifest", ""))
        if not prompt.exists():
            errors.append(f"missing prompt for {agent_id}")
        if not manifest.exists():
            errors.append(f"missing manifest for {agent_id}")
    return agents


def _validate_schemas(root: Path, errors: list[str]) -> None:
    """Valida sintaxe e metadados mínimos dos JSON Schemas."""
    for schema_path in (root / "contracts").glob("*.schema.json"):
        try:
            data = json.loads(schema_path.read_text(encoding="utf-8"))
            if "$schema" not in data or "title" not in data:
                errors.append(f"schema incomplete {schema_path.name}")
        except Exception as exc:
            errors.append(f"invalid schema {schema_path.name}: {exc}")


def _catalog_entries(catalog: dict) -> dict[str, dict]:
    """Normaliza o catálogo de skills para um mapa indexado por caminho."""
    raw_catalog = catalog.get("catalog", {})
    if isinstance(raw_catalog, dict):
        return {
            path: ({"path": path, **entry} if isinstance(entry, dict) else {"path": path})
            for path, entry in raw_catalog.items()
        }
    return {entry.get("path"): entry for entry in raw_catalog}


def _active_skill_paths(root: Path) -> set[str]:
    """Retorna caminhos de skills e engines first-party ativos no disco."""
    paths = {
        path.parent.relative_to(root).as_posix()
        for path in (root / "skills").rglob("SKILL.md")
        if "/discovery/intake/" not in path.as_posix()
        and "/discovery/quarantine/" not in path.as_posix()
        and "/vendor/" not in path.as_posix()
    }
    integrations_root = root / "integrations"
    paths.update(
        path.relative_to(root).as_posix()
        for path in integrations_root.glob("*.py")
        if path.name != "__init__.py"
    )
    paths.update(
        path.relative_to(root).as_posix()
        for path in integrations_root.glob("experimental/*.py")
        if path.name != "__init__.py"
    )
    return paths


def _validate_skills(root: Path, agents: list[dict], errors: list[str]) -> int:
    """Valida catálogo, manifests e diretórios das skills ativas."""
    catalog = _load_yaml(root / "config/skills-catalog.yaml", errors) or {}
    catalog_by_path = _catalog_entries(catalog)
    for agent in agents:
        manifest = root / str(agent.get("manifest", ""))
        if not manifest.exists():
            continue
        data = _load_yaml(manifest, errors) or {}
        agent_id = agent.get("id", "<missing>")
        for bucket in ("native", "assigned"):
            for skill in data.get(bucket, []):
                skill_path = skill.get("path")
                if bucket == "assigned" and skill_path not in catalog_by_path:
                    errors.append(f"uncatalogued skill {skill_path} in {agent_id}")
                if not (root / str(skill_path)).exists():
                    errors.append(f"missing skill path {skill_path}")
        for skill in data.get("assigned", []):
            entry = catalog_by_path.get(skill.get("path"), {})
            if agent_id not in entry.get("assigned_to", []):
                errors.append(f"catalog assignment mismatch {skill.get('path')} -> {agent_id}")
    active_paths = _active_skill_paths(root)
    for path in sorted(active_paths - set(catalog_by_path)):
        errors.append(f"active skill not catalogued {path}")
    for path in sorted(set(catalog_by_path) - active_paths):
        errors.append(f"catalogued skill missing on disk {path}")
    return len(active_paths)


def _validate_legacy_references(root: Path, errors: list[str]) -> None:
    """Rejeita referências antigas ou permissivas nos artefatos canônicos."""
    for path in list((root / "agents").rglob("*.md")) + list((root / "config").rglob("*.yaml")):
        relative = path.relative_to(root).as_posix()
        if relative == "config/skills-catalog.yaml":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"skills/legacy/|legacy/|pode criar novas skills livremente|sem aprovação", text, re.I):
            errors.append(f"legacy or permissive reference in {relative}")


PROMPT_FILES = ("AGENTS.md", "CLAUDE.md", "CODEX.md", "GEMINI.md")
PROMPT_CHARACTER_BUDGETS = {
    "AGENTS.md": 12_000,
    "CLAUDE.md": 40_000,
    "CODEX.md": 32 * 1024,
    "GEMINI.md": 12_000,
}
COPY_MODEL_PATTERNS = (
    "copiar o squad inteiro",
    "copie o squad inteiro",
    "copiar o runtime inteiro",
    "copie o runtime inteiro",
    "cópia integral do squad",
    "cópia integral do runtime",
    "copy the entire squad",
    "copy the full runtime",
)


def _validate_prompt_copy_model(root: Path, errors: list[str]) -> None:
    """Rejeita prompts que voltem a prescrever cópia integral do runtime compartilhado."""
    for name in PROMPT_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        matched = [pattern for pattern in COPY_MODEL_PATTERNS if pattern in text]
        if matched:
            errors.append(f"legacy copy-model reference in {name}: {matched[0]}")


def _validate_prompt_budgets(root: Path, errors: list[str]) -> None:
    """Mantém cada prompt dentro do limite de caracteres documentado pelo provider."""
    for name, budget in PROMPT_CHARACTER_BUDGETS.items():
        path = root / name
        if not path.is_file():
            continue
        character_count = len(path.read_text(encoding="utf-8", errors="ignore"))
        if character_count > budget:
            errors.append(f"{name} exceeds {budget} character budget: {character_count}")


def main() -> int:
    """Executa a validação estrutural completa da raiz do squad."""
    root = Path(__file__).resolve().parent.parent
    errors: list[str] = []
    _validate_required_files(root, errors)
    agents = _validate_agents(root, errors)
    _validate_schemas(root, errors)
    active_skills = _validate_skills(root, agents, errors)
    _validate_legacy_references(root, errors)
    _validate_prompt_copy_model(root, errors)
    _validate_prompt_budgets(root, errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    schema_count = len(list((root / "contracts").glob("*.schema.json")))
    print(f"VALID structure agents={len(agents)} active_skills={active_skills} schemas={schema_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
