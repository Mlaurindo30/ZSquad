"""Synchronous project admission control for Work In Progress (WIP) limits.

Strictly stdlib-only.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Dict, Optional, Union
import yaml

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.lifecycle.errors import ConfigurationError, WIPLimitExceededError
from scripts.runtime.lifecycle.policies import (
    CANONICAL_TO_LEGACY_STATE,
    normalize_stage,
)

# Canonical WIP limits per stage (Kanban discipline - Henrik Kniberg)
DEFAULT_WIP_LIMITS: Dict[LifecycleStage, Optional[int]] = {
    LifecycleStage.INTAKE: 10,
    LifecycleStage.DISCOVERY: 3,
    LifecycleStage.REQUIREMENTS_PRODUCT: 2,  # blueprint limit = 2
    LifecycleStage.PLANNING: 3,
    LifecycleStage.ARCHITECTURE_DESIGN: 2,
    LifecycleStage.READINESS_SCAFFOLDING: 2,
    LifecycleStage.IMPLEMENTATION: 3,
    LifecycleStage.CODE_REVIEW: 2,
    LifecycleStage.SECURITY_REVIEW: 2,
    LifecycleStage.TEST_VALIDATION: 2,
    LifecycleStage.QA_VALIDATION: 2,
    LifecycleStage.GOVERNANCE_RELEASE: 1,
    LifecycleStage.DONE: None,  # Unlimited
}


class WIPController:
    """Controls work item admission into lifecycle stages based on project WIP limits."""

    def __init__(self, default_limits: Optional[Dict[LifecycleStage, Optional[int]]] = None) -> None:
        self.limits = dict(default_limits or DEFAULT_WIP_LIMITS)

    def get_limit(
        self,
        stage: Union[str, LifecycleStage],
        workflow_cfg: Optional[dict] = None,
    ) -> Optional[int]:
        """Resolves the WIP limit for a given stage, checking workflow_cfg first."""
        norm = normalize_stage(stage)
        legacy_name = CANONICAL_TO_LEGACY_STATE.get(norm, norm.value.lower())

        if workflow_cfg and isinstance(workflow_cfg, dict):
            flow_cfg = workflow_cfg.get("flow")
            if flow_cfg is not None and isinstance(flow_cfg, dict):
                wip_limits = flow_cfg.get("wip_limits", {})
                if isinstance(wip_limits, dict):
                    raw_val = None
                    if legacy_name in wip_limits:
                        raw_val = wip_limits[legacy_name]
                    elif norm.value.lower() in wip_limits:
                        raw_val = wip_limits[norm.value.lower()]

                    if raw_val is not None:
                        try:
                            parsed = int(raw_val)
                            if parsed < 0:
                                raise ConfigurationError(
                                    f"Invalid negative WIP limit for stage '{legacy_name}': {raw_val}"
                                )
                            return parsed
                        except (ValueError, TypeError) as exc:
                            raise ConfigurationError(
                                f"Invalid WIP limit for stage '{legacy_name}': {raw_val}"
                            ) from exc
                    return None
            elif "wip_limits" in workflow_cfg:
                wip_limits = workflow_cfg["wip_limits"]
                if isinstance(wip_limits, dict):
                    raw_val = wip_limits.get(legacy_name, wip_limits.get(norm.value.lower()))
                    if raw_val is not None:
                        try:
                            parsed = int(raw_val)
                            if parsed < 0:
                                raise ConfigurationError(
                                    f"Invalid negative WIP limit for stage '{legacy_name}': {raw_val}"
                                )
                            return parsed
                        except (ValueError, TypeError) as exc:
                            raise ConfigurationError(
                                f"Invalid WIP limit for stage '{legacy_name}': {raw_val}"
                            ) from exc
                    return None
            else:
                # Custom workflow provided without any WIP limits defined
                return None

        return self.limits.get(norm)

    def count_active_items(
        self,
        db_conn: Optional[sqlite3.Connection],
        project_id: str,
        stage: Union[str, LifecycleStage],
        project_path: Optional[Path] = None,
        exclude_work_item_id: Optional[str] = None,
    ) -> int:
        """Counts the number of active items in target stage for the given project.

        Checks both SQLite work_item_lifecycle_state and local project directories.
        """
        norm = normalize_stage(stage)
        legacy_name = CANONICAL_TO_LEGACY_STATE.get(norm, norm.value.lower())
        exclude_id = exclude_work_item_id or ""

        found_ids = set()

        # 1. Inspect filesystem work directory
        if project_path is not None and project_path.is_dir():
            for item_dir in project_path.iterdir():
                if not item_dir.is_dir() or item_dir.name.startswith("."):
                    continue
                if item_dir.name == exclude_id:
                    continue
                status_file = item_dir / "status.yaml"
                if status_file.is_file():
                    try:
                        content = status_file.read_text(encoding="utf-8")
                        data = yaml.safe_load(content)
                        if isinstance(data, dict):
                            item_stage = data.get("state")
                            if item_stage:
                                try:
                                    if normalize_stage(item_stage) == norm:
                                        found_ids.add(item_dir.name)
                                except Exception:
                                    if str(item_stage).lower() in {legacy_name, norm.value.lower()}:
                                        found_ids.add(item_dir.name)
                    except Exception:
                        continue

        # 2. Query SQLite
        if db_conn is not None:
            try:
                cursor = db_conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT work_item_id
                    FROM work_item_lifecycle_state
                    WHERE (project_id = ? OR ? = '' OR project_id = 'default')
                      AND (current_stage = ? OR current_stage = ?)
                      AND work_item_id != ?
                    """,
                    (project_id, project_id, norm.value, legacy_name, exclude_id),
                )
                for (w_id,) in cursor.fetchall():
                    if project_path is None or (project_path / w_id).is_dir():
                        found_ids.add(w_id)
            except sqlite3.OperationalError:
                pass

        return len(found_ids)

    def assert_wip_capacity(
        self,
        db_conn: Optional[sqlite3.Connection],
        project_id: str,
        target_stage: Union[str, LifecycleStage],
        project_path: Optional[Path] = None,
        exclude_work_item_id: Optional[str] = None,
        workflow_cfg: Optional[dict] = None,
    ) -> None:
        """Asserts that target stage has capacity within WIP limits.

        Raises:
            WIPLimitExceededError: If the stage is at or above capacity.
        """
        norm = normalize_stage(target_stage)
        limit = self.get_limit(norm, workflow_cfg)
        if limit is None or limit <= 0 or limit == float("inf"):
            return

        current_count = self.count_active_items(
            db_conn=db_conn,
            project_id=project_id,
            stage=norm,
            project_path=project_path,
            exclude_work_item_id=exclude_work_item_id,
        )

        if current_count >= limit:
            legacy_name = CANONICAL_TO_LEGACY_STATE.get(norm, norm.value.lower())
            raise WIPLimitExceededError(
                f"WIP breach: Stage '{legacy_name}' ({norm.value}) in project '{project_id}' "
                f"has reached its WIP limit of {limit} active items (current: {current_count}). "
                "Admitting another work item is blocked under WIP discipline."
            )
