"""Authoritative Conflict and Workflow Drift Reconciliation Engine.

Strictly stdlib-only.
Enforces Section 20 and 21 of R6 Specification:
- Deterministic conflict matrix evaluation (NOOP, APPLY_LOCAL_TO_REMOTE,
  APPLY_REMOTE_TO_LOCAL, BLOCK_ILLEGAL_REMOTE_TRANSITION, CONFLICT_REQUIRES_RESOLUTION).
- Fail-closed validation against R4 Canonical Lifecycle Engine.
- Workflow drift detection and DomainEvent emission ('agent_squad.delivery.sync.workflow_drift_detected').
- Absolute prohibition of blind Last-Write-Wins (LWW).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from scripts.domain.events import DomainEvent
from scripts.domain.lifecycle import LifecycleStage
from scripts.domain.sync import (
    ReconciliationAction,
    ReconciliationDecision,
    ReconciliationOutcome,
)
from scripts.runtime.delivery.azure_discovery import (
    AzureDiscoveryPort,
    ClassificationNodeInfo,
    ProcessTemplateInfo,
    RepositoryInfo,
    TeamInfo,
    TeamProjectInfo,
)
from scripts.runtime.delivery.azure_writer import AzureWriterPort
from scripts.runtime.delivery.errors import (
    AmbiguousRepositoryError,
    AmbiguousResourceError,
    DeliveryBindingError,
    ForbiddenResourceMutationError,
    IllegalRemoteTransitionError,
    MissingResourceError,
    TeamProjectNotFoundError,
)
from scripts.runtime.delivery.state_mapping import (
    azure_to_lifecycle_stage,
    map_stage_to_azure,
)
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle.engine import CanonicalLifecycleService
from scripts.runtime.lifecycle.policies import (
    get_stage_policy,
    normalize_stage,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ConflictReconciliationEngine:
    """Evaluates divergences between local canonical state and remote Azure Boards state."""

    DRIFT_EVENT_TYPE = "agent_squad.delivery.sync.workflow_drift_detected"

    def __init__(
        self,
        lifecycle_service: Optional[CanonicalLifecycleService] = None,
        event_store: Optional[SqliteEventStore] = None,
    ) -> None:
        self.lifecycle_service = lifecycle_service
        self.event_store = event_store

    def evaluate(
        self,
        work_item_id: str,
        project_id: str,
        current_local_stage: Union[LifecycleStage, str],
        remote_state: str,
        remote_board_column: Optional[str] = None,
        remote_tags: Union[List[str], str, None] = None,
        local_has_unpushed_changes: bool = False,
        remote_rev_changed: bool = False,
        process_template: str = "Agile",
        item_path: Optional[Path] = None,
    ) -> ReconciliationDecision:
        """Evaluates divergence and computes deterministic ReconciliationDecision."""
        local_stage = normalize_stage(current_local_stage)
        remote_stage = azure_to_lifecycle_stage(
            state=remote_state,
            board_column=remote_board_column,
            tags=remote_tags,
            process_template=process_template,
        )

        local_str = local_stage.value
        remote_str = remote_stage.value

        # Case 1: Both unchanged and in identical stage
        if local_stage == remote_stage and not local_has_unpushed_changes:
            return ReconciliationDecision(
                work_item_id=work_item_id,
                action=ReconciliationAction.NOOP,
                reason="Both systems synchronized in identical state.",
                local_state=local_str,
                remote_state=remote_str,
                detected_at=_utc_now(),
            )

        # Case 2: Local progressed legally, remote unchanged
        if local_stage != remote_stage and local_has_unpushed_changes and not remote_rev_changed:
            return ReconciliationDecision(
                work_item_id=work_item_id,
                action=ReconciliationAction.APPLY_LOCAL_TO_REMOTE,
                reason=f"Local state advanced to {local_str}; outbound sync required.",
                local_state=local_str,
                remote_state=remote_str,
                detected_at=_utc_now(),
            )

        # Case 3: Remote attempted a state change
        if remote_stage != local_stage:
            is_legal, legality_reason = self._is_legal_transition(
                work_item_id=work_item_id,
                project_id=project_id,
                current_stage=local_stage,
                target_stage=remote_stage,
                item_path=item_path,
            )

            if not is_legal:
                # Illegal remote move -> BLOCK fail-closed & emit drift event
                decision = ReconciliationDecision(
                    work_item_id=work_item_id,
                    action=ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION,
                    reason=(
                        f"Illegal remote transition: Cannot move '{work_item_id}' from "
                        f"{local_str} to {remote_str}: {legality_reason}"
                    ),
                    local_state=local_str,
                    remote_state=remote_str,
                    detected_at=_utc_now(),
                )
                self._emit_drift_event(
                    work_item_id=work_item_id,
                    project_id=project_id,
                    attempted_remote_state=remote_state,
                    canonical_local_state=local_str,
                    reason=decision.reason,
                )
                return decision

            # Remote transition is legal per R4
            if local_has_unpushed_changes:
                # Concurrent evolution on both sides -> CONFLICT
                return ReconciliationDecision(
                    work_item_id=work_item_id,
                    action=ReconciliationAction.CONFLICT_REQUIRES_RESOLUTION,
                    reason=(
                        f"Concurrent divergence on '{work_item_id}': local mutated to {local_str} "
                        f"while remote legally advanced to {remote_str}. Requires human resolution."
                    ),
                    local_state=local_str,
                    remote_state=remote_str,
                    detected_at=_utc_now(),
                )

            # Local unchanged, remote legal forward move
            return ReconciliationDecision(
                work_item_id=work_item_id,
                action=ReconciliationAction.APPLY_REMOTE_TO_LOCAL,
                reason=f"Remote work item legally advanced to {remote_str}; applying to local lifecycle.",
                local_state=local_str,
                remote_state=remote_str,
                detected_at=_utc_now(),
            )

        # Case 4: Same stage but local has unpushed metadata/edits
        if local_has_unpushed_changes and not remote_rev_changed:
            return ReconciliationDecision(
                work_item_id=work_item_id,
                action=ReconciliationAction.APPLY_LOCAL_TO_REMOTE,
                reason="Local metadata changes queued for outbound sync.",
                local_state=local_str,
                remote_state=remote_str,
                detected_at=_utc_now(),
            )

        # Default fallback: safe NOOP
        return ReconciliationDecision(
            work_item_id=work_item_id,
            action=ReconciliationAction.NOOP,
            reason="No divergence detected between local and remote representations.",
            local_state=local_str,
            remote_state=remote_str,
            detected_at=_utc_now(),
        )

    def _is_legal_transition(
        self,
        work_item_id: str,
        project_id: str,
        current_stage: LifecycleStage,
        target_stage: LifecycleStage,
        item_path: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """Validates if transition from current_stage to target_stage is allowed by R4 policy."""
        # 1. Use CanonicalLifecycleService if available
        if self.lifecycle_service is not None:
            try:
                allowed, reason = self.lifecycle_service.can_transition(
                    work_item_id=work_item_id,
                    project_id=project_id,
                    target_stage=target_stage,
                    item_path=item_path,
                )
                return allowed, reason
            except Exception as exc:
                logger.debug(f"lifecycle_service.can_transition error: {exc}")

        # 2. Direct StagePolicy verification fallback
        policy = get_stage_policy(current_stage)
        if target_stage not in policy.allowed_next_stages:
            return False, f"Stage {current_stage.value} does not allow direct transition to {target_stage.value}"

        # 3. Guard against skipping to terminal without prerequisites
        if target_stage == LifecycleStage.DONE:
            if current_stage != LifecycleStage.GOVERNANCE_RELEASE:
                return False, f"Cannot jump directly to DONE from {current_stage.value} without G6 governance release"

        return True, "Transition satisfies lifecycle policy"

    def _emit_drift_event(
        self,
        work_item_id: str,
        project_id: str,
        attempted_remote_state: str,
        canonical_local_state: str,
        reason: str,
    ) -> None:
        """Emits WORKFLOW_DRIFT_DETECTED event to event store if configured."""
        if self.event_store is None:
            return

        event = DomainEvent.create(
            event_type=self.DRIFT_EVENT_TYPE,
            work_item_id=work_item_id,
            project_id=project_id,
            source="sync_reconciliation_engine",
            correlation_id=f"drift-{uuid.uuid4()}",
            causation_id=work_item_id,
            payload={
                "work_item_id": work_item_id,
                "project_id": project_id,
                "attempted_remote_state": attempted_remote_state,
                "canonical_local_state": canonical_local_state,
                "reason": reason,
                "timestamp": _utc_now_iso(),
            },
        )
        try:
            if hasattr(self.event_store, "save_event"):
                self.event_store.save_event(event)
            elif hasattr(self.event_store, "record_event"):
                self.event_store.record_event(event)
        except Exception as exc:
            logger.error(f"Failed to record drift event: {exc}")


@dataclass(frozen=True)
class ResourceReconciliationResult:
    """Result of resource reconciliation across repositories, teams, and classification nodes."""

    reconciled: bool
    reused_resources: List[str] = field(default_factory=list)
    created_resources: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ResourceReconciliationService:
    """Discovers, reconciles, and provisions delivery resources per R6 Sections 5, 23 & 47.

    Guarantees:
    - Existing resources (repo, team, area, iteration) are discovered and reused.
    - Missing managed resources are provisioned via writer.
    - Missing external resources fail-closed (MissingResourceError).
    - Ambiguous resources fail-closed (AmbiguousResourceError).
    - Area and iteration creation is validated under approved subtree (starts with team_project).
    - Team Project creation is strictly prohibited (POST /_apis/projects blocked).
    - Process templates are strictly read-only and never mutated.
    - Repeat reconciliation is idempotent.
    """

    def __init__(
        self,
        discovery: AzureDiscoveryPort,
        writer: AzureWriterPort,
    ) -> None:
        self.discovery = discovery
        self.writer = writer

    def reconcile_project_resources(
        self,
        organization_url: str,
        team_project_name: str,
        repository_name: Optional[str] = None,
        is_repository_managed: bool = True,
        team_name: Optional[str] = None,
        is_team_managed: bool = True,
        area_path: Optional[str] = None,
        is_area_managed: bool = True,
        iteration_path: Optional[str] = None,
        is_iteration_managed: bool = True,
    ) -> ResourceReconciliationResult:
        reused: List[str] = []
        created: List[str] = []
        errors: List[str] = []

        # 1. Team Project Discovery (Enterprise container: strictly read-only)
        # Invariant: Team Project creation is CATEGORICALLY PROHIBITED.
        tp = self.discovery.get_project(organization_url, team_project_name)
        if not tp:
            raise TeamProjectNotFoundError(team_project_name, organization_url)
        reused.append(f"team_project:{tp.name}")

        project_id = tp.id

        # 2. Repository Reconciliation
        if repository_name:
            repo = self.discovery.get_repository(organization_url, project_id, repository_name)
            if repo:
                reused.append(f"repository:{repo.name}")
            else:
                if is_repository_managed:
                    self.writer.create_repository(organization_url, project_id, repository_name, is_managed=True)
                    created.append(f"repository:{repository_name}")
                else:
                    raise MissingResourceError("Repository", repository_name, details={"mode": "EXTERNAL"})

        # 3. Team Reconciliation
        if team_name:
            team = self.discovery.get_team(organization_url, project_id, team_name)
            if team:
                reused.append(f"team:{team.name}")
            else:
                if is_team_managed:
                    self.writer.create_team(organization_url, project_id, team_name, is_managed=True)
                    created.append(f"team:{team_name}")
                else:
                    raise MissingResourceError("Team", team_name, details={"mode": "EXTERNAL"})

        # 4. Area Path Reconciliation
        if area_path:
            expected_prefix = team_project_name
            if not (area_path == expected_prefix or area_path.startswith(f"{expected_prefix}\\")):
                raise DeliveryBindingError(
                    f"Area path '{area_path}' must be subordinated to Team Project '{team_project_name}'",
                    code="AZ_INVALID_SUBTREE",
                )
            rel_path = area_path[len(expected_prefix):].lstrip("\\")
            node = None
            if hasattr(self.discovery, "get_area_node"):
                node = self.discovery.get_area_node(organization_url, project_id, area_path)
            elif hasattr(self.discovery, "get_classification_node"):
                node = self.discovery.get_classification_node(organization_url, team_project_name, "areas", rel_path) if rel_path else True

            if node:
                reused.append(f"area:{area_path}")
            else:
                if is_area_managed:
                    parts = rel_path.split("\\")
                    node_name = parts[-1]
                    parent_path = "\\".join(parts[:-1]) if len(parts) > 1 else None
                    self.writer.create_area(organization_url, team_project_name, node_name, parent_path=parent_path, is_managed=True)
                    created.append(f"area:{area_path}")
                else:
                    raise MissingResourceError("AreaPath", area_path, details={"mode": "EXTERNAL"})

        # 5. Iteration Path Reconciliation
        if iteration_path:
            expected_prefix = team_project_name
            if not (iteration_path == expected_prefix or iteration_path.startswith(f"{expected_prefix}\\")):
                raise DeliveryBindingError(
                    f"Iteration path '{iteration_path}' must be subordinated to Team Project '{team_project_name}'",
                    code="AZ_INVALID_SUBTREE",
                )
            rel_path = iteration_path[len(expected_prefix):].lstrip("\\")
            node = None
            if hasattr(self.discovery, "get_iteration_node"):
                node = self.discovery.get_iteration_node(organization_url, project_id, iteration_path)
            elif hasattr(self.discovery, "get_classification_node"):
                node = self.discovery.get_classification_node(organization_url, team_project_name, "iterations", rel_path) if rel_path else True

            if node:
                reused.append(f"iteration:{iteration_path}")
            else:
                if is_iteration_managed:
                    parts = rel_path.split("\\")
                    node_name = parts[-1]
                    parent_path = "\\".join(parts[:-1]) if len(parts) > 1 else None
                    self.writer.create_iteration(organization_url, team_project_name, node_name, parent_path=parent_path, is_managed=True)
                    created.append(f"iteration:{iteration_path}")
                else:
                    raise MissingResourceError("IterationPath", iteration_path, details={"mode": "EXTERNAL"})

        # 6. Process Template Verification (strictly read-only, never mutated)
        proc = None
        if hasattr(self.discovery, "get_process_template"):
            proc = self.discovery.get_process_template(organization_url, project_id)
        elif hasattr(self.discovery, "get_project_process_template"):
            proc = self.discovery.get_project_process_template(organization_url, project_id)

        if proc:
            t_name = getattr(proc, "template_name", getattr(proc, "name", "Unknown"))
            reused.append(f"process_template:{t_name}")

        return ResourceReconciliationResult(
            reconciled=True,
            reused_resources=reused,
            created_resources=created,
            errors=errors,
        )
