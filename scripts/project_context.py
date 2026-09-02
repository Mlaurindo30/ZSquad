"""
O que é: resolvedor do contexto entre um projeto consumidor e o runtime compartilhado.
Responsabilidade: validar o marcador mínimo .agents_squad e produzir caminhos centrais namespaced.
Pra que serve: permitir que vários providers e projetos usem um único runtime sem copiá-lo.
Comportamento em falha: rejeita marcador ausente, inválido ou runtime incompatível.
Conexões: bootstrap_project_squad.py, agent_squad.py e prompts dos providers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProjectContextError(ValueError):
    """Indica que o vínculo projeto-runtime não pode ser validado."""


@dataclass(frozen=True)
class ProjectContext:
    """Contexto validado de um projeto consumidor do runtime central."""

    runtime_root: Path
    project_root: Path
    project_id: str

    @property
    def work_dir(self) -> Path:
        return self.runtime_root / "work" / self.project_id

    @property
    def db_path(self) -> Path:
        return self.runtime_root / "banco" / "squad.db"


def validate_project_id(project_id: str) -> str:
    """Valida o identificador usado como namespace central."""
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectContextError(f"project_id inválido: {project_id!r}")
    return project_id


def find_project_root(start: Path) -> Path | None:
    """Procura o marcador de projeto no diretório inicial e ancestrais."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".agents_squad" / "config" / "project.yaml").is_file():
            return candidate
    return None


def load_project_context(project_root: Path) -> ProjectContext:
    """Carrega e valida o marcador mínimo criado pelo bootstrap."""
    project_root = project_root.resolve()
    marker = project_root / ".agents_squad" / "config" / "project.yaml"
    try:
        payload: Any = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectContextError(f"marcador de projeto inválido: {marker}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectContextError(f"marcador de projeto inválido: {marker}")

    project_id = validate_project_id(str(payload.get("project_id") or payload.get("project_name") or ""))
    runtime_value = payload.get("runtime")
    recorded_root = payload.get("project_root")
    if not isinstance(runtime_value, str) or not runtime_value:
        raise ProjectContextError("runtime ausente no marcador de projeto")
    if recorded_root and Path(str(recorded_root)).resolve() != project_root:
        raise ProjectContextError("project_root do marcador não corresponde ao projeto atual")

    runtime_root = Path(runtime_value).resolve()
    required = (runtime_root / "scripts", runtime_root / "agents", runtime_root / "contracts")
    if not runtime_root.is_dir() or not all(path.is_dir() for path in required):
        raise ProjectContextError(f"runtime compartilhado inválido: {runtime_root}")
    return ProjectContext(runtime_root, project_root, project_id)


def resolve_project_context(start: Path, explicit_project_root: Path | None = None) -> ProjectContext:
    """Resolve contexto a partir de uma raiz explícita ou do diretório atual."""
    root = explicit_project_root.resolve() if explicit_project_root else find_project_root(start)
    if root is None:
        raise ProjectContextError("marcador .agents_squad/config/project.yaml não encontrado")
    return load_project_context(root)
