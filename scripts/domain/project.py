"""Domain models for Project Binding, Delivery Backend and Local Work Mirror.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional, Set

from .common import BaseDomainModel, ValidationError

FORBIDDEN_LEGACY_TOKENS: Set[str] = {
    "cbvgas",
    "arthemis",
    "deepvision",
    "test_root",
    "test_item",
}


def _check_forbidden_tokens(name: str, value: Optional[str]) -> None:
    """Ensures zero project-specific contamination defaults exist in configurations."""
    if not value:
        return
    lowered = value.lower()
    for token in FORBIDDEN_LEGACY_TOKENS:
        if token in lowered:
            raise ValidationError(
                f"Field '{name}' contains forbidden legacy/product-specific token '{token}': '{value}'"
            )


class DeliveryBackendKind(str, Enum):
    AZURE_DEVOPS = "AZURE_DEVOPS"
    LOCAL_ONLY = "LOCAL_ONLY"
    MOCK = "MOCK"


@dataclass(frozen=True)
class ProjectBinding(BaseDomainModel):
    """Binds a local repository/project to an enterprise delivery infrastructure."""

    project_id: str
    project_root: str
    display_name: str
    delivery_backend_kind: DeliveryBackendKind
    delivery_binding_ref: str
    is_governed: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.project_root or not self.project_root.strip():
            raise ValidationError("project_root must not be empty")
        if not self.display_name or not self.display_name.strip():
            raise ValidationError("display_name must not be empty")
        if not self.delivery_binding_ref or not self.delivery_binding_ref.strip():
            raise ValidationError("delivery_binding_ref must not be empty")

        _check_forbidden_tokens("project_id", self.project_id)
        _check_forbidden_tokens("project_root", self.project_root)
        _check_forbidden_tokens("display_name", self.display_name)
        _check_forbidden_tokens("delivery_binding_ref", self.delivery_binding_ref)


@dataclass(frozen=True)
class AdoBinding(BaseDomainModel):
    """Azure DevOps specific delivery binding parameters.

    In accordance with SEC-R1-01:
    - Zero secrets in plain text: service_hook_secret_ref is a reference, not a secret.
    - Zero legacy product defaults: all fields must be explicitly supplied.
    """

    organization_url: str
    team_project: str
    area_path: str
    iteration_path: str
    assigned_team: str
    repository_name: Optional[str] = None
    service_hook_secret_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.organization_url or not self.organization_url.strip():
            raise ValidationError("organization_url must not be empty")
        if not self.organization_url.startswith("https://"):
            raise ValidationError(
                f"organization_url must use secure HTTPS: '{self.organization_url}'"
            )
        if not self.team_project or not self.team_project.strip():
            raise ValidationError("team_project must not be empty")
        if not self.area_path or not self.area_path.strip():
            raise ValidationError("area_path must not be empty")
        if not self.iteration_path or not self.iteration_path.strip():
            raise ValidationError("iteration_path must not be empty")
        if not self.assigned_team or not self.assigned_team.strip():
            raise ValidationError("assigned_team must not be empty")

        _check_forbidden_tokens("team_project", self.team_project)
        _check_forbidden_tokens("area_path", self.area_path)
        _check_forbidden_tokens("iteration_path", self.iteration_path)
        _check_forbidden_tokens("assigned_team", self.assigned_team)
        if self.repository_name:
            _check_forbidden_tokens("repository_name", self.repository_name)
        if self.service_hook_secret_ref:
            _check_forbidden_tokens("service_hook_secret_ref", self.service_hook_secret_ref)


@dataclass(frozen=True)
class LocalWorkMirror(BaseDomainModel):
    """Workspace filesystem mirror representation.

    Note: Local mirror directories are projection caches and scratchpads.
    They hold zero independent policy authority.
    """

    project_id: str
    root_path: str
    work_item_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.root_path or not self.root_path.strip():
            raise ValidationError("root_path must not be empty")

    def get_work_item_dir(self) -> Path:
        """Returns the localized working directory for a work item."""
        if not self.work_item_id:
            raise ValidationError("work_item_id is required to resolve work item directory")
        return Path(self.root_path) / "work" / self.project_id / self.work_item_id
