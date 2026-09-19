"""Canonical Tests for R5 Delivery Binding Domain Events and R2 Outbox Integration.

Validates:
- Deterministic emission of domain events:
  - agent_squad.project.resolved
  - agent_squad.delivery.bound
  - agent_squad.delivery.binding_blocked
- Full integration with SqliteEventStore (transactional outbox)
- Correct population of DomainEvent envelope:
  - event_type, project_id, work_item_id, source="delivery.binding_service", causation_id, payload
- Fallback / resilient collection in BindingResolutionResult.events_emitted when event_store=None
"""

from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.runtime.delivery.azure_discovery import ReadOnlyAzureDiscovery
from scripts.runtime.delivery.binding import (
    BindingResolutionResult,
    ProjectBindingStatus,
    ProjectDeliveryBindingService,
)
from scripts.runtime.delivery.repository import SqliteBindingRepository
from scripts.runtime.events.store import SqliteEventStore


@pytest.fixture
def test_env():
    """Provides an isolated project directory, binding repository, and SQLite event store."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        repo = SqliteBindingRepository(":memory:")
        event_store = SqliteEventStore(":memory:")
        service = ProjectDeliveryBindingService(repository=repo, event_store=event_store)
        yield tmp_path, service, event_store
        repo.close()


def test_emit_events_local_only_success(test_env):
    """Verifies project.resolved and delivery.bound events emitted for LOCAL_ONLY binding."""
    project_dir, service, event_store = test_env
    cfg_dir = project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "local-app", "name": "Local Application"},
        "delivery": {"backend": "LOCAL_ONLY"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = service.bind_project(project_dir, persist=True)

    assert result.status == ProjectBindingStatus.COMPLETE
    assert "agent_squad.project.resolved" in result.events_emitted
    assert "agent_squad.delivery.bound" in result.events_emitted

    # Verify events in SqliteEventStore
    stored_events = event_store.list_events_by_work_item("proj-local-app")
    assert len(stored_events) == 2

    # 1. Project Resolved Event
    e1 = stored_events[0]
    assert e1.event_type == "agent_squad.project.resolved"
    assert e1.project_id == "local-app"
    assert e1.source == "delivery.binding_service"
    assert e1.causation_id == "delivery.bind_project"
    assert e1.payload["project_id"] == "local-app"
    assert e1.payload["display_name"] == "Local Application"
    assert "resolved_at" in e1.payload

    # 2. Delivery Bound Event
    e2 = stored_events[1]
    assert e2.event_type == "agent_squad.delivery.bound"
    assert e2.project_id == "local-app"
    assert e2.payload["delivery_backend_kind"] == "LOCAL_ONLY"
    assert e2.payload["binding_status"] == "COMPLETE"
    assert e2.payload["revision"] == 1
    assert "fingerprint" in e2.payload


def test_emit_events_mock_success(test_env):
    """Verifies project.resolved and delivery.bound events emitted for MOCK binding."""
    project_dir, service, event_store = test_env
    cfg_dir = project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "mock-app", "name": "Mock Application"},
        "delivery": {"backend": "MOCK"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = service.bind_project(project_dir, persist=True)

    assert result.status == ProjectBindingStatus.COMPLETE
    assert "agent_squad.project.resolved" in result.events_emitted
    assert "agent_squad.delivery.bound" in result.events_emitted

    stored_events = event_store.list_events_by_work_item("proj-mock-app")
    assert len(stored_events) == 2
    assert stored_events[1].payload["delivery_backend_kind"] == "MOCK"


def test_emit_events_binding_blocked_when_unconfigured(test_env):
    """Verifies agent_squad.delivery.binding_blocked event emitted when azure_devops block is missing."""
    project_dir, service, event_store = test_env
    cfg_dir = project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "blocked-app", "name": "Blocked Application"},
        "delivery": {"backend": "AZURE_DEVOPS"},
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = service.bind_project(project_dir, persist=True)

    assert result.status == ProjectBindingStatus.NOT_CONFIGURED
    assert "agent_squad.project.resolved" in result.events_emitted
    assert "agent_squad.delivery.binding_blocked" in result.events_emitted

    stored_events = event_store.list_events_by_work_item("proj-blocked-app")
    assert len(stored_events) == 2
    blocked_event = stored_events[1]
    assert blocked_event.event_type == "agent_squad.delivery.binding_blocked"
    assert "Missing azure_devops configuration" in str(blocked_event.payload.get("reason"))


def test_emit_events_binding_blocked_when_team_project_missing(test_env):
    """Verifies agent_squad.delivery.binding_blocked event emitted when remote Team Project cannot be found."""
    project_dir, service, event_store = test_env
    cfg_dir = project_dir / ".agents_squad" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "project": {"id": "missing-tp-app", "name": "Missing TP App"},
        "delivery": {
            "backend": "AZURE_DEVOPS",
            "azure_devops": {
                "organization_url": "https://dev.azure.com/enterprise-org",
                "team_project": "NonExistentTeamProject",
            },
        },
    }
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    # Mock discovery returning None for get_project (404)
    mock_discovery = ReadOnlyAzureDiscovery(
        transport=lambda url, h, t: (_ for _ in ()).throw(FileNotFoundError())
    )
    service._discovery = mock_discovery

    result = service.bind_project(project_dir, persist=True)

    assert result.status == ProjectBindingStatus.BLOCKED
    assert "agent_squad.delivery.binding_blocked" in result.events_emitted

    stored_events = event_store.list_events_by_work_item("proj-missing-tp-app")
    assert len(stored_events) == 2
    assert stored_events[1].event_type == "agent_squad.delivery.binding_blocked"


def test_events_resilience_when_event_store_is_none():
    """Verifies that binding service functions smoothly and collects events even without SqliteEventStore."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / ".agents_squad" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project": {"id": "no-store-app", "name": "No Store App"},
            "delivery": {"backend": "LOCAL_ONLY"},
        }
        (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

        repo = SqliteBindingRepository(":memory:")
        service = ProjectDeliveryBindingService(repository=repo, event_store=None)
        try:
            result = service.bind_project(tmp_path, persist=True)
            assert result.status == ProjectBindingStatus.COMPLETE
            assert result.events_emitted == ["agent_squad.project.resolved", "agent_squad.delivery.bound"]
        finally:
            repo.close()
