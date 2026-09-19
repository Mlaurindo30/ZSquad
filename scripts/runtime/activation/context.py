"""Hierarchical WorkContext Builder for Milestone R9.

Traverses ancestors (EPIC -> FEATURE -> STORY -> TASK), extracts specifications,
computes deterministic cache fingerprints, and enforces fail-closed completeness.
Strictly stdlib + yaml only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml

from scripts.domain.common import ValidationError
from scripts.domain.delegation import AncestorSnapshot, WorkContext
from scripts.domain.work_items import AcceptanceCriterion, WorkItemKind
from scripts.runtime.work_items.hierarchy import HierarchyContextResolver
from scripts.runtime.work_items.ids import CanonicalIdService
from scripts.runtime.work_items.paths import WorkItemPathResolver
from .errors import IncompleteContextError


SPEC_FILE_NAMES = [
    "epic.md",
    "product-goal.md",
    "feature.md",
    "component-design.md",
    "story.md",
    "acceptance-criteria.md",
    "architecture.md",
]


class WorkContextBuilder:
    """Constructs complete, immutable, hierarchical WorkContext for specialist activation."""

    def __init__(self, runtime_root: Union[str, Path], project_id: str):
        self.runtime_root = Path(runtime_root).resolve()
        self.project_id = str(project_id).strip()
        self.path_resolver = WorkItemPathResolver(self.runtime_root, self.project_id)
        self.hierarchy_resolver = HierarchyContextResolver(self.path_resolver)

    def _read_yaml(self, file_path: Path) -> Dict[str, Any]:
        if not file_path.is_file():
            return {}
        try:
            content = file_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            return data if isinstance(data, dict) else {}
        except Exception as err:
            raise IncompleteContextError(f"Failed to parse YAML from '{file_path}': {err}") from err

    def _extract_spec_summary(self, dir_path: Path, max_chars: int = 800) -> Tuple[str, Dict[str, str]]:
        """Extracts spec summary and dictionary of spec file contents."""
        artifacts: Dict[str, str] = {}
        summaries: List[str] = []

        for name in SPEC_FILE_NAMES:
            spec_path = dir_path / name
            if spec_path.is_file():
                try:
                    text = spec_path.read_text(encoding="utf-8")
                    rel_name = spec_path.name
                    artifacts[rel_name] = text
                    cleaned = text.strip()
                    first_para = cleaned.split("\n\n")[0] if "\n\n" in cleaned else cleaned[:max_chars]
                    summaries.append(f"[{rel_name}]: {first_para[:300]}")
                except Exception as err:
                    raise IncompleteContextError(f"Cannot read specification artifact '{spec_path}': {err}") from err

        summary_text = " | ".join(summaries) if summaries else "No spec artifacts present."
        return summary_text[:max_chars], artifacts

    def _parse_acceptance_criteria(self, raw_criteria: Any) -> List[AcceptanceCriterion]:
        criteria: List[AcceptanceCriterion] = []
        if not raw_criteria or not isinstance(raw_criteria, list):
            return criteria

        for idx, item in enumerate(raw_criteria, 1):
            if isinstance(item, dict):
                cid = str(item.get("id") or f"AC-{idx:03d}")
                scenario = str(item.get("scenario") or item.get("title") or f"Scenario {idx}")
                given = str(item.get("given") or "Given context")
                when = str(item.get("when") or "When action executed")
                then = str(item.get("then") or "Then result expected")
                criteria.append(
                    AcceptanceCriterion(
                        id=cid,
                        scenario=scenario,
                        given=given,
                        when=when,
                        then=then,
                        is_verified=bool(item.get("is_verified", False)),
                    )
                )
            elif isinstance(item, str):
                criteria.append(
                    AcceptanceCriterion(
                        id=f"AC-{idx:03d}",
                        scenario=f"Criterion {idx}",
                        given="Given system state",
                        when=f"When condition {item}",
                        then="Then condition satisfied",
                        is_verified=False,
                    )
                )
        return criteria

    def build_work_context(
        self,
        work_item_id: str,
        current_stage: Optional[str] = None,
        active_receipts: Optional[List[str]] = None,
    ) -> Tuple[WorkContext, str]:
        """Builds a complete WorkContext and deterministic cache fingerprint.

        Returns:
            Tuple[WorkContext, context_fingerprint]
        """
        try:
            item_path = self.path_resolver.resolve_item_path(work_item_id)
        except (FileNotFoundError, ValidationError) as err:
            raise IncompleteContextError(f"Cannot resolve work item path for '{work_item_id}': {err}") from err

        status_file = item_path / "status.yaml"
        if not status_file.is_file():
            raise IncompleteContextError(f"Missing status.yaml for work item '{work_item_id}' at {item_path}")

        status_data = self._read_yaml(status_file)
        item_id = str(status_data.get("id") or item_path.name)
        norm_id = CanonicalIdService.normalize(item_id)
        title = str(status_data.get("title") or norm_id)
        description = str(status_data.get("description") or "")
        stage = current_stage or str(status_data.get("stage") or status_data.get("current_stage") or "INTAKE")

        # Acceptance criteria & DoD
        raw_dod = status_data.get("definition_of_done", [])
        dod = [str(d) for d in raw_dod] if isinstance(raw_dod, list) else []

        raw_ac = status_data.get("acceptance_criteria", [])
        criteria = self._parse_acceptance_criteria(raw_ac)

        # Build ancestor hierarchy
        try:
            raw_ancestors = self.hierarchy_resolver.get_ancestor_chain(item_path)
        except Exception as err:
            raise IncompleteContextError(f"Error resolving ancestor chain for '{work_item_id}': {err}") from err

        ancestor_snapshots: List[AncestorSnapshot] = []
        all_ancestor_artifacts: Dict[str, str] = {}
        tracked_files: List[Path] = [status_file]

        for anc in raw_ancestors:
            anc_path = Path(anc["path"])
            anc_status_file = anc_path / "status.yaml"
            if not anc_status_file.is_file():
                raise IncompleteContextError(f"Ancestor '{anc.get('id')}' missing status.yaml at {anc_path}")

            tracked_files.append(anc_status_file)
            anc_status = anc.get("status", {})
            anc_id = str(anc.get("id") or anc_path.name)
            anc_kind_val = anc.get("kind") or CanonicalIdService.infer_kind(anc_id)
            if isinstance(anc_kind_val, str):
                anc_kind = WorkItemKind(anc_kind_val.upper())
            else:
                anc_kind = anc_kind_val

            anc_title = str(anc_status.get("title") or anc_id)
            anc_stage = str(anc_status.get("stage") or "UNKNOWN")

            spec_summary, spec_artifacts = self._extract_spec_summary(anc_path)
            for sname, scontent in spec_artifacts.items():
                rel_key = f"{anc_id}/{sname}"
                all_ancestor_artifacts[rel_key] = scontent
                tracked_files.append(anc_path / sname)

            ancestor_snapshots.append(
                AncestorSnapshot(
                    work_item_id=anc_id,
                    kind=anc_kind,
                    title=anc_title,
                    stage=anc_stage,
                    spec_summary=spec_summary,
                )
            )

        # Include local spec files in artifacts and tracking
        _, local_spec_artifacts = self._extract_spec_summary(item_path)
        for sname, scontent in local_spec_artifacts.items():
            rel_key = f"{norm_id}/{sname}"
            all_ancestor_artifacts[rel_key] = scontent
            tracked_files.append(item_path / sname)

        receipts = list(active_receipts or [])
        filesystem_scope = [str(item_path.resolve())]

        context = WorkContext(
            work_item_id=norm_id,
            project_id=self.project_id,
            current_stage=stage,
            title=title,
            description=description,
            definition_of_done=dod,
            acceptance_criteria=criteria,
            ancestors=ancestor_snapshots,
            ancestor_artifacts=all_ancestor_artifacts,
            active_receipts=receipts,
            filesystem_scope=filesystem_scope,
        )

        fingerprint = self.compute_fingerprint(context, tracked_files)
        return context, fingerprint

    @staticmethod
    def compute_fingerprint(context: WorkContext, tracked_files: Optional[List[Path]] = None) -> str:
        """Computes deterministic SHA256 fingerprint over logical semantic content only.

        Zero reliance on filesystem timestamps (mtime), file sizes, or host-dependent absolute paths.
        Identical logical WorkContexts across fresh clones or directory copies yield identical fingerprints.
        """
        hasher = hashlib.sha256()
        hasher.update(f"work_item_id:{context.work_item_id}\n".encode("utf-8"))
        hasher.update(f"project_id:{context.project_id}\n".encode("utf-8"))
        hasher.update(f"stage:{context.current_stage}\n".encode("utf-8"))
        hasher.update(f"title:{context.title}\n".encode("utf-8"))
        hasher.update(f"description:{context.description}\n".encode("utf-8"))

        for dod in sorted(context.definition_of_done):
            hasher.update(f"dod:{dod}\n".encode("utf-8"))

        for ac in context.acceptance_criteria:
            hasher.update(f"ac:{ac.id}:{ac.scenario}:{ac.given}:{ac.when}:{ac.then}:{ac.is_verified}\n".encode("utf-8"))

        for anc in context.ancestors:
            kind_val = anc.kind.value if hasattr(anc.kind, "value") else str(anc.kind)
            hasher.update(f"anc:{anc.work_item_id}:{kind_val}:{anc.title}:{anc.stage}:{anc.spec_summary}\n".encode("utf-8"))

        for key in sorted(context.ancestor_artifacts.keys()):
            content = context.ancestor_artifacts[key]
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            hasher.update(f"artifact:{key}:{content_hash}\n".encode("utf-8"))

        for r in sorted(context.active_receipts):
            hasher.update(f"receipt:{r}\n".encode("utf-8"))

        for s in sorted(context.filesystem_scope):
            rel_s = s.replace("\\", "/").rstrip("/").split("/")[-1]
            hasher.update(f"scope:{rel_s}\n".encode("utf-8"))

        return hasher.hexdigest()
