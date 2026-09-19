"""Canonical Tests for R5 Local Project Resolution and Delivery Binding.

Validates:
- Deterministic resolution of local project root via .agents_squad/config/project.yaml
- Compatibility fallback to .squad/project.yaml
- Ancestor directory traversal from nested subdirectories or files
- Strict enforcement of PathContainmentViolationError for project-local './work'
- Rejection of missing declarative configuration (ProjectConfigNotFoundError)
- Rejection of invalid YAML or non-dict configuration root
- Validation of project_id grammar and strict rejection of FORBIDDEN_LEGACY_TOKENS
- Supported backend resolution: LOCAL_ONLY, AZURE_DEVOPS, MOCK
- DeliveryBindingManager alias consistency
- Deterministic BindingResolutionResult metadata and status propagation
"""

from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.project import DeliveryBackendKind, FORBIDDEN_LEGACY_TOKENS
from scripts.runtime.delivery.binding import (
    BindingResolutionResult,
    DeliveryBindingManager,
    ProjectBindingStatus,
    ProjectDeliveryBindingService,
    ResourceBindingStatus,
)
from scripts.runtime.delivery.errors import (
    DeliveryBackendNotConfiguredError,
    DeliveryBindingError,
    InvalidBindingConfigurationError,
    PathContainmentViolationError,
    ProjectConfigNotFoundError,
)
from scripts.runtime.delivery.repository import SqliteBindingRepository


@pytest.fixture
def temp_project_dir():
    """Creates a temporary project root directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp).resolve()


@pytest.fixture
def binding_service():
    """Provides an isolated ProjectDeliveryBindingService with in-memory persistence."""
    repo = SqliteBindingRepository(":memory:")
    service = ProjectDeliveryBindingService(repository=repo)
    yield service
    repo.close()


def test_resolve_local_project_standard_path(temp_project_dir, binding_service):
    """Verifies resolution via standard .agents_squad/config/project.yaml."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "project.yaml"
    cfg_content = {
        "version": "1.0",
        "project": {
            "id": "payments-core",
            "name": "Payments Core Service",
        },
        "delivery": {
            "backend": "LOCAL_ONLY",
        },
    }
    cfg_file.write_text(yaml.safe_dump(cfg_content), encoding="utf-8")

    root, config = binding_service.resolve_local_project(temp_project_dir)
    assert root == temp_project_dir
    assert config["project"]["id"] == "payments-core"
    assert config["delivery"]["backend"] == "LOCAL_ONLY"


def test_resolve_local_project_fallback_squad_path(temp_project_dir, binding_service):
    """Verifies compatibility fallback to .squad/project.yaml."""
    cfg_dir = temp_project_dir / ".squad"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "project.yaml"
    cfg_content = {
        "version": "1.0",
        "project": {
            "id": "legacy-adapter",
            "name": "Legacy Adapter",
        },
        "delivery": {
            "backend": "LOCAL_ONLY",
        },
    }
    cfg_file.write_text(yaml.safe_dump(cfg_content), encoding="utf-8")

    root, config = binding_service.resolve_local_project(temp_project_dir)
    assert root == temp_project_dir
    assert config["project"]["id"] == "legacy-adapter"


def test_resolve_local_project_ancestor_traversal(temp_project_dir, binding_service):
    """Verifies project discovery when resolving from deep nested directory or file."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "project.yaml"
    cfg_content = {
        "project": {"id": "deep-service", "name": "Deep Service"},
        "delivery": {"backend": "LOCAL_ONLY"},
    }
    cfg_file.write_text(yaml.safe_dump(cfg_content), encoding="utf-8")

    nested_sub = temp_project_dir / "src" / "api" / "v1" / "handlers"
    nested_sub.mkdir(parents=True, exist_ok=True)
    dummy_file = nested_sub / "handler.py"
    dummy_file.write_text("# code", encoding="utf-8")

    # Resolve from nested directory
    root1, _ = binding_service.resolve_local_project(nested_sub)
    assert root1 == temp_project_dir

    # Resolve from nested file
    root2, _ = binding_service.resolve_local_project(dummy_file)
    assert root2 == temp_project_dir


def test_resolve_local_project_missing_config_fails_closed(temp_project_dir, binding_service):
    """Verifies ProjectConfigNotFoundError when no declarative config exists in hierarchy."""
    with pytest.raises(ProjectConfigNotFoundError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert "not found" in str(exc_info.value).lower()
    assert exc_info.value.code == "PROJ_001"


def test_resolve_local_project_path_containment_violation_for_local_work(temp_project_dir, binding_service):
    """Detects and blocks project-local './work' directory to prevent state fragmentation."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "project.yaml").write_text("project: {id: test-p}\n", encoding="utf-8")

    # Create illegal project-local work directory
    illegal_work = temp_project_dir / "work"
    illegal_work.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PathContainmentViolationError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert "escapes governed boundary" in str(exc_info.value)
    assert exc_info.value.code == "SEC_001"


def test_resolve_local_project_invalid_yaml(temp_project_dir, binding_service):
    """Rejects malformed YAML declarative configuration."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "project.yaml").write_text("invalid: [yaml: broken: {", encoding="utf-8")

    with pytest.raises(InvalidBindingConfigurationError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert exc_info.value.code == "BIND_INVALID_CONF"


def test_resolve_local_project_non_dict_config(temp_project_dir, binding_service):
    """Rejects declarative configuration that does not parse to a dictionary/mapping."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "project.yaml").write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(InvalidBindingConfigurationError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert "must be a YAML mapping" in str(exc_info.value)


