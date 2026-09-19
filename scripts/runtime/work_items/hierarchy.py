"""Hierarchy Validation, Parent Resolution, and Ancestor Context Propagation.

Governs parent-child integrity invariants (EPIC -> FEATURE -> STORY -> TASK),
short-circuit and fallback parent resolution, and ancestor context compilation.
Strictly stdlib-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
import yaml

from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkHierarchy, WorkItemKind
from .ids import CanonicalIdService
from .paths import WorkItemPathResolver


def _to_kind(val: Optional[Union[WorkItemKind, str]]) -> Optional[WorkItemKind]:
    if val is None:
        return None
    if isinstance(val, WorkItemKind):
        return val
    try:
        return WorkItemKind(val.upper().strip())
    except ValueError:
        return CanonicalIdService.infer_kind(val)


def validate_parent_child(
    parent_kind: Optional[Union[WorkItemKind, str]],
    child_kind: Union[WorkItemKind, str],
) -> None:
    """Validates parent-child relationship against canonical 4-tier hierarchy rules."""
    p_kind = _to_kind(parent_kind)
    c_kind = _to_kind(child_kind)
    if c_kind is None:
        raise ValidationError("Child WorkItemKind must not be None")
    WorkHierarchy.validate_parent_child(p_kind, c_kind)


class HierarchyContextResolver:
    """Resolves parentage and recursive ancestor lineage for Work Items."""

    def __init__(self, path_resolver: WorkItemPathResolver):
        self.path_resolver = path_resolver

    def _read_status(self, item_dir: Path) -> Dict[str, Any]:
        status_file = item_dir / "status.yaml"
        if not status_file.is_file():
            raise FileNotFoundError(f"status.yaml missing in {item_dir}")
        with open(status_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    def get_parent(self, work_item_id_or_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
        """Resolves direct parent metadata and path for a work item."""
        item_path = self.path_resolver.resolve_item_path(work_item_id_or_path)
        status = self._read_status(item_path)
        parent_id = status.get("parent_id")
        if not parent_id:
            return None

        clean_parent_id = CanonicalIdService.normalize(str(parent_id))
        parent_path: Optional[Path] = None

        # Physical Short-Circuit: If in plural subfolder (.../stories/STORY-01)
        if item_path.parent.name in {"features", "stories", "tasks", "bugs", "spikes", "items"}:
            candidate_parent = item_path.parent.parent
            if (candidate_parent / "status.yaml").is_file():
                cand_status = self._read_status(candidate_parent)
                cand_id = CanonicalIdService.normalize(str(cand_status.get("id", candidate_parent.name)))
                if cand_id == clean_parent_id:
                    parent_path = candidate_parent

        # Fallback to general resolver
        if not parent_path:
            parent_path = self.path_resolver.resolve_item_path(clean_parent_id)

        parent_status = self._read_status(parent_path)
        child_type = status.get("type", item_path.name)
        parent_type = parent_status.get("type", parent_path.name)
        validate_parent_child(parent_type, child_type)

        return {
            "id": parent_status.get("id", parent_path.name),
            "type": parent_type,
            "kind": _to_kind(parent_type),
            "path": parent_path,
            "status": parent_status,
            "story_points": parent_status.get("story_points"),
            "cycle": parent_status.get("cycle"),
        }

    def get_ancestor_chain(self, work_item_id_or_path: Union[str, Path]) -> List[Dict[str, Any]]:
        """Ascends the tree and returns the ordered list of ancestors: [Parent, Grandparent, ..., Root]."""
        chain: List[Dict[str, Any]] = []
        visited_ids: Set[str] = set()

        curr_target: Union[str, Path] = work_item_id_or_path
        while True:
            parent_info = self.get_parent(curr_target)
            if not parent_info:
                break

            pid = str(parent_info["id"])
            if pid in visited_ids:
                raise ValidationError(f"Circular parentage hierarchy detected at '{pid}'")
            visited_ids.add(pid)
            chain.append(parent_info)
            curr_target = parent_info["path"]

        return chain

    def compile_hierarchy_context(self, work_item_id_or_path: Union[str, Path]) -> Dict[str, Any]:
        """Compiles rich ancestral context suitable for subagent briefing injection."""
        item_path = self.path_resolver.resolve_item_path(work_item_id_or_path)
        status = self._read_status(item_path)
        item_id = status.get("id", item_path.name)
        item_type = status.get("type", "unknown")

        ancestors = self.get_ancestor_chain(item_path)

        briefing_lines: List[str] = [
            f"# CONTEXTO HIERÁRQUICO DO WORK ITEM",
            f"Você está atuando no item: {item_id} ({item_type.upper()})",
            "",
            "## LINHAGEM E ESPECIFICAÇÕES ANCESTRAIS",
        ]

        if not ancestors:
            briefing_lines.append("- Item raiz de escopo (sem ancestrais).")
        else:
            icons = {
                WorkItemKind.STORY: "🔷",
                WorkItemKind.FEATURE: "🟣",
                WorkItemKind.EPIC: "🔶",
            }
            for anc in ancestors:
                kind = anc["kind"]
                icon = icons.get(kind, "🔹")
                sp_info = f" ({anc['story_points']} SP)" if anc.get("story_points") else ""
                briefing_lines.append(f"- {icon} {anc['type'].upper()}: {anc['id']}{sp_info}")
                briefing_lines.append(f"  * Path: {anc['path']}")

                # Extract excerpt if relevant spec files exist
                anc_path = anc["path"]
                if (anc_path / "acceptance-criteria.md").is_file():
                    briefing_lines.append("  * Artefato: acceptance-criteria.md disponível")
                if (anc_path / "component-design.md").is_file():
                    briefing_lines.append("  * Artefato: component-design.md disponível")
                if (anc_path / "product-goal.md").is_file():
                    briefing_lines.append("  * Artefato: product-goal.md disponível")

        return {
            "work_item_id": item_id,
            "type": item_type,
            "path": str(item_path),
            "ancestors": ancestors,
            "formatted_briefing": "\n".join(briefing_lines),
        }
