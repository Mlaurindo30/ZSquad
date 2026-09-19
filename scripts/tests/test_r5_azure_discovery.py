"""Canonical Tests for R5 Read-Only Azure DevOps Topology Discovery.

Validates:
- Strictly READ-ONLY operations (zero POST, PUT, PATCH, DELETE or resource creation)
- Deterministic discovery with injected mock HTTP transport
- Team Project resolution (by exact name or UUID, 404 handling, empty query handling)
- Team Projects listing (multiple project containers)
- Repository discovery (exact ID, exact name, case-insensitive, AmbiguousRepositoryError fail-closed)
- Repository listing (metadata mapping: branch, size, remoteUrl)
- Team discovery (exact ID, exact name, case-insensitive, AmbiguousTeamError fail-closed)
- Classification nodes discovery: Area Path and Iteration Path resolution
- Team Kanban boards and columns inspection
- Process template classification (Agile, Scrum, CMMI, Basic, Custom)
- Fail-closed error classification:
  - AzureAuthenticationError (HTTP 401/403)
  - AzureUnavailableError (HTTP 500/503)
  - AzureTimeoutError (HTTP timeout / socket timeout)
- Credential isolation and PAT authorization header construction
"""

import json
from typing import Any, Dict
import urllib.error
import pytest

from scripts.runtime.delivery.azure_discovery import (
    BoardInfo,
    ClassificationNodeInfo,
    ProcessTemplateInfo,
    ReadOnlyAzureDiscovery,
    RepositoryInfo,
    TeamInfo,
    TeamProjectInfo,
)
from scripts.runtime.delivery.errors import (
    AmbiguousRepositoryError,
    AmbiguousTeamError,
    AzureAuthenticationError,
    AzureTimeoutError,
    AzureUnavailableError,
)

ORG_URL = "https://dev.azure.com/enterprise-org"
PROJECT_ID = "00000000-0000-0000-0000-000000000001"
PROJECT_NAME = "Core-Banking"


def build_mock_transport(routes: Dict[str, Any]):
    """Creates a mock TransportCallable routing requested URLs to mock responses or exceptions."""
    def transport(url: str, headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
        # Enforce that no mutating headers or methods exist
        assert headers.get("Accept") == "application/json"
        assert "Agent-Squad" in headers.get("User-Agent", "")

        for pattern, response in routes.items():
            if pattern in url:
                if isinstance(response, Exception):
                    raise response
                if callable(response):
                    return response(url, headers)
                return response
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return transport


def test_discovery_strictly_read_only_methods():
    """Validates that ReadOnlyAzureDiscovery interface only implements read-only query methods."""
    discovery = ReadOnlyAzureDiscovery()
    prohibited_prefixes = ("create", "add", "post", "put", "patch", "delete", "remove", "update", "modify")

    for attr_name in dir(discovery):
        if not attr_name.startswith("_"):
            for prefix in prohibited_prefixes:
                assert not attr_name.startswith(prefix), f"Prohibited mutating method found: {attr_name}"


def test_get_project_success():
    """Verifies successful retrieval of Team Project container."""
    routes = {
        "_apis/projects/Core-Banking": {
            "id": PROJECT_ID,
            "name": PROJECT_NAME,
            "description": "Core Banking Enterprise Container",
            "state": "wellFormed",
            "visibility": "private",
            "defaultTeam": {"id": "team-001", "name": "Core-Banking Team"},
            "capabilities": {
                "processTemplate": {"templateTypeId": "adcc421a-4da4-4470-8481-944c6883e33e"}
            },
        }
    }
    discovery = ReadOnlyAzureDiscovery(pat="dummy-pat", transport=build_mock_transport(routes))
    project = discovery.get_project(ORG_URL, "Core-Banking")

    assert isinstance(project, TeamProjectInfo)
    assert project.id == PROJECT_ID
    assert project.name == PROJECT_NAME
    assert project.default_team_id == "team-001"
    assert project.default_team_name == "Core-Banking Team"
    assert project.process_template_id == "adcc421a-4da4-4470-8481-944c6883e33e"


def test_get_project_not_found_returns_none():
    """Verifies that 404 HTTP response gracefully returns None for missing Team Project."""
    routes = {}
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    project = discovery.get_project(ORG_URL, "NonExistentProject")
    assert project is None


def test_get_project_empty_or_whitespace_returns_none():
    """Verifies that empty queries return None without issuing HTTP request."""
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport({}))
    assert discovery.get_project(ORG_URL, "") is None
    assert discovery.get_project(ORG_URL, "   ") is None


