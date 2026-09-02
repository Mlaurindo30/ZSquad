#!/usr/bin/env python3
"""Testes para McpDevOpsClient — wrapper MCP stdio."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "integrations"))
sys.path.insert(0, str(_ROOT / "scripts"))

import unittest
from unittest.mock import MagicMock, patch


class TestMcpDevOpsClientABCCompliance(unittest.TestCase):
    """Verifica que McpDevOpsClient implementa todos os métodos da ABC."""

    def test_import(self):
        try:
            from integrations.mcp_devops_client import McpDevOpsClient
            self.assertTrue(True)
        except ImportError as exc:
            self.fail(f"McpDevOpsClient não pode ser importado: {exc}")

    def test_inherits_from_base_devops_client(self):
        import integrations.devops_platform_connector as dpc
        from integrations.mcp_devops_client import McpDevOpsClient
        BaseDevOpsClient = getattr(dpc, "BaseDevOpsClient", None)
        self.assertIsNotNone(BaseDevOpsClient)
        self.assertTrue(issubclass(McpDevOpsClient, BaseDevOpsClient))

    def test_all_abc_methods_implemented(self):
        import inspect
        from integrations.mcp_devops_client import McpDevOpsClient
        from integrations.devops_platform_connector import BaseDevOpsClient

        abstract_methods = set()
        for name, method in inspect.getmembers(BaseDevOpsClient, predicate=inspect.isfunction):
            if getattr(method, "__isabstractmethod__", False):
                abstract_methods.add(name)

        for name in sorted(abstract_methods):
            self.assertTrue(
                hasattr(McpDevOpsClient, name),
                f"McpDevOpsClient falta implementar método abstrato: {name}"
            )


class TestMcpDevOpsClientFallback(unittest.TestCase):
    """Testa fallback automático para REST quando MCP não disponível."""

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_fallback_to_rest_when_npx_unavailable(self, mock_run, mock_popen):
        mock_run.return_value = MagicMock(returncode=1)

        from integrations.mcp_devops_client import McpDevOpsClient

        client = McpDevOpsClient(
            organization="test-org",
            project="test-project",
            pat_token="fake-token",
        )

        with self.assertRaises(NotImplementedError):
            client.create_project("Test Project", "Agile")


class TestMcpDevOpsClientToolMapping(unittest.TestCase):
    """Testa que os métodos delegam para as tools MCP corretas."""

    def test_tool_map_exists(self):
        from integrations.mcp_devops_client import McpDevOpsClient
        self.assertIn("pull_ready_items", McpDevOpsClient.TOOL_MAP)
        self.assertIn("update_item_state", McpDevOpsClient.TOOL_MAP)
        self.assertIn("create_pull_request", McpDevOpsClient.TOOL_MAP)

    def test_rest_only_operations(self):
        from integrations.mcp_devops_client import McpDevOpsClient
        self.assertIn("apply_iterations", McpDevOpsClient.REST_ONLY)
        self.assertIn("assign_iteration_to_team", McpDevOpsClient.REST_ONLY)
        self.assertIn("create_project", McpDevOpsClient.REST_ONLY)
        self.assertIn("import_repository", McpDevOpsClient.REST_ONLY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
