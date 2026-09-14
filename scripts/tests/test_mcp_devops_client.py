#!/usr/bin/env python3
"""Testes para McpDevOpsClient — wrapper MCP stdio."""

import json
import queue
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "integrations"))
sys.path.insert(0, str(_ROOT / "scripts"))

import unittest
from unittest.mock import MagicMock, patch


class _FakeStdin:
    def __init__(self, server):
        self._server = server
        self.messages = []

    def write(self, data):
        message = json.loads(data.decode("utf-8"))
        self.messages.append(message)
        self._server.receive(message)
        return len(data)

    def flush(self):
        return None


class _FakeStdout:
    def __init__(self):
        self._lines = queue.Queue()

    def readline(self):
        return self._lines.get()

    def send(self, message):
        self._lines.put(json.dumps(message).encode("utf-8") + b"\n")

    def eof(self):
        self._lines.put(b"")


class _FakeMcpServer:
    """Strict in-memory MCP server used to prove the client-side stdio contract."""

    def __init__(self, tool_error=False):
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self)
        self.stderr = MagicMock()
        self.pid = 1234
        self._initialized = False
        self._tool_error = tool_error

    def receive(self, message):
        method = message["method"]
        if method == "initialize":
            assert message["jsonrpc"] == "2.0"
            assert "id" in message
            assert "protocolVersion" in message["params"]
            self.stdout.send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake"},
                },
            })
        elif method == "notifications/initialized":
            assert "id" not in message
            self._initialized = True
        elif method == "tools/list":
            assert self._initialized
            self.stdout.send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": [
                    {"name": "wit_work_item_write"},
                    {"name": "repo_pull_request_write"},
                ]},
            })
        elif method == "tools/call":
            assert self._initialized
            assert set(message["params"]) == {"name", "arguments"}
            if self._tool_error:
                self.stdout.send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32000, "message": "tool failed"},
                })
            else:
                self.stdout.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
                self.stdout.send({
                    "jsonrpc": "2.0",
                    "id": message["id"] + 1000,
                    "result": {"wrong": True},
                })
                self.stdout.send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"ok": True},
                })
        else:
            raise AssertionError(f"arbitrary MCP method rejected: {method}")

    def terminate(self):
        self.stdout.eof()

    def wait(self, timeout=None):
        return 0


def _make_client_without_mcp(**kwargs):
    from integrations.mcp_devops_client import McpDevOpsClient
    project = kwargs.pop("project", "test-project")
    with patch.object(McpDevOpsClient, "_start_mcp_server"):
        return McpDevOpsClient(
            organization="test-org",
            project=project,
            pat_token="fake-token",
            **kwargs,
        )


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

    def test_fallback_to_rest_when_mcp_is_unavailable(self):
        client = _make_client_without_mcp()

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


class TestMcpDevOpsClientMethods(unittest.TestCase):
    """Testa métodos específicos corrigidos na Frente 1."""

    def test_pull_ready_items_area_clause(self):
        client = _make_client_without_mcp(config={"area_path": "Arthemis\\agent-squad"})
        captured = {}

        def fake_call(tool_name, arguments):
            captured["tool"] = tool_name
            captured["arguments"] = arguments
            return {"workItems": []}

        client._call_mcp_tool = fake_call
        client.pull_ready_items()

        self.assertEqual("pull_ready_items", captured["tool"])
        self.assertIn("[System.AreaPath] UNDER 'Arthemis\\agent-squad'", captured["arguments"]["wiql"])

    def test_apply_iterations_delegation(self):
        client = _make_client_without_mcp(config={"team": "agent-squad"})

        with patch("integrations.mcp_devops_client.AzureDevOpsProjectSetup") as mock_setup_cls:
            instance = mock_setup_cls.return_value
            instance.apply_iterations.return_value = None
            instance.results = [{"step": "iterations", "status": "ok"}]

            res = client.apply_iterations([{"name": "Sprint 1"}])
            mock_setup_cls.assert_called_once()
            args, kwargs = mock_setup_cls.call_args
            self.assertEqual(args[1]["iterations"], [{"name": "Sprint 1"}])
            self.assertEqual(args[1]["team"], "agent-squad")
            self.assertTrue(res)

    def test_assign_iteration_to_team_delegation(self):
        client = _make_client_without_mcp()

        with patch("integrations.mcp_devops_client.AzureDevOpsProjectSetup") as mock_setup_cls:
            instance = mock_setup_cls.return_value
            instance.assign_iteration_to_team.return_value = True

            res = client.assign_iteration_to_team("team-1", "iter-1")
            instance.assign_iteration_to_team.assert_called_once_with("team-1", "iter-1")
            self.assertTrue(res)

    def test_get_project_info_url_quote(self):
        client = _make_client_without_mcp(project="Arthemis Project")

        client._ensure_rest_client()
        with patch.object(client.rest_client, "_request", return_value={"id": "proj-id"}) as mock_req:
            info = client.get_project_info()
            mock_req.assert_called_once()
            url = mock_req.call_args[0][1]
            self.assertIn("Arthemis%20Project", url)
            self.assertEqual(info["id"], "proj-id")


class TestMcpDevOpsClientProtocol(unittest.TestCase):
    def _client_with_server(self, server):
        with patch("integrations.mcp_devops_client.subprocess.Popen", return_value=server):
            from integrations.mcp_devops_client import McpDevOpsClient
            return McpDevOpsClient("test-org", "test-project", "fake-token")

    def test_negotiates_lifecycle_discovers_tools_and_calls_mapped_tool(self):
        server = _FakeMcpServer()
        client = self._client_with_server(server)

        result = client._call_mcp_tool("update_item_state", {"id": 7})

        self.assertEqual({"ok": True}, result)
        methods = [message["method"] for message in server.stdin.messages]
        self.assertEqual(
            ["initialize", "notifications/initialized", "tools/list", "tools/call"],
            methods,
        )
        self.assertEqual(
            "wit_work_item_write",
            server.stdin.messages[-1]["params"]["name"],
        )

    def test_mcp_error_falls_back_to_rest_client(self):
        server = _FakeMcpServer(tool_error=True)
        rest_client = MagicMock()
        rest_client.update_item_state.return_value = True
        client = self._client_with_server(server)
        client.rest_client = rest_client

        self.assertTrue(client.update_item_state("7", "Active"))
        rest_client.update_item_state.assert_called_once_with("7", "Active", None)

    def test_operation_arguments_use_discovered_azure_devops_tool_contract(self):
        server = _FakeMcpServer()
        client = self._client_with_server(server)

        client.update_item_state("7", "Active")

        arguments = server.stdin.messages[-1]["params"]["arguments"]
        self.assertEqual("update", arguments["action"])
        self.assertEqual("test-project", arguments["project"])
        self.assertEqual(7, arguments["id"])

    def test_eof_during_initialization_disables_mcp(self):
        server = _FakeMcpServer()
        server.receive = lambda message: server.stdout.eof()

        client = self._client_with_server(server)

        self.assertIsNone(client._process)


if __name__ == "__main__":
    unittest.main(verbosity=2)