def test_list_projects_success():
    """Verifies listing accessible Team Projects in organization."""
    routes = {
        "_apis/projects": {
            "value": [
                {"id": "p1", "name": "Project-Alpha", "description": "Desc A", "state": "wellFormed", "visibility": "private"},
                {"id": "p2", "name": "Project-Beta", "description": "Desc B", "state": "wellFormed", "visibility": "public"},
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    projects = discovery.list_projects(ORG_URL)

    assert len(projects) == 2
    assert projects[0].id == "p1"
    assert projects[0].name == "Project-Alpha"
    assert projects[1].id == "p2"
    assert projects[1].visibility == "public"


def test_list_repositories_success():
    """Verifies listing all Git repositories within a Team Project."""
    routes = {
        "_apis/git/repositories": {
            "value": [
                {
                    "id": "repo-1",
                    "name": "payment-gateway",
                    "project": {"id": PROJECT_ID},
                    "defaultBranch": "refs/heads/main",
                    "remoteUrl": "https://dev.azure.com/org/proj/_git/payment-gateway",
                    "size": 1048576,
                },
                {
                    "id": "repo-2",
                    "name": "account-service",
                    "project": {"id": PROJECT_ID},
                    "defaultBranch": "refs/heads/master",
                    "remoteUrl": "https://dev.azure.com/org/proj/_git/account-service",
                    "size": 2097152,
                },
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    repos = discovery.list_repositories(ORG_URL, PROJECT_ID)

    assert len(repos) == 2
    assert repos[0].id == "repo-1"
    assert repos[0].name == "payment-gateway"
    assert repos[0].default_branch == "refs/heads/main"
    assert repos[0].size_bytes == 1048576


def test_get_repository_exact_id_and_name_matching():
    """Verifies repository resolution by exact ID and by exact Name."""
    routes = {
        "_apis/git/repositories": {
            "value": [
                {"id": "repo-uuid-1", "name": "service-alpha", "project": {"id": PROJECT_ID}},
                {"id": "repo-uuid-2", "name": "service-beta", "project": {"id": PROJECT_ID}},
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    # Match by exact ID
    repo_by_id = discovery.get_repository(ORG_URL, PROJECT_ID, "repo-uuid-1")
    assert repo_by_id is not None
    assert repo_by_id.name == "service-alpha"

    # Match by exact name
    repo_by_name = discovery.get_repository(ORG_URL, PROJECT_ID, "service-beta")
    assert repo_by_name is not None
    assert repo_by_name.id == "repo-uuid-2"

    # Case-insensitive single match
    repo_ci = discovery.get_repository(ORG_URL, PROJECT_ID, "SERVICE-ALPHA")
    assert repo_ci is not None
    assert repo_ci.id == "repo-uuid-1"


def test_get_repository_ambiguous_fails_closed():
    """Verifies that multiple repositories matching case-insensitively raise AmbiguousRepositoryError."""
    routes = {
        "_apis/git/repositories": {
            "value": [
                {"id": "r1", "name": "billing-service", "project": {"id": PROJECT_ID}},
                {"id": "r2", "name": "Billing-Service", "project": {"id": PROJECT_ID}},
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    with pytest.raises(AmbiguousRepositoryError) as exc_info:
        discovery.get_repository(ORG_URL, PROJECT_ID, "BILLING-SERVICE")
    assert "Ambiguous Repository" in str(exc_info.value)
    assert exc_info.value.code == "AZ_409_AMBIG"
    assert len(exc_info.value.candidates) == 2


def test_get_team_exact_and_ambiguous_resolution():
    """Verifies engineering team discovery with exact match and ambiguous fail-closed behavior."""
    routes = {
        "_apis/projects/00000000-0000-0000-0000-000000000001/teams": {
            "value": [
                {"id": "team-id-1", "name": "Payments Squad", "description": "Payments team"},
                {"id": "team-id-2", "name": "payments squad", "description": "Duplicate team"},
                {"id": "team-id-3", "name": "Accounts Squad", "description": "Accounts team"},
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    # Match exact ID
    t1 = discovery.get_team(ORG_URL, PROJECT_ID, "team-id-3")
    assert t1 is not None
    assert t1.name == "Accounts Squad"

    # Match exact name
    t2 = discovery.get_team(ORG_URL, PROJECT_ID, "Accounts Squad")
    assert t2 is not None
    assert t2.id == "team-id-3"

    # Ambiguity case-insensitive
    with pytest.raises(AmbiguousTeamError) as exc_info:
        discovery.get_team(ORG_URL, PROJECT_ID, "PAYMENTS SQUAD")
    assert "Ambiguous Team" in str(exc_info.value)
    assert exc_info.value.code == "AZ_409_AMBIG"


def test_get_area_node_resolution():
    """Verifies Area Path classification tree node discovery."""
    routes = {
        "_apis/wit/classificationnodes/Areas/Payments/Gateway": {
            "id": 1024,
            "name": "Gateway",
            "hasChildren": False,
            "attributes": {},
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    node = discovery.get_area_node(ORG_URL, PROJECT_ID, "Core-Banking\\Payments\\Gateway")

    assert isinstance(node, ClassificationNodeInfo)
    assert node.id == 1024
    assert node.name == "Gateway"
    assert node.structure_type == "area"


def test_get_iteration_node_resolution():
    """Verifies Iteration Path cadence node discovery."""
    routes = {
        "_apis/wit/classificationnodes/Iterations/2026-Q3/Sprint-1": {
            "id": 2048,
            "name": "Sprint-1",
            "hasChildren": False,
            "attributes": {"startDate": "2026-07-01T00:00:00Z"},
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    node = discovery.get_iteration_node(ORG_URL, PROJECT_ID, "Core-Banking\\2026-Q3\\Sprint-1")

    assert isinstance(node, ClassificationNodeInfo)
    assert node.id == 2048
    assert node.name == "Sprint-1"
    assert node.structure_type == "iteration"


def test_get_team_boards():
    """Verifies inspection of team Kanban boards and columns."""
    routes = {
        "_apis/work/boards": {
            "value": [
                {
                    "id": "board-1",
                    "name": "Stories",
                    "columns": [
                        {"id": "c1", "name": "Backlog", "isSplit": False},
                        {"id": "c2", "name": "Active", "isSplit": True},
                        {"id": "c3", "name": "Resolved", "isSplit": False},
                        {"id": "c4", "name": "Closed", "isSplit": False},
                    ],
                }
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    boards = discovery.get_team_boards(ORG_URL, PROJECT_ID, "team-001")

    assert len(boards) == 1
    assert boards[0].id == "board-1"
    assert boards[0].name == "Stories"
    assert len(boards[0].columns) == 4


@pytest.mark.parametrize("guid,expected_name,expected_type", [
    ("adcc421a-4da4-4470-8481-944c6883e33e", "Agile", "Agile"),
    ("6b724908-ef14-45cf-84f8-768b5384da45", "Scrum", "Scrum"),
    ("27450541-8e31-4150-9947-dc59f9987a19", "CMMI", "CMMI"),
    ("b8a3a935-7e91-48b8-a94c-606d37c3e9f2", "Basic", "Basic"),
    ("custom-guid-9999-9999-9999-999999999999", "Custom / Inherited", "Custom"),
])
def test_get_process_template_classification(guid, expected_name, expected_type):
    """Verifies process template mapping against canonical templates."""
    routes = {
        "_apis/projects/00000000-0000-0000-0000-000000000001/properties": {
            "value": [
                {"name": "System.ProcessTemplateType", "value": guid}
            ]
        }
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))
    tmpl = discovery.get_process_template(ORG_URL, PROJECT_ID)

    assert isinstance(tmpl, ProcessTemplateInfo)
    assert tmpl.template_id == guid
    assert tmpl.template_name == expected_name
    assert tmpl.template_type == expected_type


def test_authentication_error_fail_closed():
    """HTTP 401/403 triggers AzureAuthenticationError."""
    routes = {
        "_apis/projects": urllib.error.HTTPError("https://dev.azure.com", 401, "Unauthorized", {}, None)
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    with pytest.raises(AzureAuthenticationError) as exc_info:
        discovery.list_projects(ORG_URL)
    assert exc_info.value.code == "AZ_401_AUTH"


def test_unavailable_error_fail_closed():
    """HTTP 500/503 triggers AzureUnavailableError."""
    routes = {
        "_apis/projects": urllib.error.HTTPError("https://dev.azure.com", 503, "Service Unavailable", {}, None)
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    with pytest.raises(AzureUnavailableError) as exc_info:
        discovery.list_projects(ORG_URL)
    assert exc_info.value.code == "AZ_503"


def test_timeout_error_fail_closed():
    """Network TimeoutError triggers AzureTimeoutError."""
    routes = {
        "_apis/projects": TimeoutError("Connection timed out")
    }
    discovery = ReadOnlyAzureDiscovery(transport=build_mock_transport(routes))

    with pytest.raises(AzureTimeoutError) as exc_info:
        discovery.list_projects(ORG_URL)
    assert exc_info.value.code == "AZ_TIMEOUT"


def test_pat_authorization_header_sanitization():
    """Verifies that PAT is encoded into standard Basic auth header and not exposed in plain text."""
    secret_pat = "my-secret-pat-12345"
    discovery = ReadOnlyAzureDiscovery(pat=secret_pat)
    headers = discovery._get_headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")
    # Must NOT contain plain text secret PAT
    assert secret_pat not in headers["Authorization"]