def test_resolve_local_project_missing_project_id(temp_project_dir, binding_service):
    """Rejects configuration lacking project.id or project_id."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "project.yaml").write_text("project:\n  name: 'No ID Here'\n", encoding="utf-8")

    with pytest.raises(InvalidBindingConfigurationError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert "missing 'project.id'" in str(exc_info.value)


@pytest.mark.parametrize("invalid_id", [
    "-invalid-start",
    ".invalid-dot-start",
    "invalid space in id",
    "invalid@character",
    "invalid/slash",
    "invalid\\backslash",
    "a" * 129,  # exceeds 128 chars
])
def test_resolve_local_project_invalid_id_grammar(temp_project_dir, binding_service, invalid_id):
    """Rejects project_id that violates grammar rules."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"project": {"id": invalid_id, "name": "Valid Name"}}
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    with pytest.raises(InvalidBindingConfigurationError) as exc_info:
        binding_service.resolve_local_project(temp_project_dir)
    assert "grammar" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()


@pytest.mark.parametrize("token", FORBIDDEN_LEGACY_TOKENS)
def test_resolve_local_project_forbidden_legacy_tokens(temp_project_dir, binding_service, token):
    """Rejects project_id and display_name containing legacy tokens."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Test in project_id
    cfg1 = {"project": {"id": f"proj-{token}-service", "name": "Valid Name"}}
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg1), encoding="utf-8")
    with pytest.raises(InvalidBindingConfigurationError):
        binding_service.resolve_local_project(temp_project_dir)

    # Test in display_name
    cfg2 = {"project": {"id": "valid-proj-id", "name": f"Service with {token}"}}
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg2), encoding="utf-8")
    with pytest.raises(InvalidBindingConfigurationError):
        binding_service.resolve_local_project(temp_project_dir)


def test_bind_local_only_backend(temp_project_dir, binding_service):
    """Verifies bind_project with LOCAL_ONLY backend kind."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "inventory-svc", "name": "Inventory Service"},
        "delivery": {"backend": "LOCAL_ONLY"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = binding_service.bind_project(temp_project_dir, persist=True)

    assert isinstance(result, BindingResolutionResult)
    assert result.status == ProjectBindingStatus.COMPLETE
    assert result.is_persisted is True
    assert result.binding_record.project_id == "inventory-svc"
    assert result.binding_record.delivery_backend_kind == DeliveryBackendKind.LOCAL_ONLY.value
    assert result.binding_record.delivery_binding_ref == "local://inventory-svc"
    assert result.binding_record.is_governed is True
    assert result.binding_record.revision == 1
    assert result.resource_report.team_project == ResourceBindingStatus.NOT_CONFIGURED

    # Check persistence in repo
    persisted = binding_service.get_binding("inventory-svc")
    assert persisted is not None
    assert persisted.fingerprint == result.binding_record.fingerprint


def test_bind_mock_backend(temp_project_dir, binding_service):
    """Verifies bind_project with MOCK backend kind for simulated delivery."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "sandbox-svc", "name": "Sandbox Service"},
        "delivery": {"backend": "MOCK"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = binding_service.bind_project(temp_project_dir, persist=True)

    assert result.status == ProjectBindingStatus.COMPLETE
    assert result.binding_record.delivery_backend_kind == DeliveryBackendKind.MOCK.value
    assert result.binding_record.delivery_binding_ref == "mock://sandbox-svc"
    assert result.resource_report.team_project == ResourceBindingStatus.RESOLVED
    assert result.resource_report.repository == ResourceBindingStatus.RESOLVED
    assert result.resource_report.assigned_team == ResourceBindingStatus.RESOLVED


def test_bind_unsupported_backend_kind(temp_project_dir, binding_service):
    """Rejects unknown/unsupported delivery backend kind."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "unknown-backend-svc", "name": "Unknown"},
        "delivery": {"backend": "JIRA_CLOUD"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    with pytest.raises(InvalidBindingConfigurationError) as exc_info:
        binding_service.bind_project(temp_project_dir)
    assert "unsupported delivery backend kind" in str(exc_info.value).lower()


def test_bind_azure_devops_unconfigured_fails_closed(temp_project_dir, binding_service):
    """Fails closed when AZURE_DEVOPS is declared without azure_devops configuration block."""
    cfg_dir = temp_project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "unconfigured-ado-svc", "name": "Unconfigured ADO"},
        "delivery": {"backend": "AZURE_DEVOPS"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = binding_service.bind_project(temp_project_dir, persist=True)

    assert result.status == ProjectBindingStatus.NOT_CONFIGURED
    assert result.binding_record.binding_status == ProjectBindingStatus.NOT_CONFIGURED.value
    assert len(result.errors) > 0
    assert "agent_squad.delivery.binding_blocked" in result.events_emitted


def test_delivery_binding_manager_alias_parity(temp_project_dir):
    """Verifies that DeliveryBindingManager is a canonical alias of ProjectDeliveryBindingService."""
    assert DeliveryBindingManager is ProjectDeliveryBindingService
    manager = DeliveryBindingManager(repository=SqliteBindingRepository(":memory:"))
    assert hasattr(manager, "resolve_local_project")
    assert hasattr(manager, "bind_project")
    assert hasattr(manager, "get_binding")
    manager.repository.close()
