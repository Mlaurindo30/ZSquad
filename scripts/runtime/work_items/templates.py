"""Level-Specific Artifact Materializer and Containment Guard for Agent Squad.

Eliminates artifact leakage (R0-WORK-003) by ensuring that Tasks NEVER receive
Epic or Product Goal artifacts, and materializes authorized templates per level.
Strictly stdlib-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind
from .ids import CanonicalIdService


class ArtifactContainmentViolation(RuntimeError):
    """Raised when an unauthorized artifact is materialized into a work item folder."""
    pass


PROHIBITED_ARTIFACTS: Dict[WorkItemKind, Set[str]] = {
    WorkItemKind.TASK: {
        "epic.md",
        "product-goal.md",
        "architecture-vision.md",
        "backlog.md",
        "feature-spec.md",
        "component-design.md",
    },
    WorkItemKind.STORY: {
        "epic.md",
        "product-goal.md",
        "architecture-vision.md",
        "backlog.md",
    },
    WorkItemKind.FEATURE: {
        "epic.md",
        "product-goal.md",
        "task-scope.md",
    },
    WorkItemKind.EPIC: {
        "task-scope.md",
        "acceptance-criteria.md",
    },
    WorkItemKind.BUG: {
        "epic.md",
        "product-goal.md",
        "architecture-vision.md",
    },
    WorkItemKind.SPIKE: {
        "epic.md",
        "product-goal.md",
    },
}

REQUIRED_ARTIFACTS: Dict[WorkItemKind, List[str]] = {
    WorkItemKind.EPIC: [
        "epic.md",
        "product-goal.md",
        "architecture-vision.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.FEATURE: [
        "feature-spec.md",
        "component-design.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.STORY: [
        "user-story.md",
        "acceptance-criteria.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.TASK: [
        "task-scope.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.BUG: [
        "bug-report.md",
        "reproduction-steps.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.SPIKE: [
        "spike-report.md",
        "findings.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.INCIDENT: [
        "incident-report.md",
        "post-mortem.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.RELEASE: [
        "release-notes.md",
        "deployment-plan.md",
        "documentation/delivery-ledger.md",
    ],
    WorkItemKind.PROJECT_SETUP: [
        "project-setup.md",
        "documentation/delivery-ledger.md",
    ],
}


def _resolve_kind(kind: Union[WorkItemKind, str]) -> WorkItemKind:
    if isinstance(kind, WorkItemKind):
        return kind
    raw = kind.upper().strip()
    if hasattr(WorkItemKind, raw):
        return getattr(WorkItemKind, raw)
    return CanonicalIdService.infer_kind(kind)


class ArtifactMaterializer:
    """Materializes level-specific artifacts governed by work item kind."""

    def __init__(self, templates_dir: Path):
        self.templates_dir = Path(templates_dir).resolve()

    def get_required_artifacts(self, kind: Union[WorkItemKind, str]) -> List[str]:
        k = _resolve_kind(kind)
        return REQUIRED_ARTIFACTS.get(k, ["scope.md"])

    def get_prohibited_artifacts(self, kind: Union[WorkItemKind, str]) -> Set[str]:
        k = _resolve_kind(kind)
        return PROHIBITED_ARTIFACTS.get(k, set())

    def validate_artifact_allowed(self, kind: Union[WorkItemKind, str], artifact_rel_path: str) -> None:
        """Validates that an artifact is permitted for the given kind, raising on violation."""
        k = _resolve_kind(kind)
        file_name = Path(artifact_rel_path).name
        prohibited = self.get_prohibited_artifacts(k)
        if file_name in prohibited:
            raise ArtifactContainmentViolation(
                f"ArtifactContainmentViolation: '{file_name}' is strictly prohibited for {k.value} items. "
                f"Prohibited artifacts: {sorted(list(prohibited))}"
            )

    def _read_template_content(self, template_name: str, fallback_title: str) -> str:
        """Reads template from templates directory, with graceful fallback."""
        template_file = self.templates_dir / template_name
        if template_file.is_file():
            return template_file.read_text(encoding="utf-8")
        # Direct fallback for standard names
        return f"# {fallback_title}\n\n<!-- Conteúdo inicial do artefato {template_name} -->\n"

    def materialize_artifacts(
        self,
        target_dir: Path,
        kind: Union[WorkItemKind, str],
        work_item_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Instantiates authorized level-specific artifacts inside target_dir.

        Strictly enforces that no prohibited artifacts are created.
        """
        k = _resolve_kind(kind)
        meta = metadata or {}
        title = meta.get("title", work_item_id)
        parent_id = meta.get("parent_id", "")
        created_files: List[str] = []

        required = self.get_required_artifacts(k)
        for rel_name in required:
            self.validate_artifact_allowed(k, rel_name)

            dest_path = (target_dir / rel_name).resolve()
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            tpl_file_name = Path(rel_name).name
            template_content = self._read_template_content(tpl_file_name, f"{work_item_id} — {title}")

            rendered = (
                template_content
                .replace("<WORK-ID>", work_item_id)
                .replace("<EPIC-ID>", work_item_id if k == WorkItemKind.EPIC else str(parent_id))
                .replace("<FEATURE-ID>", work_item_id if k == WorkItemKind.FEATURE else str(parent_id))
                .replace("<STORY-ID>", work_item_id if k == WorkItemKind.STORY else str(parent_id))
                .replace("<TASK-ID>", work_item_id)
                .replace("<Title>", title)
                .replace("<PARENT-FEATURE-ID>", str(parent_id))
                .replace("<PARENT-STORY-ID>", str(parent_id))
                .replace("<PARENT-EPIC-ID>", str(parent_id))
            )

            dest_path.write_text(rendered, encoding="utf-8")
            created_files.append(rel_name)

        return created_files
