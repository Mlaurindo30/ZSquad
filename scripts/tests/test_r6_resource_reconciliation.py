"""Canonical Tests for R6 Resource Reconciliation (Section 47).

Covers:
- existing repo reused
- missing managed repo created
- missing external repo blocked
- ambiguous repo blocked
- existing team reused
- missing managed team created
- area creation under approved subtree
- iteration creation under approved subtree
- Team Project never created per product (POST /_apis/projects blocked)
- process not mutated
- repeat reconciliation idempotent
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pytest

from scripts.runtime.delivery.azure_discovery import (
    AzureDiscoveryPort,
    BoardInfo,
    ClassificationNodeInfo,
    ProcessTemplateInfo,
    RepositoryInfo,
    TeamInfo,
    TeamProjectInfo,
)
from scripts.runtime.delivery.azure_writer import AzureWriter, AzureWriterPort
from scripts.runtime.delivery.errors import (
    AmbiguousRepositoryError,
    DeliveryBindingError,
    ForbiddenResourceMutationError,
    MissingResourceError,
    TeamProjectNotFoundError,
)
from scripts.runtime.delivery.reconciliation import (
    ResourceReconciliationResult,
    ResourceReconciliationService,
)


class MockDiscovery(AzureDiscoveryPort):
    """Mock implementation of AzureDiscoveryPort for testing."""

    def __init__(
        self,
        projects: Optional[Dict[str, TeamProjectInfo]] = None,
        repositories: Optional[Dict[str, RepositoryInfo]] = None,
        teams: Optional[Dict[str, TeamInfo]] = None,
        area_nodes: Optional[Dict[str, ClassificationNodeInfo]] = None,
        iteration_nodes: Optional[Dict[str, ClassificationNodeInfo]] = None,
        process_templates: Optional[Dict[str, ProcessTemplateInfo]] = None,
        ambiguous_repos: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.projects = projects or {}
        self.repositories = repositories or {}
        self.teams = teams or {}
        self.area_nodes = area_nodes or {}
        self.iteration_nodes = iteration_nodes or {}
        self.process_templates = process_templates or {}
        self.ambiguous_repos = ambiguous_repos or {}

    def get_project(self, organization_url: str, project_name_or_id: str) -> Optional[TeamProjectInfo]:
        return self.projects.get(project_name_or_id)

    def list_projects(self, organization_url: str) -> List[TeamProjectInfo]:
        return list(self.projects.values())

    def get_repository(
        self, organization_url: str, project_id: str, repo_name_or_id: str
    ) -> Optional[RepositoryInfo]:
        if repo_name_or_id in self.ambiguous_repos:
            raise AmbiguousRepositoryError(
                query=repo_name_or_id,
                candidates=self.ambiguous_repos[repo_name_or_id],
            )
        key = f"{project_id}/{repo_name_or_id}"
        return self.repositories.get(key) or self.repositories.get(repo_name_or_id)

    def list_repositories(self, organization_url: str, project_id: str) -> List[RepositoryInfo]:
        return [r for r in self.repositories.values() if r.team_project_id == project_id]

    def get_team(
        self, organization_url: str, project_id: str, team_name_or_id: str
    ) -> Optional[TeamInfo]:
        key = f"{project_id}/{team_name_or_id}"
        return self.teams.get(key) or self.teams.get(team_name_or_id)

    def get_area_node(
        self, organization_url: str, project_id: str, area_path: str
    ) -> Optional[ClassificationNodeInfo]:
        return self.area_nodes.get(area_path)

    def get_iteration_node(
        self, organization_url: str, project_id: str, iteration_path: str
    ) -> Optional[ClassificationNodeInfo]:
        return self.iteration_nodes.get(iteration_path)

    def get_team_boards(self, organization_url: str, project_id: str, team_id: str) -> List[BoardInfo]:
        return []

    def get_process_template(self, organization_url: str, project_id: str) -> ProcessTemplateInfo:
        return self.process_templates.get(
            project_id,
            ProcessTemplateInfo(template_id="default-proc", template_name="Agile", template_type="Agile"),
        )


class MockWriter(AzureWriterPort):
    """Mock mutating adapter capturing resource provisioning calls."""

    def __init__(self) -> None:
        self.created_repositories: List[Dict[str, Any]] = []
        self.created_teams: List[Dict[str, Any]] = []
        self.created_areas: List[Dict[str, Any]] = []
        self.created_iterations: List[Dict[str, Any]] = []
        self.created_work_items: List[Dict[str, Any]] = []
        self.updated_work_items: List[Dict[str, Any]] = []
        self.created_service_hooks: List[Dict[str, Any]] = []

    def create_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        self.created_work_items.append(kwargs)
        return {"id": 101, "rev": 1, "url": "https://dev.azure.com/mock/_apis/wit/workItems/101"}

    def update_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        self.updated_work_items.append(kwargs)
        rev = (kwargs.get("expected_rev") or 1) + 1
        return {"id": kwargs.get("ado_id", 101), "rev": rev}

    def create_repository(
        self,
        organization_url: str,
        team_project_id: str,
        repo_name: str,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        if not is_managed:
            raise DeliveryBindingError(f"Repository '{repo_name}' is external", code="AZ_FORBIDDEN_EXTERNAL")
        payload = {"name": repo_name, "project_id": team_project_id}
        self.created_repositories.append(payload)
        return {"id": f"repo-{repo_name}", "name": repo_name}

    def create_team(
        self,
        organization_url: str,
        team_project_id: str,
        team_name: str,
        description: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        if not is_managed:
            raise DeliveryBindingError(f"Team '{team_name}' is external", code="AZ_FORBIDDEN_EXTERNAL")
        payload = {"name": team_name, "project_id": team_project_id, "description": description}
        self.created_teams.append(payload)
        return {"id": f"team-{team_name}", "name": team_name}

    def create_area(
        self,
        organization_url: str,
        project_name: str,
        area_name: str,
        parent_path: Optional[str] = None,
        is_managed: bool = True,
    ) -> Dict[str, Any]:
        if not is_managed:
            raise DeliveryBindingError(f"Area '{area_name}' is external", code="AZ_FORBIDDEN_EXTERNAL")
        payload = {"name": area_name, "project_name": project_name, "parent_path": parent_path}
        self.created_areas.append(payload)
        return {"name": area_name, "path": f"{parent_path}\\{area_name}" if parent_path else area_name}

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
        if not is_managed:
            raise DeliveryBindingError(f"Iteration '{iteration_name}' is external", code="AZ_FORBIDDEN_EXTERNAL")
        payload = {
            "name": iteration_name,
            "project_name": project_name,
            "parent_path": parent_path,
            "start_date": start_date,
            "finish_date": finish_date,
        }
        self.created_iterations.append(payload)
        return {"name": iteration_name, "path": f"{parent_path}\\{iteration_name}" if parent_path else iteration_name}

    def create_service_hook(self, **kwargs: Any) -> Dict[str, Any]:
        self.created_service_hooks.append(kwargs)
        return {"id": "sub-123"}


@pytest.fixture
def mock_team_project() -> TeamProjectInfo:
    return TeamProjectInfo(
        id="tp-uuid-1",
        name="Enterprise-Platform",
        state="wellFormed",
        visibility="private",
    )


def test_reconcile_existing_repo_reused(mock_team_project: TeamProjectInfo):
    """Existing Git repository in Team Project is reused and not recreated."""
    repo = RepositoryInfo(
        id="repo-uuid-1",
        name="order-service",
        team_project_id="tp-uuid-1",
        default_branch="refs/heads/main",
        remote_url="https://dev.azure.com/corp/Enterprise-Platform/_git/order-service",
    )
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        repositories={"tp-uuid-1/order-service": repo},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        repository_name="order-service",
        is_repository_managed=True,
    )

    assert result.reconciled is True
    assert "repository:order-service" in result.reused_resources
    assert len(writer.created_repositories) == 0


def test_reconcile_missing_managed_repo_created(mock_team_project: TeamProjectInfo):
    """Missing managed repository is created via writer."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        repositories={},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        repository_name="new-service",
        is_repository_managed=True,
    )

    assert result.reconciled is True
    assert "repository:new-service" in result.created_resources
    assert len(writer.created_repositories) == 1
    assert writer.created_repositories[0]["name"] == "new-service"


