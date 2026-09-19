"""Authoritative Read-Only Azure DevOps Topology Discovery Adapter.

Strictly stdlib-only. ZERO MUTATIONS: creation, updates, and deletions are prohibited.
Supports injectable HTTP client or mock providers for deterministic, offline testing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from scripts.runtime.delivery.errors import (
    AmbiguousRepositoryError,
    AmbiguousTeamError,
    AzureAuthenticationError,
    AzureTimeoutError,
    AzureUnavailableError,
    DeliveryBindingError,
    TeamProjectNotFoundError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamProjectInfo:
    """Discovered Azure DevOps Team Project container metadata."""

    id: str
    name: str
    description: Optional[str] = None
    state: str = "wellFormed"
    visibility: str = "private"
    default_team_id: Optional[str] = None
    default_team_name: Optional[str] = None
    process_template_id: Optional[str] = None


@dataclass(frozen=True)
class RepositoryInfo:
    """Discovered Git repository metadata within a Team Project."""

    id: str
    name: str
    team_project_id: str
    default_branch: Optional[str] = None
    remote_url: Optional[str] = None
    size_bytes: int = 0


@dataclass(frozen=True)
class TeamInfo:
    """Discovered product / engineering team metadata within a Team Project."""

    id: str
    name: str
    team_project_id: str
    description: Optional[str] = None


@dataclass(frozen=True)
class ClassificationNodeInfo:
    """Discovered classification tree node (Area Path or Iteration Path)."""

    id: int
    name: str
    structure_type: str  # 'area' or 'iteration'
    path: str
    has_children: bool = False
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BoardInfo:
    """Discovered Kanban board metadata and columns."""

    id: str
    name: str
    team_id: str
    columns: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessTemplateInfo:
    """Discovered process template classification for a Team Project."""

    template_id: str
    template_name: str
    template_type: str  # 'Agile', 'Scrum', 'Basic', 'CMMI', 'Custom'


class AzureDiscoveryPort(ABC):
    """Authoritative, strictly read-only discovery port for Azure DevOps delivery topology."""

    @abstractmethod
    def get_project(self, organization_url: str, project_name_or_id: str) -> Optional[TeamProjectInfo]:
        """Resolves Team Project container by exact name or UUID."""
        ...

    @abstractmethod
    def list_projects(self, organization_url: str) -> List[TeamProjectInfo]:
        """Lists accessible Team Projects within the organization."""
        ...

    @abstractmethod
    def get_repository(self, organization_url: str, project_id: str, repo_name_or_id: str) -> Optional[RepositoryInfo]:
        """Discovers a Git repository within a Team Project."""
        ...

    @abstractmethod
    def list_repositories(self, organization_url: str, project_id: str) -> List[RepositoryInfo]:
        """Lists all Git repositories within the Team Project."""
        ...

    @abstractmethod
    def get_team(self, organization_url: str, project_id: str, team_name_or_id: str) -> Optional[TeamInfo]:
        """Discovers an assigned engineering team within the Team Project."""
        ...

    @abstractmethod
    def get_area_node(self, organization_url: str, project_id: str, area_path: str) -> Optional[ClassificationNodeInfo]:
        """Verifies existence of an Area Path within the Team Project classification tree."""
        ...

    @abstractmethod
    def get_iteration_node(self, organization_url: str, project_id: str, iteration_path: str) -> Optional[ClassificationNodeInfo]:
        """Verifies existence of an Iteration Path within the Team Project cadence tree."""
        ...

    @abstractmethod
    def get_team_boards(self, organization_url: str, project_id: str, team_id: str) -> List[BoardInfo]:
        """Inspects Kanban boards and columns configured for a team."""
        ...

    @abstractmethod
    def get_process_template(self, organization_url: str, project_id: str) -> ProcessTemplateInfo:
        """Discovers active process template (Agile, Scrum, Basic, CMMI, Custom)."""
        ...


# Type for custom HTTP transport callable: (url: str, headers: Dict[str, str], timeout: float) -> Dict[str, Any]
TransportCallable = Callable[[str, Dict[str, str], float], Dict[str, Any]]


class ReadOnlyAzureDiscovery(AzureDiscoveryPort):
    """Concrete, strictly read-only implementation of AzureDiscoveryPort.
    
    GUARANTEES:
    - Never issues HTTP POST, PUT, PATCH, or DELETE requests.
    - Zero plain-text tokens or credentials logged or leaked in exceptions.
    - Supports mock/injected transport for deterministic offline testing.
    """

    API_VERSION = "7.1"

    def __init__(
        self,
        pat: Optional[str] = None,
        pat_env_var: Optional[str] = "AZURE_DEVOPS_EXT_PAT",
        transport: Optional[TransportCallable] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Initializes read-only discovery.
        
        Args:
            pat: Optional explicit Personal Access Token.
            pat_env_var: Environment variable name holding PAT (default: AZURE_DEVOPS_EXT_PAT).
            transport: Injected HTTP client for mock/testing. If None, uses urllib.request.
            timeout_seconds: Request timeout in seconds.
        """
        self._pat = pat or (os.environ.get(pat_env_var) if pat_env_var else None)
        self._transport = transport
        self._timeout = timeout_seconds

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Agent-Squad-Delivery-Binding/R5",
        }
        if self._pat:
            encoded_pat = base64.b64encode(f":{self._pat}".encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded_pat}"
        return headers

    def _clean_url(self, base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        base = base_url.rstrip("/")
        clean_path = path.lstrip("/")
        full_url = f"{base}/{clean_path}"
        all_params = {"api-version": self.API_VERSION}
        if params:
            all_params.update(params)
        query = urllib.parse.urlencode(all_params)
        return f"{full_url}?{query}"

    def _http_get(self, url: str) -> Dict[str, Any]:
        """Executes strictly read-only HTTP GET request with error classification and secret redaction."""
        try:
            if self._transport is not None:
                return self._transport(url, self._get_headers(), self._timeout)

            req = urllib.request.Request(url, headers=self._get_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            # Redact authorization / sensitive info
            redacted_url = re.sub(r"://([^/@]+)@", "://", url)
            if exc.code in (401, 403):
                raise AzureAuthenticationError(
                    endpoint=redacted_url,
                    reason=f"Authentication failed (HTTP {exc.code})",
                ) from exc
            if exc.code == 404:
                # Let callers handle 404 if expected
                raise
            if exc.code >= 500:
                raise AzureUnavailableError(
                    endpoint=redacted_url,
                    reason=f"Remote server error (HTTP {exc.code})",
                ) from exc
            raise AzureUnavailableError(
                endpoint=redacted_url,
                reason=f"Unexpected HTTP {exc.code}",
            ) from exc
        except urllib.error.URLError as exc:
            redacted_url = re.sub(r"://([^/@]+)@", "://", url)
            if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
                raise AzureTimeoutError(endpoint=redacted_url, timeout_seconds=self._timeout) from exc
            raise AzureUnavailableError(endpoint=redacted_url, reason=str(exc.reason)) from exc
        except TimeoutError as exc:
            redacted_url = re.sub(r"://([^/@]+)@", "://", url)
            raise AzureTimeoutError(endpoint=redacted_url, timeout_seconds=self._timeout) from exc

    def get_project(self, organization_url: str, project_name_or_id: str) -> Optional[TeamProjectInfo]:
        """Resolves Team Project container by exact name or UUID."""
        if not project_name_or_id or not project_name_or_id.strip():
            return None
        clean_name = project_name_or_id.strip()
        url = self._clean_url(
            organization_url,
            f"_apis/projects/{urllib.parse.quote(clean_name)}",
            {"includeCapabilities": "true"},
        )
        try:
            data = self._http_get(url)
            default_team = data.get("defaultTeam", {})
            capabilities = data.get("capabilities", {})
            process_tmpl = capabilities.get("versioncontrol", {}).get("gitEnabled")
            template_id = capabilities.get("processTemplate", {}).get("templateTypeId")
            return TeamProjectInfo(
                id=data["id"],
                name=data["name"],
                description=data.get("description"),
                state=data.get("state", "wellFormed"),
                visibility=data.get("visibility", "private"),
                default_team_id=default_team.get("id"),
                default_team_name=default_team.get("name"),
                process_template_id=template_id,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def list_projects(self, organization_url: str) -> List[TeamProjectInfo]:
        """Lists accessible Team Projects in the organization."""
        url = self._clean_url(organization_url, "_apis/projects")
        data = self._http_get(url)
        items = data.get("value", [])
        results = []
        for item in items:
            results.append(
                TeamProjectInfo(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description"),
                    state=item.get("state", "wellFormed"),
                    visibility=item.get("visibility", "private"),
                )
            )
        return results

    def list_repositories(self, organization_url: str, project_id: str) -> List[RepositoryInfo]:
        """Lists all Git repositories within a Team Project."""
        url = self._clean_url(
            organization_url,
            f"{urllib.parse.quote(project_id)}/_apis/git/repositories",
        )
        data = self._http_get(url)
        items = data.get("value", [])
        repos = []
        for item in items:
            repos.append(
                RepositoryInfo(
                    id=item["id"],
                    name=item["name"],
                    team_project_id=item.get("project", {}).get("id", project_id),
                    default_branch=item.get("defaultBranch"),
                    remote_url=item.get("remoteUrl"),
                    size_bytes=item.get("size", 0),
                )
            )
        return repos

    def get_repository(self, organization_url: str, project_id: str, repo_name_or_id: str) -> Optional[RepositoryInfo]:
        """Discovers a Git repository within a Team Project. Fails closed on ambiguity."""
        if not repo_name_or_id or not repo_name_or_id.strip():
            return None
        target = repo_name_or_id.strip()
        all_repos = self.list_repositories(organization_url, project_id)

        # 1. Check exact ID match
        id_matches = [r for r in all_repos if r.id.lower() == target.lower()]
        if len(id_matches) == 1:
            return id_matches[0]

        # 2. Check exact name match (case-sensitive)
        exact_matches = [r for r in all_repos if r.name == target]
        if len(exact_matches) == 1:
            return exact_matches[0]

        # 3. Check case-insensitive match
        ci_matches = [r for r in all_repos if r.name.lower() == target.lower()]
        if len(ci_matches) == 1:
            return ci_matches[0]
        elif len(ci_matches) > 1:
            raise AmbiguousRepositoryError(
                query=target,
                candidates=[r.name for r in ci_matches],
            )

        return None

    def get_team(self, organization_url: str, project_id: str, team_name_or_id: str) -> Optional[TeamInfo]:
        """Discovers an assigned engineering team within the Team Project. Fails closed on ambiguity."""
        if not team_name_or_id or not team_name_or_id.strip():
            return None
        target = team_name_or_id.strip()
        url = self._clean_url(
            organization_url,
            f"_apis/projects/{urllib.parse.quote(project_id)}/teams",
        )
        data = self._http_get(url)
        items = data.get("value", [])
        teams = [
            TeamInfo(
                id=item["id"],
                name=item["name"],
                team_project_id=project_id,
                description=item.get("description"),
            )
            for item in items
        ]

        # 1. Exact ID match
        id_matches = [t for t in teams if t.id.lower() == target.lower()]
        if len(id_matches) == 1:
            return id_matches[0]

        # 2. Exact name match
        exact_matches = [t for t in teams if t.name == target]
        if len(exact_matches) == 1:
            return exact_matches[0]

        # 3. Case-insensitive match
        ci_matches = [t for t in teams if t.name.lower() == target.lower()]
        if len(ci_matches) == 1:
            return ci_matches[0]
        elif len(ci_matches) > 1:
            raise AmbiguousTeamError(
                query=target,
                candidates=[t.name for t in ci_matches],
            )

        return None

    def get_area_node(self, organization_url: str, project_id: str, area_path: str) -> Optional[ClassificationNodeInfo]:
        """Verifies existence of an Area Path within the Team Project classification tree."""
        if not area_path or not area_path.strip():
            return None
        clean_path = area_path.strip().replace("/", "\\")
        # Sub-path relative to Project name
        parts = clean_path.split("\\")
        # If path starts with project name, skip root
        relative_parts = [urllib.parse.quote(p) for p in parts[1:]] if len(parts) > 1 else []
        relative_subpath = "/".join(relative_parts)
        endpoint_path = f"{urllib.parse.quote(project_id)}/_apis/wit/classificationnodes/Areas"
        if relative_subpath:
            endpoint_path = f"{endpoint_path}/{relative_subpath}"

        url = self._clean_url(
            organization_url,
            endpoint_path,
            {"$depth": "1"},
        )
        try:
            data = self._http_get(url)
            return ClassificationNodeInfo(
                id=data["id"],
                name=data["name"],
                structure_type="area",
                path=clean_path,
                has_children=data.get("hasChildren", False),
                attributes=data.get("attributes", {}),
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def get_iteration_node(self, organization_url: str, project_id: str, iteration_path: str) -> Optional[ClassificationNodeInfo]:
        """Verifies existence of an Iteration Path within the Team Project cadence tree."""
        if not iteration_path or not iteration_path.strip():
            return None
        clean_path = iteration_path.strip().replace("/", "\\")
        parts = clean_path.split("\\")
        relative_parts = [urllib.parse.quote(p) for p in parts[1:]] if len(parts) > 1 else []
        relative_subpath = "/".join(relative_parts)
        endpoint_path = f"{urllib.parse.quote(project_id)}/_apis/wit/classificationnodes/Iterations"
        if relative_subpath:
            endpoint_path = f"{endpoint_path}/{relative_subpath}"

        url = self._clean_url(
            organization_url,
            endpoint_path,
            {"$depth": "1"},
        )
        try:
            data = self._http_get(url)
            return ClassificationNodeInfo(
                id=data["id"],
                name=data["name"],
                structure_type="iteration",
                path=clean_path,
                has_children=data.get("hasChildren", False),
                attributes=data.get("attributes", {}),
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def get_team_boards(self, organization_url: str, project_id: str, team_id: str) -> List[BoardInfo]:
        """Inspects Kanban boards and columns configured for a team."""
        url = self._clean_url(
            organization_url,
            f"{urllib.parse.quote(project_id)}/{urllib.parse.quote(team_id)}/_apis/work/boards",
        )
        try:
            data = self._http_get(url)
            boards_raw = data.get("value", [])
            boards = []
            for b in boards_raw:
                boards.append(
                    BoardInfo(
                        id=b["id"],
                        name=b["name"],
                        team_id=team_id,
                        columns=b.get("columns", []),
                    )
                )
            return boards
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise

    def get_process_template(self, organization_url: str, project_id: str) -> ProcessTemplateInfo:
        """Discovers the active Process Template (Agile, Scrum, Basic, CMMI, Custom)."""
        url = self._clean_url(
            organization_url,
            f"_apis/projects/{urllib.parse.quote(project_id)}/properties",
            {"keys": "System.ProcessTemplateType"},
        )
        template_id = "unknown"
        template_name = "Agile"
        template_type = "Agile"

        try:
            data = self._http_get(url)
            values = data.get("value", [])
            for prop in values:
                if prop.get("name") == "System.ProcessTemplateType":
                    template_id = str(prop.get("value", ""))
                    break
        except Exception as _e:
            logger.debug(f"Process template properties query returned: {_e}")

        # Well-known Azure DevOps Process Template GUIDs
        KNOWN_TEMPLATES = {
            "adcc421a-4da4-4470-8481-944c6883e33e": ("Agile", "Agile"),
            "6b724908-ef14-45cf-84f8-768b5384da45": ("Scrum", "Scrum"),
            "27450541-8e31-4150-9947-dc59f9987a19": ("CMMI", "CMMI"),
            "b8a3a935-7e91-48b8-a94c-606d37c3e9f2": ("Basic", "Basic"),
        }
        if template_id.lower() in KNOWN_TEMPLATES:
            template_name, template_type = KNOWN_TEMPLATES[template_id.lower()]
        elif template_id != "unknown":
            template_name = "Custom / Inherited"
            template_type = "Custom"

        return ProcessTemplateInfo(
            template_id=template_id,
            template_name=template_name,
            template_type=template_type,
        )
