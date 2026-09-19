"""Authoritative Mutating Azure DevOps Adapter.

Strictly stdlib-only.
Executes mutating operations on Azure Boards, Repos, and Service Hooks:
- Work item creation (with typed process templates, tags, acceptance criteria, hierarchy-reverse parent links)
- Work item update (with optimistic concurrency test on /rev, 412 conflict trapping)
- Managed repository creation (only when MANAGED and absent)
- Managed team creation (only when MANAGED and absent)
- Managed classification nodes (Area and Iteration paths)
- Service Hook subscription registration (with duplicate prevention)

ABSOLUTE PROHIBITIONS:
- ZERO creation of corporate Team Projects ('POST /_apis/projects' is strictly prohibited).
- ZERO storage or logging of PATs / secrets (SEC-R1-01).
- Supports mock / injected transport for deterministic offline tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Union
import urllib.error
import urllib.parse
import urllib.request

from scripts.domain.common import canonical_json
from scripts.runtime.delivery.errors import (
    AzureAuthenticationError,
    AzureTimeoutError,
    AzureUnavailableError,
    DeliveryBindingError,
    ForbiddenResourceMutationError,
    OptimisticConcurrencyError,
)
from scripts.runtime.delivery.repository import sanitize_credentials

logger = logging.getLogger(__name__)

# TransportCallable: (method: str, url: str, headers: Dict[str, str], body: Optional[Any], timeout: float) -> Dict[str, Any]
TransportCallable = Callable[[str, str, Dict[str, str], Optional[Any], float], Dict[str, Any]]


class AzureWriterPort(ABC):
    """Port interface governing mutations against Azure DevOps."""

    @abstractmethod
    def create_work_item(
        self,
        organization_url: str,
        project_name: str,
        work_item_type: str,
        title: str,
        description: Optional[str] = None,
        area_path: Optional[str] = None,
        iteration_path: Optional[str] = None,
        story_points: Optional[Union[int, float]] = None,
        acceptance_criteria: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_ado_id: Optional[int] = None,
        parent_comment: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Creates a work item with tags, criteria, and parent linkage."""
        ...

    @abstractmethod
    def update_work_item(
        self,
        organization_url: str,
        project_name: str,
        ado_id: int,
        expected_rev: Optional[int] = None,
        state: Optional[str] = None,
        board_column: Optional[str] = None,
        history_comment: Optional[str] = None,
        tags: Optional[List[str]] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Updates work item fields with optimistic concurrency revision test."""
        ...

    @abstractmethod
    def create_repository(
        self,
        organization_url: str,
        team_project_id: str,
        repo_name: str,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates a Git repository within an existing Team Project (MANAGED only)."""
        ...

    @abstractmethod
    def create_team(
        self,
        organization_url: str,
        team_project_id: str,
        team_name: str,
        description: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates a product engineering team within the Team Project (MANAGED only)."""
        ...

    @abstractmethod
    def create_area(
        self,
        organization_url: str,
        project_name: str,
        area_name: str,
        parent_path: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates an Area Path classification node under product subtree."""
        ...

    @abstractmethod
    def create_iteration(
        self,
        organization_url: str,
        project_name: str,
        iteration_name: str,
        parent_path: Optional[str] = None,
        start_date: Optional[str] = None,
        finish_date: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates an Iteration Path cadence node under product subtree."""
        ...

    @abstractmethod
    def create_service_hook(
        self,
        organization_url: str,
        publisher_id: str,
        event_type: str,
        consumer_id: str,
        consumer_action_id: str,
        publisher_inputs: Dict[str, Any],
        consumer_inputs: Dict[str, Any],
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Registers a Service Hook subscription with duplicate suppression."""
        ...


class AzureWriter(AzureWriterPort):
    """Concrete production Azure DevOps mutating client.

    Guarantees:
    - Never implements or calls create_project.
    - Zero plain-text credentials logged or leaked.
    - Supports mock/injected transport for deterministic offline tests.
    """

    API_VERSION = "7.1"

    def __init__(
        self,
        pat: Optional[str] = None,
        pat_env_var: Optional[str] = "AZURE_DEVOPS_EXT_PAT",
        transport: Optional[TransportCallable] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._pat = pat
        self._pat_env_var = pat_env_var
        self._custom_transport = transport
        self.timeout_seconds = timeout_seconds

    def __getattr__(self, name: str) -> Any:
        if name == "create_project":
            raise ForbiddenResourceMutationError("TeamProject", action="create")
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _resolve_pat(self) -> str:
        if self._pat and self._pat.strip():
            return self._pat.strip()
        if self._pat_env_var:
            env_val = os.environ.get(self._pat_env_var)
            if env_val and env_val.strip():
                return env_val.strip()
        fallback = os.environ.get("AZURE_DEVOPS_PAT")
        if fallback and fallback.strip():
            return fallback.strip()
        return ""

    def _build_auth_headers(self, content_type: str = "application/json") -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "Agent-Squad/1.0 (DeliverySyncService)",
        }
        pat = self._resolve_pat()
        if pat:
            encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        return headers

    def _execute_http(
        self,
        method: str,
        url: str,
        body: Optional[Any] = None,
        content_type: str = "application/json",
    ) -> Dict[str, Any]:
        headers = self._build_auth_headers(content_type=content_type)
        if self._custom_transport is not None:
            return self._custom_transport(method, url, headers, body, self.timeout_seconds)

        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            elif isinstance(body, bytes):
                data = body

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        sanitized_url = sanitize_credentials(url)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                content = resp.read().decode("utf-8")
                if not content.strip():
                    return {}
                return json.loads(content)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            # Optimistic concurrency conflict detection
            if exc.code == 412 or (exc.code == 400 and ("rev" in err_body.lower() or "tf400898" in err_body.lower())):
                raise OptimisticConcurrencyError(
                    work_item_id=url.split("/")[-1].split("?")[0],
                    ado_id=0,
                    expected_rev=0,
                    details={"http_code": exc.code, "response_body": err_body},
                )
            if exc.code in (401, 403):
                raise AzureAuthenticationError(
                    endpoint=sanitized_url,
                    reason=f"HTTP {exc.code}: {err_body}",
                    details={"status_code": exc.code},
                )
            if exc.code in (500, 502, 503, 504):
                raise AzureUnavailableError(
                    endpoint=sanitized_url,
                    reason=f"HTTP {exc.code}: {err_body}",
                    details={"status_code": exc.code},
                )
            raise DeliveryBindingError(
                f"Azure DevOps mutation failed HTTP {exc.code} at {sanitized_url}: {err_body}",
                code=f"AZ_HTTP_{exc.code}",
                details={"status_code": exc.code, "response_body": err_body},
            )
        except urllib.error.URLError as exc:
            reason_str = str(exc.reason)
            if "timed out" in reason_str.lower():
                raise AzureTimeoutError(sanitized_url, self.timeout_seconds)
            raise AzureUnavailableError(sanitized_url, reason=reason_str)

    def create_work_item(
        self,
        organization_url: str,
        project_name: str,
        work_item_type: str,
        title: str,
        description: Optional[str] = None,
        area_path: Optional[str] = None,
        iteration_path: Optional[str] = None,
        story_points: Optional[Union[int, float]] = None,
        acceptance_criteria: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_ado_id: Optional[int] = None,
        parent_comment: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Creates a work item using JSON-patch format."""
        patch_operations: List[Dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title", "value": title}
        ]

        if description:
            patch_operations.append({"op": "add", "path": "/fields/System.Description", "value": description})
        if area_path:
            patch_operations.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
        if iteration_path:
            patch_operations.append({"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path})
        if story_points is not None:
            patch_operations.append({"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.StoryPoints", "value": story_points})
        if acceptance_criteria:
            patch_operations.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": acceptance_criteria})
        if tags:
            tag_str = "; ".join(tags) if isinstance(tags, list) else str(tags)
            patch_operations.append({"op": "add", "path": "/fields/System.Tags", "value": tag_str})

        if fields:
            for k, v in fields.items():
                field_path = k if k.startswith("/fields/") else f"/fields/{k}"
                patch_operations.append({"op": "add", "path": field_path, "value": v})

        # Directional Hierarchy-Reverse link (Child -> Parent)
        if parent_ado_id is not None and parent_ado_id > 0:
            org_norm = organization_url.rstrip("/")
            proj_encoded = urllib.parse.quote(project_name)
            parent_url = f"{org_norm}/{proj_encoded}/_apis/wit/workItems/{parent_ado_id}"
            patch_operations.append({
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": parent_url,
                    "attributes": {
                        "comment": parent_comment or f"Canonical parent link #{parent_ado_id}"
                    },
                },
            })

        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(project_name)
        type_encoded = urllib.parse.quote(f"${work_item_type}")
        url = f"{org_norm}/{proj_encoded}/_apis/wit/workitems/{type_encoded}?api-version={self.API_VERSION}"

        return self._execute_http("POST", url, body=patch_operations, content_type="application/json-patch+json")

    def update_work_item(
        self,
        organization_url: str,
        project_name: str,
        ado_id: int,
        expected_rev: Optional[int] = None,
        state: Optional[str] = None,
        board_column: Optional[str] = None,
        history_comment: Optional[str] = None,
        tags: Optional[List[str]] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Updates work item fields with optimistic concurrency test."""
        patch_operations: List[Dict[str, Any]] = []

        # Optimistic concurrency revision check
        if expected_rev is not None:
            patch_operations.append({
                "op": "test",
                "path": "/rev",
                "value": int(expected_rev),
            })

        if state:
            patch_operations.append({"op": "add", "path": "/fields/System.State", "value": state})
        if board_column:
            patch_operations.append({"op": "add", "path": "/fields/System.BoardColumn", "value": board_column})
        if history_comment:
            patch_operations.append({"op": "add", "path": "/fields/System.History", "value": history_comment})
        if tags:
            tag_str = "; ".join(tags) if isinstance(tags, list) else str(tags)
            patch_operations.append({"op": "add", "path": "/fields/System.Tags", "value": tag_str})

        if fields:
            for k, v in fields.items():
                field_path = k if k.startswith("/fields/") else f"/fields/{k}"
                patch_operations.append({"op": "add", "path": field_path, "value": v})

        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(project_name)
        url = f"{org_norm}/{proj_encoded}/_apis/wit/workitems/{ado_id}?api-version={self.API_VERSION}"

        try:
            return self._execute_http("PATCH", url, body=patch_operations, content_type="application/json-patch+json")
        except OptimisticConcurrencyError as oce:
            # Enrich with actual ado_id and expected_rev
            raise OptimisticConcurrencyError(
                work_item_id=str(ado_id),
                ado_id=ado_id,
                expected_rev=expected_rev or 0,
                details=oce.details,
            )

    def create_repository(
        self,
        organization_url: str,
        team_project_id: str,
        repo_name: str,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Provisions a Git repository inside the specified Team Project (MANAGED only)."""
        if not is_managed:
            raise DeliveryBindingError(
                f"Repository '{repo_name}' creation rejected: management mode is EXTERNAL",
                code="AZ_FORBIDDEN_EXTERNAL",
            )
        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(team_project_id)
        url = f"{org_norm}/{proj_encoded}/_apis/git/repositories?api-version={self.API_VERSION}"
        payload = {
            "name": repo_name,
            "project": {"id": team_project_id},
        }
        return self._execute_http("POST", url, body=payload)

    def create_team(
        self,
        organization_url: str,
        team_project_id: str,
        team_name: str,
        description: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Provisions an engineering team within the Team Project (MANAGED only)."""
        if not is_managed:
            raise DeliveryBindingError(
                f"Team '{team_name}' creation rejected: management mode is EXTERNAL",
                code="AZ_FORBIDDEN_EXTERNAL",
            )
        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(team_project_id)
        url = f"{org_norm}/_apis/projects/{proj_encoded}/teams?api-version={self.API_VERSION}"
        payload = {
            "name": team_name,
            "description": description or "",
        }
        return self._execute_http("POST", url, body=payload)

    def create_area(
        self,
        organization_url: str,
        project_name: str,
        area_name: str,
        parent_path: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates an Area Path node under the product classification tree."""
        if not is_managed:
            raise DeliveryBindingError(
                f"Area path '{area_name}' creation rejected: management mode is EXTERNAL",
                code="AZ_FORBIDDEN_EXTERNAL",
            )
        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(project_name)
        subpath = f"/{urllib.parse.quote(parent_path.strip('/'))}" if parent_path else ""
        url = f"{org_norm}/{proj_encoded}/_apis/wit/classificationnodes/areas{subpath}?api-version={self.API_VERSION}"
        payload = {"name": area_name}
        return self._execute_http("POST", url, body=payload)

    def create_iteration(
        self,
        organization_url: str,
        project_name: str,
        iteration_name: str,
        parent_path: Optional[str] = None,
        start_date: Optional[str] = None,
        finish_date: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Creates an Iteration Path node under the product cadence tree."""
        if not is_managed:
            raise DeliveryBindingError(
                f"Iteration path '{iteration_name}' creation rejected: management mode is EXTERNAL",
                code="AZ_FORBIDDEN_EXTERNAL",
            )
        org_norm = organization_url.rstrip("/")
        proj_encoded = urllib.parse.quote(project_name)
        subpath = f"/{urllib.parse.quote(parent_path.strip('/'))}" if parent_path else ""
        url = f"{org_norm}/{proj_encoded}/_apis/wit/classificationnodes/iterations{subpath}?api-version={self.API_VERSION}"
        payload: Dict[str, Any] = {"name": iteration_name}
        if start_date or finish_date:
            attrs: Dict[str, Any] = {}
            if start_date:
                attrs["startDate"] = start_date
            if finish_date:
                attrs["finishDate"] = finish_date
            payload["attributes"] = attrs
        return self._execute_http("POST", url, body=payload)

    def list_service_hooks(self, organization_url: str) -> List[Dict[str, Any]]:
        """Queries existing Service Hook subscriptions to prevent duplicate registrations."""
        org_norm = organization_url.rstrip("/")
        url = f"{org_norm}/_apis/hooks/subscriptions?api-version={self.API_VERSION}"
        try:
            resp = self._execute_http("GET", url)
            return resp.get("value", [])
        except Exception as exc:
            logger.warning(f"Could not query existing Service Hooks: {exc}")
            return []

    def create_service_hook(
        self,
        organization_url: str,
        publisher_id: str,
        event_type: str,
        consumer_id: str,
        consumer_action_id: str,
        publisher_inputs: Dict[str, Any],
        consumer_inputs: Dict[str, Any],
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        """Registers a Service Hook subscription, validating against duplicates first."""
        if not is_managed:
            raise DeliveryBindingError(
                "Service Hook registration rejected: management mode is EXTERNAL",
                code="AZ_FORBIDDEN_EXTERNAL",
            )

        # Duplicate check
        existing = self.list_service_hooks(organization_url)
        target_url = consumer_inputs.get("url", "").rstrip("/")
        for sub in existing:
            if (
                sub.get("publisherId") == publisher_id
                and sub.get("eventType") == event_type
                and sub.get("consumerId") == consumer_id
            ):
                sub_url = (sub.get("consumerInputs") or {}).get("url", "").rstrip("/")
                if target_url and sub_url == target_url:
                    logger.info(f"Service Hook subscription already exists: {sub.get('id')}")
                    return sub

        org_norm = organization_url.rstrip("/")
        url = f"{org_norm}/_apis/hooks/subscriptions?api-version={self.API_VERSION}"
        payload = {
            "publisherId": publisher_id,
            "eventType": event_type,
            "consumerId": consumer_id,
            "consumerActionId": consumer_action_id,
            "publisherInputs": publisher_inputs,
            "consumerInputs": consumer_inputs,
        }
        return self._execute_http("POST", url, body=payload)
