"""Physical Path Resolution and Directory Hierarchy Manager for Agent Squad Work Items.

Enforces canonical 4-tier nesting under work/<project_id>/, supports zero-destructive
legacy flat reads, validates path containment guards, and sanitizes slugs.
Strictly stdlib-only.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import List, Optional, Set, Union

from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind
try:
    from project_context import PathContainmentGuard, PathContainmentViolation
except ImportError:
    from scripts.project_context import PathContainmentGuard, PathContainmentViolation
from .ids import CanonicalIdService

SLUG_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

WINDOWS_RESERVED_NAMES: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

PLURAL_CONTAINERS = {
    WorkItemKind.FEATURE: "features",
    WorkItemKind.STORY: "stories",
    WorkItemKind.TASK: "tasks",
    WorkItemKind.BUG: "bugs",
    WorkItemKind.SPIKE: "spikes",
}


def sanitize_slug(slug: str) -> str:
    """Validates that a work item identifier or slug is safe and does not escape paths."""
    if not slug or not isinstance(slug, str):
        raise ValidationError("Work item slug must be a non-empty string")

    trimmed = slug.strip()
    if ".." in trimmed or "/" in trimmed or "\\" in trimmed:
        raise PathContainmentViolation(f"Path traversal detected in slug: '{slug}'")

    if not SLUG_SAFE_RE.match(trimmed):
        raise ValidationError(
            f"Invalid work item slug '{slug}': contains illegal characters. Allowed: [A-Za-z0-9_-]"
        )

    if trimmed.upper() in WINDOWS_RESERVED_NAMES:
        raise ValidationError(f"Invalid work item slug '{slug}': reserved system device name")

    return trimmed


class WorkItemPathResolver:
    """Resolves physical filesystem paths for Work Items in canonical and legacy layouts."""

    def __init__(self, runtime_root: Path, project_id: str):
        self.runtime_root = Path(runtime_root).resolve()
        self.project_id = sanitize_slug(project_id)
        self.project_work_dir = (self.runtime_root / "work" / self.project_id).resolve()

    def _validate_containment(self, target_path: Path) -> Path:
        """Enforces that resolved path strictly resides within the project work directory."""
        resolved = target_path.resolve()
        return PathContainmentGuard.validate_work_path(resolved, self.runtime_root, self.project_id)

    def resolve_item_path(self, work_item_id_or_path: Union[str, Path]) -> Path:
        """Resolves the absolute directory path of an existing work item.

        Two-tier lookup:
        1. Fast Path: If given a direct absolute or relative Path that exists with status.yaml.
        2. Tier 1: Canonical nested search under work/<project_id>/**/<ID>/status.yaml.
        3. Tier 2: Legacy flat search under work/<project_id>/<ID>/status.yaml.
        Matches canonical IDs and legacy aliases interchangeably.
        """
        # Case 1: Caller passed an existing Path or relative path
        as_path = Path(work_item_id_or_path)
        if as_path.is_absolute():
            candidate = as_path.resolve()
            if (candidate / "status.yaml").is_file():
                return self._validate_containment(candidate)
        else:
            parts = as_path.parts
            if len(parts) >= 2 and parts[0] == "work":
                if len(parts) >= 3 and parts[1] == self.project_id:
                    candidate = (self.project_work_dir / Path(*parts[2:])).resolve()
                else:
                    candidate = (self.runtime_root / as_path).resolve()
                if (candidate / "status.yaml").is_file():
                    return self._validate_containment(candidate)

        raw_id = as_path.name
        norm_id = CanonicalIdService.normalize(raw_id)
        legacy_id = CanonicalIdService.to_legacy_alias(norm_id)
        candidate_ids = {raw_id, norm_id}
        if legacy_id:
            candidate_ids.add(legacy_id)

        # Tier 2 (Direct Flat check): Check flat location under project work base
        for cid in candidate_ids:
            flat_candidate = (self.project_work_dir / cid).resolve()
            if (flat_candidate / "status.yaml").is_file():
                return self._validate_containment(flat_candidate)

        # Tier 1 (Nested check): Search in nested physical hierarchy
        if self.project_work_dir.exists():
            for status_path in self.project_work_dir.rglob("status.yaml"):
                item_dir = status_path.parent
                if item_dir.name in candidate_ids:
                    return self._validate_containment(item_dir)

        raise FileNotFoundError(
            f"Work item '{work_item_id_or_path}' not found in project '{self.project_id}' "
            f"under {self.project_work_dir}"
        )

    def construct_canonical_path(
        self,
        work_item_id: str,
        kind: Union[WorkItemKind, str],
        parent_id: Optional[str] = None,
    ) -> Path:
        """Constructs the canonical path for creating a new work item.

        If parent_id is provided, constructs nested path inside the appropriate plural container.
        If parent_id is None, places directly under work/<project_id>/<work_item_id>.
        """
        clean_id = sanitize_slug(work_item_id)

        if isinstance(kind, str):
            try:
                resolved_kind = WorkItemKind(kind.upper())
            except ValueError:
                resolved_kind = CanonicalIdService.infer_kind(clean_id)
        else:
            resolved_kind = kind

        if parent_id:
            clean_parent_id = sanitize_slug(parent_id)
            parent_dir = self.resolve_item_path(clean_parent_id)

            container = PLURAL_CONTAINERS.get(resolved_kind)
            if not container:
                if resolved_kind == WorkItemKind.BUG:
                    container = "tasks"
                elif resolved_kind == WorkItemKind.SPIKE:
                    container = "spikes"
                else:
                    container = "items"

            target_path = (parent_dir / container / clean_id).resolve()
        else:
            target_path = (self.project_work_dir / clean_id).resolve()

        return self._validate_containment(target_path)

    def find_all_work_items(self) -> List[Path]:
        """Scans the project work directory and returns paths to all valid work items."""
        if not self.project_work_dir.exists():
            return []

        items: List[Path] = []
        for status_path in self.project_work_dir.rglob("status.yaml"):
            item_dir = status_path.parent
            try:
                self._validate_containment(item_dir)
                items.append(item_dir)
            except PathContainmentViolation:
                continue
        return sorted(items)