def test_reconcile_missing_external_repo_blocked(mock_team_project: TeamProjectInfo):
    """Missing external repository raises MissingResourceError (fails closed)."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        repositories={},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    with pytest.raises(MissingResourceError) as exc_info:
        service.reconcile_project_resources(
            organization_url="https://dev.azure.com/corp",
            team_project_name="Enterprise-Platform",
            repository_name="legacy-external-repo",
            is_repository_managed=False,
        )

    assert "legacy-external-repo" in str(exc_info.value)
    assert len(writer.created_repositories) == 0


def test_reconcile_ambiguous_repo_blocked(mock_team_project: TeamProjectInfo):
    """Ambiguous repository match raises AmbiguousRepositoryError (fails closed)."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        ambiguous_repos={"ambiguous-repo": ["repo-1", "repo-fork"]},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    with pytest.raises(AmbiguousRepositoryError) as exc_info:
        service.reconcile_project_resources(
            organization_url="https://dev.azure.com/corp",
            team_project_name="Enterprise-Platform",
            repository_name="ambiguous-repo",
            is_repository_managed=True,
        )

    assert "ambiguous-repo" in str(exc_info.value)
    assert len(writer.created_repositories) == 0


def test_reconcile_existing_team_reused(mock_team_project: TeamProjectInfo):
    """Existing team is discovered and reused without creation."""
    team = TeamInfo(id="team-uuid-1", name="OrderTeam", team_project_id="tp-uuid-1", description="Orders squad")
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        teams={"tp-uuid-1/OrderTeam": team},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        team_name="OrderTeam",
        is_team_managed=True,
    )

    assert result.reconciled is True
    assert "team:OrderTeam" in result.reused_resources
    assert len(writer.created_teams) == 0


