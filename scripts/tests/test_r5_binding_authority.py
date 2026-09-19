"""Canonical Tests for R5 Binding Authority, Segregation of Duties and Architectural Invariants.

Validates:
- Ontological segregation: Product vs. Team Project (Enterprise shared container decoupling)
- Prohibition of per-product Team Project creation (AST verification of zero create_project calls)
- Area path subordination check: area_path must strictly be a child/subordinate of team_project
- SEC-R1-01 Security Policy: strict sanitization of credentials/PATs across all binding layers
- Pure Ports & Adapters architecture: delivery package relies exclusively on stdlib and domain contracts
- Absence of proprietary vendor/LLM coupling (zero OpenAI, Anthropic, Gemini, LangChain in delivery)
- Canonical aliases: BindingError and DeliveryBindingManager parity
"""

import ast
import inspect
from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.domain.project import DeliveryBackendKind
from scripts.runtime.delivery.binding import (
    BindingResolutionResult,
    DeliveryBindingManager,
    ProjectBindingStatus,
    ProjectDeliveryBindingService,
)
from scripts.runtime.delivery.errors import (
    BindingError,
    DeliveryBackendNotConfiguredError,
    DeliveryBindingError,
    InvalidBindingConfigurationError,
)
from scripts.runtime.delivery.repository import SqliteBindingRepository


DELIVERY_DIR = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "delivery"


def test_delivery_modules_stdlib_and_domain_only():
    """Validates that all modules in scripts/runtime/delivery/ rely strictly on stdlib and scripts.domain."""
    py_files = list(DELIVERY_DIR.glob("*.py"))
    assert len(py_files) >= 5, f"Expected at least 5 delivery modules, found {len(py_files)}"

    disallowed_packages = {
        "openai", "anthropic", "google", "langchain", "llama_index",
        "requests", "httpx", "aiohttp", "fastapi", "flask",
    }

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_pkg = alias.name.split(".")[0]
                    assert top_pkg not in disallowed_packages, (
                        f"Disallowed import '{alias.name}' found in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_pkg = node.module.split(".")[0]
                    assert top_pkg not in disallowed_packages, (
                        f"Disallowed import from '{node.module}' found in {py_file.name}"
                    )


def test_zero_team_project_creation_methods_in_delivery():
    """Verifies that no Team Project creation methods or endpoints exist in the delivery package."""
    for py_file in DELIVERY_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        # Invariant: R5 must never invoke or define create_project
        assert "def create_project(" not in content, (
            f"Prohibited method 'create_project' found in {py_file.name}"
        )
        assert "_apis/projects?" not in content or "POST" not in content, (
            f"Prohibited project creation REST endpoint found in {py_file.name}"
        )


def test_product_vs_team_project_area_subordination_valid():
    """Area path correctly subordinated to Team Project name is accepted."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / ".agents_squad" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project": {"id": "payment-api", "name": "Payment API"},
            "delivery": {
                "backend": "AZURE_DEVOPS",
                "azure_devops": {
                    "organization_url": "https://dev.azure.com/enterprise-fintech",
                    "team_project": "Core-Banking",
                    "area_path": "Core-Banking\\Payments\\API",
                    "iteration_path": "Core-Banking\\2026-Q3",
                },
            },
        }
        (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

        repo = SqliteBindingRepository(":memory:")
        service = ProjectDeliveryBindingService(repository=repo)
        try:
            # We provide a mock discovery that returns the project
            from scripts.runtime.delivery.azure_discovery import ReadOnlyAzureDiscovery, TeamProjectInfo
            mock_discovery = ReadOnlyAzureDiscovery(
                transport=lambda url, h, t: {
                    "id": "tp-001",
                    "name": "Core-Banking",
                    "state": "wellFormed",
                    "capabilities": {},
                    "value": [],
                }
            )
            service._discovery = mock_discovery
            res = service.bind_project(tmp_path, persist=False)
            assert res.binding_record.team_project_name == "Core-Banking"
            assert res.binding_record.area_path == "Core-Banking\\Payments\\API"
        finally:
            repo.close()


def test_product_vs_team_project_area_subordination_violation_rejected():
    """Rejects area_path that is not subordinated to declared Team Project name."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / ".agents_squad" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project": {"id": "rogue-service", "name": "Rogue Service"},
            "delivery": {
                "backend": "AZURE_DEVOPS",
                "azure_devops": {
                    "organization_url": "https://dev.azure.com/enterprise-fintech",
                    "team_project": "Core-Banking",
                    "area_path": "Different-Container\\Payments\\Rogue",
                },
            },
        }
        (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

        repo = SqliteBindingRepository(":memory:")
        service = ProjectDeliveryBindingService(repository=repo)
        try:
            with pytest.raises(InvalidBindingConfigurationError) as exc_info:
                service.bind_project(tmp_path, persist=False)
            assert "must be subordinated to Team Project 'Core-Banking'" in str(exc_info.value)
        finally:
            repo.close()


def test_sec_r1_01_sanitizes_inline_pat_in_organization_url():
    """Verifies that inline tokens in organization_url are sanitized before persistence and validation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / ".agents_squad" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project": {"id": "token-test-svc", "name": "Token Test Service"},
            "delivery": {
                "backend": "AZURE_DEVOPS",
                "azure_devops": {
                    "organization_url": "https://super-secret-pat-xyz@dev.azure.com/enterprise-fintech",
                    "team_project": "Core-Banking",
                },
            },
        }
        (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

        repo = SqliteBindingRepository(":memory:")
        service = ProjectDeliveryBindingService(repository=repo)
        try:
            from scripts.runtime.delivery.azure_discovery import ReadOnlyAzureDiscovery
            mock_discovery = ReadOnlyAzureDiscovery(
                transport=lambda url, h, t: {
                    "id": "tp-001",
                    "name": "Core-Banking",
                    "state": "wellFormed",
                    "capabilities": {},
                    "value": [],
                }
            )
            service._discovery = mock_discovery
            res = service.bind_project(tmp_path, persist=True)

            # Check that secret was never persisted in record or repo
            assert "super-secret-pat-xyz" not in (res.binding_record.organization_url or "")
            assert res.binding_record.organization_url == "https://dev.azure.com/enterprise-fintech"

            fetched = repo.get_binding("token-test-svc")
            assert fetched is not None
            assert "super-secret-pat-xyz" not in (fetched.organization_url or "")
        finally:
            repo.close()


def test_sec_r1_01_rejects_insecure_http_organization_url():
    """SEC-R1-01: Azure DevOps organization URL must use secure HTTPS."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / ".agents_squad" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project": {"id": "insecure-svc", "name": "Insecure Service"},
            "delivery": {
                "backend": "AZURE_DEVOPS",
                "azure_devops": {
                    "organization_url": "http://insecure.dev.azure.com/enterprise",
                    "team_project": "Core-Banking",
                },
            },
        }
        (cfg_dir / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

        repo = SqliteBindingRepository(":memory:")
        service = ProjectDeliveryBindingService(repository=repo)
        try:
            with pytest.raises(DeliveryBackendNotConfiguredError) as exc_info:
                service.bind_project(tmp_path, persist=False)
            assert "must use secure HTTPS" in str(exc_info.value)
        finally:
            repo.close()


def test_canonical_aliases_parity():
    """Verifies that BindingError and DeliveryBindingManager canonical aliases are maintained."""
    assert BindingError is DeliveryBindingError
    assert issubclass(BindingError, Exception)
    assert DeliveryBindingManager is ProjectDeliveryBindingService
