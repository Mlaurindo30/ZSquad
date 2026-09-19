"""Authoritative Delivery Backend Binding Service and Lifecycle Management.

Strictly stdlib-only.
Enforces local project identity resolution, read-only Azure discovery,
container vs product segregation, SHA-256 fingerprinting, SQLite persistence,
and R2 domain event notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import yaml

from scripts.domain.common import canonical_json
from scripts.domain.project import (
    DeliveryBackendKind,
    FORBIDDEN_LEGACY_TOKENS,
    _check_forbidden_tokens,
)
from scripts.runtime.delivery.azure_discovery import (
    AzureDiscoveryPort,
    ClassificationNodeInfo,
    ProcessTemplateInfo,
    ReadOnlyAzureDiscovery,
    RepositoryInfo,
    TeamInfo,
    TeamProjectInfo,
)
from scripts.runtime.delivery.errors import (
    AmbiguousResourceError,
    AzureUnavailableError,
    BindingBlockedError,
    DeliveryBackendNotConfiguredError,
    DeliveryBindingError,
    InvalidBindingConfigurationError,
    PathContainmentViolationError,
    ProjectConfigNotFoundError,
    ProjectNotResolvedError,
    TeamProjectNotFoundError,
)
from scripts.runtime.delivery.repository import (
    BindingHistoryRecord,
    ProjectBindingRecord,
    SqliteBindingRepository,
    compute_binding_fingerprint,
    sanitize_credentials,
)

logger = logging.getLogger(__name__)

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ResourceBindingStatus(str, Enum):
    """Per-resource discovery status."""
    RESOLVED = "RESOLVED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class ProjectBindingStatus(str, Enum):
    """Aggregate project delivery binding status."""
    COMPLETE = "COMPLETE"
    BOUND = "COMPLETE"       # Canonical alias
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    DRIFTED = "DRIFTED"
    UNBOUND = "UNBOUND"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class ResourceStatusReport:
    """Detailed discovery report for individual delivery backend resources."""
    team_project: ResourceBindingStatus
    repository: ResourceBindingStatus
    assigned_team: ResourceBindingStatus
    area_path: ResourceBindingStatus
    iteration_path: ResourceBindingStatus
    process_template: ResourceBindingStatus
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BindingResolutionResult:
    """Comprehensive result of project resolution and backend binding."""
    binding_record: ProjectBindingRecord
    status: ProjectBindingStatus
    resource_report: ResourceStatusReport
    is_persisted: bool
    events_emitted: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ProjectDeliveryBindingService:
    """Canonical service for resolving local projects and establishing delivery bindings."""

    def __init__(
        self,
        repository: Optional[SqliteBindingRepository] = None,
        discovery: Optional[AzureDiscoveryPort] = None,
        event_store: Optional[Any] = None,
        runtime_root: Optional[Path] = None,
    ) -> None:
        """Initializes the binding service with dependencies.

        Args:
            repository: SqliteBindingRepository for persistence. If None, instantiates default.
            discovery: AzureDiscoveryPort for topology discovery. If None, instantiates ReadOnlyAzureDiscovery.
            event_store: Optional SqliteEventStore for emitting R2 domain events.
            runtime_root: Optional SQUAD_RUNTIME root path.
        """
        self._repo = repository or SqliteBindingRepository()
        self._discovery = discovery or ReadOnlyAzureDiscovery()
        self._event_store = event_store
        if runtime_root is not None:
            self._runtime_root = Path(runtime_root).resolve()
        else:
            env_val = os.environ.get("SQUAD_RUNTIME")
            self._runtime_root = Path(env_val).resolve() if env_val else Path(__file__).resolve().parents[3]

    @property
    def repository(self) -> SqliteBindingRepository:
        return self._repo

    def resolve_local_project(
        self,
        path_or_root: Union[str, Path],
    ) -> Tuple[Path, Dict[str, Any]]:
        """Deterministically locates and validates declarative project configuration.
        
        Searches path_or_root and ancestors for:
        1. <root>/.agents_squad/config/project.yaml
        2. <root>/.squad/project.yaml (compatibility fallback)

        Returns:
            Tuple[Path, Dict[str, Any]]: (project_root, parsed_config)
        Raises:
            ProjectNotResolvedError, PathContainmentViolationError, InvalidBindingConfigurationError
        """
        start = Path(path_or_root).resolve()
        if start.is_file():
            start = start.parent

        current = start
        found_config: Optional[Path] = None
        found_root: Optional[Path] = None

        for candidate in (current, *current.parents):
            primary = candidate / ".agents_squad" / "config" / "project.yaml"
            if primary.is_file():
                found_config = primary
                found_root = candidate
                break
            fallback = candidate / ".squad" / "project.yaml"
            if fallback.is_file():
                found_config = fallback
                found_root = candidate
                break

        if not found_config or not found_root:
            raise ProjectConfigNotFoundError(search_path=str(start))

        # Check path containment: forbid work directories inside client project root
        project_local_work = found_root / "work"
        if project_local_work.is_dir():
            raise PathContainmentViolationError(
                path=str(project_local_work),
                boundary=str(self._runtime_root / "work"),
                details={"reason": "Project-local './work' directory detected. All work state must reside in SQUAD_RUNTIME/work."},
            )

        try:
            raw_text = found_config.read_text(encoding="utf-8")
            config = yaml.safe_load(raw_text) or {}
        except Exception as exc:
            raise InvalidBindingConfigurationError(
                project_id=found_root.name,
                reason=f"Failed to read/parse project configuration at {found_config}: {exc}",
            ) from exc

        if not isinstance(config, dict):
            raise InvalidBindingConfigurationError(
                project_id=found_root.name,
                reason=f"Project configuration root must be a YAML mapping: {found_config}",
            )

        # Validate project ID and name
        project_section = config.get("project")
        if isinstance(project_section, dict):
            raw_id = project_section.get("id") or project_section.get("project_id") or ""
            display_name = project_section.get("name") or str(raw_id)
        else:
            raw_id = config.get("project_id") or config.get("project_name") or found_root.name
            display_name = config.get("display_name") or str(raw_id)

        project_id = str(raw_id).strip()
        if not project_id:
            raise InvalidBindingConfigurationError(
                project_id=found_root.name,
                reason="Declarative configuration missing 'project.id' or 'project_id'",
            )

        if not PROJECT_ID_RE.fullmatch(project_id):
            raise InvalidBindingConfigurationError(
                project_id=project_id,
                reason=f"Invalid project_id grammar: '{project_id}'. Must match {PROJECT_ID_RE.pattern}",
            )

        # Enforce zero forbidden legacy tokens
        try:
            _check_forbidden_tokens("project_id", project_id)
            _check_forbidden_tokens("display_name", display_name)
        except Exception as exc:
            raise InvalidBindingConfigurationError(
                project_id=project_id,
                reason=str(exc),
            ) from exc

        return found_root, config

    def bind_project(
        self,
        path_or_root: Union[str, Path],
        persist: bool = True,
        changed_by: str = "delivery-orchestrator",
    ) -> BindingResolutionResult:
        """Resolves local project, discovers remote delivery topology, and establishes authoritative binding.

        GUARANTEES:
        - Read-only topology discovery: never creates Azure resources.
        - Complete secret isolation: zero PATs stored or logged.
        - Monotonic revisions & idempotency in SQLite.
        - Emits R2 domain events via Outbox.
        """
        emitted_events: List[str] = []
        errors: List[str] = []

        # 1. Resolve Local Project
        project_root, config = self.resolve_local_project(path_or_root)
        project_section = config.get("project") if isinstance(config.get("project"), dict) else {}
        project_id = str(project_section.get("id") or config.get("project_id") or project_root.name)
        display_name = str(project_section.get("name") or config.get("display_name") or project_id)

        # Emit project resolved event
        self._emit_event(
            event_type="agent_squad.project.resolved",
            project_id=project_id,
            payload={
                "project_id": project_id,
                "project_root": str(project_root),
                "display_name": display_name,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
            emitted_collector=emitted_events,
        )

        # 2. Inspect Delivery Backend Kind
        delivery_section = config.get("delivery") if isinstance(config.get("delivery"), dict) else {}
        backend_kind_raw = (delivery_section.get("backend") or config.get("delivery_backend_kind") or "").upper()

        if not backend_kind_raw:
            # Check devops.yaml fallback if present in project
            devops_path = project_root / ".agents_squad" / "config" / "devops.yaml"
            if devops_path.is_file():
                backend_kind_raw = DeliveryBackendKind.AZURE_DEVOPS.value
            else:
                backend_kind_raw = DeliveryBackendKind.LOCAL_ONLY.value

        try:
            backend_kind = DeliveryBackendKind(backend_kind_raw)
        except ValueError:
            raise InvalidBindingConfigurationError(
                project_id=project_id,
                reason=f"Unsupported delivery backend kind '{backend_kind_raw}'. Must be one of AZURE_DEVOPS, LOCAL_ONLY, MOCK",
            )

        # 3. Process Backend Binding
        if backend_kind == DeliveryBackendKind.LOCAL_ONLY:
            return self._bind_local_only(
                project_id=project_id,
                project_root=project_root,
                display_name=display_name,
                persist=persist,
                changed_by=changed_by,
                emitted_events=emitted_events,
            )
        elif backend_kind == DeliveryBackendKind.MOCK:
            return self._bind_mock(
                project_id=project_id,
                project_root=project_root,
                display_name=display_name,
                persist=persist,
                changed_by=changed_by,
                emitted_events=emitted_events,
            )
        elif backend_kind == DeliveryBackendKind.AZURE_DEVOPS:
            return self._bind_azure_devops(
                project_id=project_id,
                project_root=project_root,
                display_name=display_name,
                config=config,
                persist=persist,
                changed_by=changed_by,
                emitted_events=emitted_events,
            )
        else:
            raise DeliveryBindingError(f"Unhandled backend kind: {backend_kind}")

    def _bind_local_only(
        self,
        project_id: str,
        project_root: Path,
        display_name: str,
        persist: bool,
        changed_by: str,
        emitted_events: List[str],
    ) -> BindingResolutionResult:
        """Handles binding for LOCAL_ONLY governance."""
        delivery_ref = f"local://{project_id}"
        report = ResourceStatusReport(
            team_project=ResourceBindingStatus.NOT_CONFIGURED,
            repository=ResourceBindingStatus.NOT_CONFIGURED,
            assigned_team=ResourceBindingStatus.NOT_CONFIGURED,
            area_path=ResourceBindingStatus.NOT_CONFIGURED,
            iteration_path=ResourceBindingStatus.NOT_CONFIGURED,
            process_template=ResourceBindingStatus.NOT_CONFIGURED,
            details={"mode": "LOCAL_ONLY", "governed": True},
        )
        status = ProjectBindingStatus.COMPLETE
        record = ProjectBindingRecord(
            project_id=project_id,
            project_root=str(project_root),
            display_name=display_name,
            delivery_backend_kind=DeliveryBackendKind.LOCAL_ONLY.value,
            delivery_binding_ref=delivery_ref,
            binding_status=status.value,
            is_governed=True,
            metadata={"backend": "LOCAL_ONLY"},
        )

        is_persisted = False
        if persist:
            record, _ = self._repo.upsert_binding(
                record=record,
                changed_by=changed_by,
                action="BIND_LOCAL",
                details={"status": status.value},
            )
            is_persisted = True

        self._emit_event(
            event_type="agent_squad.delivery.bound",
            project_id=project_id,
            payload={
                "project_id": project_id,
                "delivery_backend_kind": DeliveryBackendKind.LOCAL_ONLY.value,
                "binding_status": status.value,
                "revision": record.revision,
                "fingerprint": record.fingerprint,
            },
            emitted_collector=emitted_events,
        )

        return BindingResolutionResult(
            binding_record=record,
            status=status,
            resource_report=report,
            is_persisted=is_persisted,
            events_emitted=emitted_events,
        )

    def _bind_mock(
        self,
        project_id: str,
        project_root: Path,
        display_name: str,
        persist: bool,
        changed_by: str,
        emitted_events: List[str],
    ) -> BindingResolutionResult:
        """Handles simulated binding for testing."""
        delivery_ref = f"mock://{project_id}"
        report = ResourceStatusReport(
            team_project=ResourceBindingStatus.RESOLVED,
            repository=ResourceBindingStatus.RESOLVED,
            assigned_team=ResourceBindingStatus.RESOLVED,
            area_path=ResourceBindingStatus.RESOLVED,
            iteration_path=ResourceBindingStatus.RESOLVED,
            process_template=ResourceBindingStatus.RESOLVED,
            details={"mode": "MOCK"},
        )
        status = ProjectBindingStatus.COMPLETE
        record = ProjectBindingRecord(
            project_id=project_id,
            project_root=str(project_root),
            display_name=display_name,
            delivery_backend_kind=DeliveryBackendKind.MOCK.value,
            delivery_binding_ref=delivery_ref,
            binding_status=status.value,
            is_governed=True,
            team_project_name="MockTeamProject",
            repository_name=f"{project_id}-repo",
            assigned_team_name="MockTeam",
            area_path=f"MockTeamProject\\{project_id}",
            iteration_path="MockTeamProject",
            process_template="Agile",
            metadata={"mode": "MOCK"},
        )

        is_persisted = False
        if persist:
            record, _ = self._repo.upsert_binding(
                record=record,
                changed_by=changed_by,
                action="BIND_MOCK",
                details={"status": status.value},
            )
            is_persisted = True

        self._emit_event(
            event_type="agent_squad.delivery.bound",
            project_id=project_id,
            payload={
                "project_id": project_id,
                "delivery_backend_kind": DeliveryBackendKind.MOCK.value,
                "binding_status": status.value,
                "revision": record.revision,
                "fingerprint": record.fingerprint,
            },
            emitted_collector=emitted_events,
        )

        return BindingResolutionResult(
            binding_record=record,
            status=status,
            resource_report=report,
            is_persisted=is_persisted,
            events_emitted=emitted_events,
        )

    def _bind_azure_devops(
        self,
        project_id: str,
        project_root: Path,
        display_name: str,
        config: Dict[str, Any],
        persist: bool,
        changed_by: str,
        emitted_events: List[str],
    ) -> BindingResolutionResult:
        """Executes strictly read-only Azure DevOps topology discovery and binding."""
        delivery_cfg = config.get("delivery", {})
        ado_cfg = delivery_cfg.get("azure_devops") if isinstance(delivery_cfg, dict) else None

        # Fallback to devops.yaml if present
        if not ado_cfg:
            devops_file = project_root / ".agents_squad" / "config" / "devops.yaml"
            if devops_file.is_file():
                try:
                    loaded = yaml.safe_load(devops_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        ado_cfg = loaded
                except Exception:
                    pass

        if not ado_cfg or not isinstance(ado_cfg, dict):
            # Not configured
            status = ProjectBindingStatus.NOT_CONFIGURED
            report = ResourceStatusReport(
                team_project=ResourceBindingStatus.NOT_CONFIGURED,
                repository=ResourceBindingStatus.NOT_CONFIGURED,
                assigned_team=ResourceBindingStatus.NOT_CONFIGURED,
                area_path=ResourceBindingStatus.NOT_CONFIGURED,
                iteration_path=ResourceBindingStatus.NOT_CONFIGURED,
                process_template=ResourceBindingStatus.NOT_CONFIGURED,
                details={"reason": "Missing azure_devops configuration block"},
            )
            record = ProjectBindingRecord(
                project_id=project_id,
                project_root=str(project_root),
                display_name=display_name,
                delivery_backend_kind=DeliveryBackendKind.AZURE_DEVOPS.value,
                delivery_binding_ref="ado://unconfigured",
                binding_status=status.value,
            )
            if persist:
                record, _ = self._repo.upsert_binding(record, changed_by=changed_by, action="BIND_UNCONFIGURED")

            self._emit_event(
                event_type="agent_squad.delivery.binding_blocked",
                project_id=project_id,
                payload={"project_id": project_id, "reason": "Missing azure_devops configuration block"},
                emitted_collector=emitted_events,
            )
            return BindingResolutionResult(record, status, report, persist, emitted_events, ["Missing azure_devops configuration block"])

        # Extract and validate fields
        raw_org = str(ado_cfg.get("organization_url") or ado_cfg.get("org_url") or ado_cfg.get("org") or "").strip()
        raw_tp = str(ado_cfg.get("team_project") or ado_cfg.get("project") or "").strip()
        raw_repo = str(ado_cfg.get("repository_name") or ado_cfg.get("repo") or project_id).strip()
        raw_team = str(ado_cfg.get("assigned_team") or ado_cfg.get("team") or "").strip()
        raw_area = str(ado_cfg.get("area_path") or "").strip()
        raw_iteration = str(ado_cfg.get("iteration_path") or "").strip()
        secret_ref = ado_cfg.get("service_hook_secret_ref")

        # Sanitize credentials from org URL
        org_url = sanitize_credentials(raw_org)
        if not org_url or not org_url.startswith("https://"):
            raise DeliveryBackendNotConfiguredError(
                project_id=project_id,
                backend_kind="AZURE_DEVOPS",
                reason=f"organization_url must use secure HTTPS: '{org_url}'",
            )

        if not raw_tp:
            raise DeliveryBackendNotConfiguredError(
                project_id=project_id,
                backend_kind="AZURE_DEVOPS",
                reason="team_project must not be empty",
            )

        # Enforce zero forbidden legacy tokens in ADO parameters
        _check_forbidden_tokens("team_project", raw_tp)
        if raw_team:
            _check_forbidden_tokens("assigned_team", raw_team)
        if raw_area:
            _check_forbidden_tokens("area_path", raw_area)
        if raw_iteration:
            _check_forbidden_tokens("iteration_path", raw_iteration)

        # Segregation Product vs Team Project check:
        # Area path must begin with Team Project name
        expected_area_prefix = raw_tp
        if raw_area and not (raw_area == expected_area_prefix or raw_area.startswith(f"{expected_area_prefix}\\")):
            raise InvalidBindingConfigurationError(
                project_id=project_id,
                reason=f"area_path '{raw_area}' must be subordinated to Team Project '{raw_tp}' (must start with '{expected_area_prefix}\\')",
            )

        # Default area and iteration if not specified
        if not raw_area:
            raw_area = f"{raw_tp}\\{project_id}"
        if not raw_iteration:
            raw_iteration = raw_tp

        # Discovery tracking
        report_details: Dict[str, Any] = {}
        tp_status = ResourceBindingStatus.MISSING
        repo_status = ResourceBindingStatus.MISSING
        team_status = ResourceBindingStatus.MISSING
        area_status = ResourceBindingStatus.MISSING
        iteration_status = ResourceBindingStatus.MISSING
        proc_status = ResourceBindingStatus.NOT_CONFIGURED

        discovered_tp: Optional[TeamProjectInfo] = None
        discovered_repo: Optional[RepositoryInfo] = None
        discovered_team: Optional[TeamInfo] = None
        discovered_area: Optional[ClassificationNodeInfo] = None
        discovered_iteration: Optional[ClassificationNodeInfo] = None
        discovered_proc: Optional[ProcessTemplateInfo] = None

        errors_encountered: List[str] = []

        try:
            # 1. Discover Team Project (Exact Match)
            discovered_tp = self._discovery.get_project(org_url, raw_tp)
            if discovered_tp:
                tp_status = ResourceBindingStatus.RESOLVED
                report_details["team_project_id"] = discovered_tp.id
                report_details["team_project_name"] = discovered_tp.name
            else:
                tp_status = ResourceBindingStatus.MISSING
                errors_encountered.append(f"Team Project '{raw_tp}' not found on Azure DevOps")
        except AzureUnavailableError as e:
            tp_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(str(e))
        except Exception as e:
            tp_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(f"Error discovering Team Project: {e}")

        # If Team Project is not resolved, we FAIL CLOSED immediately (BLOCKED)
        if tp_status != ResourceBindingStatus.RESOLVED:
            aggregate_status = ProjectBindingStatus.BLOCKED
            report = ResourceStatusReport(
                team_project=tp_status,
                repository=ResourceBindingStatus.NOT_CONFIGURED,
                assigned_team=ResourceBindingStatus.NOT_CONFIGURED,
                area_path=ResourceBindingStatus.NOT_CONFIGURED,
                iteration_path=ResourceBindingStatus.NOT_CONFIGURED,
                process_template=ResourceBindingStatus.NOT_CONFIGURED,
                details={"errors": errors_encountered},
            )
            binding_ref = f"{org_url}/{raw_tp}"
            record = ProjectBindingRecord(
                project_id=project_id,
                project_root=str(project_root),
                display_name=display_name,
                delivery_backend_kind=DeliveryBackendKind.AZURE_DEVOPS.value,
                delivery_binding_ref=binding_ref,
                binding_status=aggregate_status.value,
                organization_url=org_url,
                team_project_name=raw_tp,
                metadata={"errors": errors_encountered},
            )
            if persist:
                record, _ = self._repo.upsert_binding(record, changed_by=changed_by, action="BIND_BLOCKED")

            self._emit_event(
                event_type="agent_squad.delivery.binding_blocked",
                project_id=project_id,
                payload={"project_id": project_id, "reason": errors_encountered},
                emitted_collector=emitted_events,
            )
            return BindingResolutionResult(record, aggregate_status, report, persist, emitted_events, errors_encountered)

        # Team Project is RESOLVED! Now query child resources strictly in its scope
        project_uuid = discovered_tp.id

        # 2. Discover Repository
        try:
            discovered_repo = self._discovery.get_repository(org_url, project_uuid, raw_repo)
            if discovered_repo:
                repo_status = ResourceBindingStatus.RESOLVED
                report_details["repository_id"] = discovered_repo.id
                report_details["default_branch"] = discovered_repo.default_branch
            else:
                repo_status = ResourceBindingStatus.MISSING
                report_details["repository_missing"] = raw_repo
        except AmbiguousResourceError as e:
            repo_status = ResourceBindingStatus.AMBIGUOUS
            errors_encountered.append(str(e))
        except Exception as e:
            repo_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(f"Error discovering repository: {e}")

        # 3. Discover Team
        team_query = raw_team or discovered_tp.default_team_name or discovered_tp.default_team_id or ""
        try:
            if team_query:
                discovered_team = self._discovery.get_team(org_url, project_uuid, team_query)
                if discovered_team:
                    team_status = ResourceBindingStatus.RESOLVED
                    report_details["assigned_team_id"] = discovered_team.id
                    report_details["assigned_team_name"] = discovered_team.name
                else:
                    team_status = ResourceBindingStatus.MISSING
                    report_details["team_missing"] = team_query
            else:
                team_status = ResourceBindingStatus.NOT_CONFIGURED
        except AmbiguousResourceError as e:
            team_status = ResourceBindingStatus.AMBIGUOUS
            errors_encountered.append(str(e))
        except Exception as e:
            team_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(f"Error discovering team: {e}")

        # 4. Discover Area Path
        try:
            discovered_area = self._discovery.get_area_node(org_url, project_uuid, raw_area)
            if discovered_area:
                area_status = ResourceBindingStatus.RESOLVED
                report_details["area_node_id"] = discovered_area.id
            else:
                area_status = ResourceBindingStatus.MISSING
                report_details["area_missing"] = raw_area
        except Exception as e:
            area_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(f"Error discovering area path: {e}")

        # 5. Discover Iteration Path
        try:
            discovered_iteration = self._discovery.get_iteration_node(org_url, project_uuid, raw_iteration)
            if discovered_iteration:
                iteration_status = ResourceBindingStatus.RESOLVED
                report_details["iteration_node_id"] = discovered_iteration.id
            else:
                iteration_status = ResourceBindingStatus.MISSING
                report_details["iteration_missing"] = raw_iteration
        except Exception as e:
            iteration_status = ResourceBindingStatus.UNAVAILABLE
            errors_encountered.append(f"Error discovering iteration path: {e}")

        # 6. Discover Process Template
        try:
            discovered_proc = self._discovery.get_process_template(org_url, project_uuid)
            proc_status = ResourceBindingStatus.RESOLVED
            report_details["process_template"] = discovered_proc.template_name
            report_details["process_template_type"] = discovered_proc.template_type
        except Exception as e:
            proc_status = ResourceBindingStatus.UNAVAILABLE
            report_details["process_error"] = str(e)

        report = ResourceStatusReport(
            team_project=tp_status,
            repository=repo_status,
            assigned_team=team_status,
            area_path=area_status,
            iteration_path=iteration_status,
            process_template=proc_status,
            details=report_details,
        )

        # Determine Aggregate Status
        # Fatal conditions: any AMBIGUOUS or UNAVAILABLE resource
        if any(
            st in (ResourceBindingStatus.AMBIGUOUS, ResourceBindingStatus.UNAVAILABLE)
            for st in (repo_status, team_status, area_status, iteration_status)
        ):
            aggregate_status = ProjectBindingStatus.BLOCKED
        elif all(
            st in (ResourceBindingStatus.RESOLVED, ResourceBindingStatus.NOT_CONFIGURED)
            for st in (repo_status, team_status, area_status, iteration_status)
        ):
            aggregate_status = ProjectBindingStatus.COMPLETE
        else:
            # Some resources are MISSING (e.g. repo, team, or area node not yet created).
            # This is normal in R5: discovery marks them MISSING and defers reconciliation to R6!
            aggregate_status = ProjectBindingStatus.PARTIAL

        binding_ref = f"{org_url}/{discovered_tp.name}"
        record = ProjectBindingRecord(
            project_id=project_id,
            project_root=str(project_root),
            display_name=display_name,
            delivery_backend_kind=DeliveryBackendKind.AZURE_DEVOPS.value,
            delivery_binding_ref=binding_ref,
            binding_status=aggregate_status.value,
            is_governed=True,
            organization_url=org_url,
            team_project_id=discovered_tp.id,
            team_project_name=discovered_tp.name,
            repository_id=discovered_repo.id if discovered_repo else None,
            repository_name=discovered_repo.name if discovered_repo else raw_repo,
            assigned_team_id=discovered_team.id if discovered_team else None,
            assigned_team_name=discovered_team.name if discovered_team else (raw_team or None),
            area_path=raw_area,
            iteration_path=raw_iteration,
            process_template=discovered_proc.template_name if discovered_proc else None,
            service_hook_secret_ref=str(secret_ref) if secret_ref else None,
            metadata=report_details,
        )

        is_persisted = False
        if persist:
            record, _ = self._repo.upsert_binding(
                record=record,
                changed_by=changed_by,
                action="BIND_AZURE_DEVOPS",
                details={"status": aggregate_status.value, "report": report_details},
            )
            is_persisted = True

        if aggregate_status in (ProjectBindingStatus.COMPLETE, ProjectBindingStatus.PARTIAL):
            self._emit_event(
                event_type="agent_squad.delivery.bound",
                project_id=project_id,
                payload={
                    "project_id": project_id,
                    "delivery_backend_kind": DeliveryBackendKind.AZURE_DEVOPS.value,
                    "binding_status": aggregate_status.value,
                    "team_project": discovered_tp.name,
                    "repository_name": record.repository_name,
                    "revision": record.revision,
                    "fingerprint": record.fingerprint,
                },
                emitted_collector=emitted_events,
            )
        else:
            self._emit_event(
                event_type="agent_squad.delivery.binding_blocked",
                project_id=project_id,
                payload={
                    "project_id": project_id,
                    "reason": errors_encountered,
                    "report": report_details,
                },
                emitted_collector=emitted_events,
            )

        return BindingResolutionResult(
            binding_record=record,
            status=aggregate_status,
            resource_report=report,
            is_persisted=is_persisted,
            events_emitted=emitted_events,
            errors=errors_encountered,
        )

    def get_binding(self, project_id: str) -> Optional[ProjectBindingRecord]:
        """Fetches persisted binding record from SQLite store."""
        return self._repo.get_binding(project_id)

    def audit_history(self, project_id: str) -> List[BindingHistoryRecord]:
        """Fetches append-only audit trail of binding mutations."""
        return self._repo.get_history(project_id)

    def _emit_event(
        self,
        event_type: str,
        project_id: str,
        payload: Dict[str, Any],
        emitted_collector: List[str],
    ) -> None:
        """Emits R2 domain event through SqliteEventStore if configured."""
        if self._event_store is None:
            emitted_collector.append(event_type)
            return

        try:
            from scripts.domain.events import DomainEvent
            event = DomainEvent.create(
                event_type=event_type,
                work_item_id=f"proj-{project_id}",
                project_id=project_id,
                source="delivery.binding_service",
                correlation_id=str(uuid.uuid4()),
                causation_id="delivery.bind_project",
                payload=payload,
            )
            if hasattr(self._event_store, "save_event"):
                self._event_store.save_event(event)
            elif hasattr(self._event_store, "record_event"):
                self._event_store.record_event(event)
            emitted_collector.append(event_type)
        except Exception as exc:
            logger.warning(f"Failed to emit R2 domain event {event_type}: {exc}")


# Canonical alias
DeliveryBindingManager = ProjectDeliveryBindingService