def test_reconcile_missing_managed_team_created(mock_team_project: TeamProjectInfo):
    """Missing managed team is created via writer."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        teams={},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        team_name="BillingTeam",
        is_team_managed=True,
    )

    assert result.reconciled is True
    assert "team:BillingTeam" in result.created_resources
    assert len(writer.created_teams) == 1
    assert writer.created_teams[0]["name"] == "BillingTeam"


def test_reconcile_area_creation_under_approved_subtree(mock_team_project: TeamProjectInfo):
    """Area path creation under approved Team Project subtree succeeds."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        area_nodes={},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        area_path="Enterprise-Platform\\Orders\\Payments",
        is_area_managed=True,
    )

    assert result.reconciled is True
    assert "area:Enterprise-Platform\\Orders\\Payments" in result.created_resources
    assert len(writer.created_areas) == 1
    assert writer.created_areas[0]["name"] == "Payments"
    assert writer.created_areas[0]["parent_path"] == "Orders"


def test_reconcile_area_creation_unapproved_subtree_rejected(mock_team_project: TeamProjectInfo):
    """Area path not subordinated to Team Project raises DeliveryBindingError."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    with pytest.raises(DeliveryBindingError) as exc_info:
        service.reconcile_project_resources(
            organization_url="https://dev.azure.com/corp",
            team_project_name="Enterprise-Platform",
            area_path="UnrelatedProject\\Orders",
        )

    assert "subordinated" in str(exc_info.value).lower()
    assert len(writer.created_areas) == 0


def test_reconcile_iteration_creation_under_approved_subtree(mock_team_project: TeamProjectInfo):
    """Iteration path under approved subtree is created via writer."""
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        iteration_nodes={},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        iteration_path="Enterprise-Platform\\2026-Q3",
        is_iteration_managed=True,
    )

    assert result.reconciled is True
    assert "iteration:Enterprise-Platform\\2026-Q3" in result.created_resources
    assert len(writer.created_iterations) == 1
    assert writer.created_iterations[0]["name"] == "2026-Q3"


def test_reconcile_team_project_never_created():
    """Team Project is never created; missing Team Project raises TeamProjectNotFoundError."""
    discovery = MockDiscovery(projects={})
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    with pytest.raises(TeamProjectNotFoundError):
        service.reconcile_project_resources(
            organization_url="https://dev.azure.com/corp",
            team_project_name="NonExistentProject",
            repository_name="any-repo",
        )

    # Invariant: AzureWriter rejects create_project call
    real_writer = AzureWriter()
    with pytest.raises(ForbiddenResourceMutationError):
        real_writer.create_project()


def test_reconcile_process_not_mutated(mock_team_project: TeamProjectInfo):
    """Process template is discovered read-only and never mutated."""
    proc = ProcessTemplateInfo(
        template_id="proc-uuid-1",
        template_name="Agile",
        template_type="Agile",
    )
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        process_templates={"tp-uuid-1": proc},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    result = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
    )

    assert result.reconciled is True
    assert "process_template:Agile" in result.reused_resources
    # Verify writer has no process mutation methods
    assert not hasattr(writer, "update_process_template")
    assert not hasattr(writer, "create_process_template")


def test_reconcile_repeat_reconciliation_idempotent(mock_team_project: TeamProjectInfo):
    """Repeat reconciliation is fully idempotent: 0 creations on second run."""
    repo = RepositoryInfo(
        id="repo-1",
        name="service-a",
        team_project_id="tp-uuid-1",
        default_branch="refs/heads/main",
        remote_url="https://dev.azure.com/corp/Enterprise-Platform/_git/service-a",
    )
    team = TeamInfo(id="team-1", name="Team-A", team_project_id="tp-uuid-1")
    discovery = MockDiscovery(
        projects={"Enterprise-Platform": mock_team_project},
        repositories={"tp-uuid-1/service-a": repo},
        teams={"tp-uuid-1/Team-A": team},
    )
    writer = MockWriter()
    service = ResourceReconciliationService(discovery=discovery, writer=writer)

    # Run 1: Existing resources reused
    result1 = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        repository_name="service-a",
        team_name="Team-A",
    )
    assert result1.reconciled is True
    assert len(result1.created_resources) == 0
    assert len(result1.reused_resources) == 4  # team_project, repository, team, process_template

    # Run 2: Repeat invocation remains strictly idempotent
    result2 = service.reconcile_project_resources(
        organization_url="https://dev.azure.com/corp",
        team_project_name="Enterprise-Platform",
        repository_name="service-a",
        team_name="Team-A",
    )
    assert result2.reconciled is True
    assert len(result2.created_resources) == 0
    assert result2.reused_resources == result1.reused_resources
    assert len(writer.created_repositories) == 0
    assert len(writer.created_teams) == 0
