#!/usr/bin/env python3
"""McpDevOpsClient — wrapper MCP stdio JSON-RPC para @azure-devops/mcp.

Transport: subprocess stdio JSON-RPC (não HTTP SSE).
Fallback: se MCP falhar ou não suportar operation, delega para rest_client (AzureDevOpsClient REST).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger("McpDevOpsClient")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from integrations.devops_platform_connector import BaseDevOpsClient, DevOpsWorkItem
from azure_devops_project_setup import AzureDevOpsProjectSetup


class McpDevOpsClient(BaseDevOpsClient):
    """Wrapper MCP para Azure DevOps via @azure-devops/mcp stdio JSON-RPC.
    
    Fallback automático para rest_client (AzureDevOpsClient) quando:
    - MCP tool não existe para a operação
    - MCP timeout (30s)
    - MCP retorna erro
    """

    TOOL_MAP = {
        "pull_ready_items": "wit_query",
        "update_item_state": "wit_work_item_write",
        "create_work_item": "wit_work_item_write",
        "create_pull_request": "repo_pull_request_write",
        "get_team_settings": "work",
        "get_project_info": "list_projects",
    }

    REST_ONLY = {"apply_iterations", "assign_iteration_to_team", "create_project", "import_repository"}
    MCP_PROTOCOL_VERSION = "2025-06-18"
    MCP_CLIENT_INFO = {"name": "agent-squad", "version": "1.0"}
    MCP_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        organization: str,
        project: str,
        pat_token: str,
        config: Optional[dict[str, Any]] = None,
        rest_client: Optional[Any] = None,
    ):
        self.organization = organization
        self.project = project
        self.pat_token = pat_token
        self.config = config or {}
        self.rest_client = rest_client
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._available_tools: Optional[set[str]] = None
        self._start_mcp_server()

    def _mcp_binary_available(self) -> bool:
        try:
            result = subprocess.run(
                ["npx", "--yes", "@azure-devops/mcp@latest", "--help"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _start_mcp_server(self) -> None:
        # MCP Auth Fix (Lote 5.1): MCP v2.x exige PAT via ADO_MCP_AUTH_TOKEN com
        # --authentication envvar; a organização é passada como NOME, não URL.
        env = {
            **os.environ,
            "ADO_MCP_AUTH_TOKEN": self.pat_token,
        }
        try:
            self._process = subprocess.Popen(
                ["npx", "--yes", "@azure-devops/mcp@latest", self.organization, "--authentication", "envvar"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            logger.info("[McpDevOpsClient] MCP server started (PID=%s)", self._process.pid)
            if not self._initialize_mcp_session():
                self._stop_mcp_server()
        except Exception as exc:
            logger.warning("[McpDevOpsClient] Falha ao iniciar MCP server: %s. Usando REST only.", exc)
            self._process = None

    def _stop_mcp_server(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            pass

    def _next_request_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _read_response(self, request_id: int) -> Optional[dict[str, Any]]:
        if self._process is None or self._process.stdout is None:
            return None

        response_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                response_queue.put(self._process.stdout.readline())
            except Exception:
                response_queue.put(None)

        reader = threading.Thread(target=read_line, daemon=True)
        reader.start()
        try:
            line = response_queue.get(timeout=self.MCP_TIMEOUT_SECONDS)
        except queue.Empty:
            logger.warning("[McpDevOpsClient] Timeout waiting for MCP response id=%s", request_id)
            return None
        if not line:
            logger.warning("[McpDevOpsClient] MCP server closed stdout while waiting for id=%s", request_id)
            return None
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._read_response(request_id)
        if "id" not in response or response.get("id") != request_id:
            return self._read_response(request_id)
        if "error" in response:
            logger.warning("[McpDevOpsClient] MCP error id=%s: %s", request_id, response["error"])
            return None
        return response.get("result")

    def _send_request(self, method: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        if self._process is None:
            return None
        req_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        try:
            if self._process.stdin is None:
                return None
            self._process.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            return self._read_response(req_id)
        except Exception as exc:
            logger.warning("[McpDevOpsClient] MCP request failed: %s → %s", method, exc)
            return None

    def _send_notification(self, method: str, params: dict[str, Any]) -> bool:
        if self._process is None or self._process.stdin is None:
            return False
        try:
            self._process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            return True
        except Exception as exc:
            logger.warning("[McpDevOpsClient] MCP notification failed: %s → %s", method, exc)
            return False

    def _initialize_mcp_session(self) -> bool:
        initialized = self._send_request("initialize", {
            "protocolVersion": self.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": self.MCP_CLIENT_INFO,
        })
        if initialized is None:
            return False
        if not self._send_notification("notifications/initialized", {}):
            return False
        tools_response = self._send_request("tools/list", {})
        if tools_response is None:
            return False
        tools = tools_response.get("tools")
        if not isinstance(tools, list):
            return False
        self._available_tools = {
            tool["name"] for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        return True

    def _call_mcp_tool(self, operation: str, arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
        tool_name = self.TOOL_MAP.get(operation, operation)
        if self._process is None or self._available_tools is None or tool_name not in self._available_tools:
            return None
        return self._send_request("tools/call", {"name": tool_name, "arguments": arguments})

    def _ensure_rest_client(self) -> Optional[Any]:
        if self.rest_client is not None:
            return self.rest_client
        from integrations.devops_platform_connector import AzureDevOpsClient
        self.rest_client = AzureDevOpsClient(
            organization=self.organization,
            project=self.project,
            pat_token=self.pat_token,
            config=self.config,
        )
        return self.rest_client

    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        area_path = self.config.get("area_path")
        area_clause = f" AND [System.AreaPath] UNDER '{area_path}'" if area_path else ""
        wiql_query = f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '{self.project}' AND [System.State] IN ('New', 'Active'){area_clause}"
        result = self._call_mcp_tool("pull_ready_items", {
            "action": "wiql",
            "project": self.project,
            "wiql": wiql_query,
        })
        if result is None:
            rc = self._ensure_rest_client()
            return rc.pull_ready_items(squad_name) if rc else []
        items = []
        for ref in result.get("workItems", []):
            items.append(DevOpsWorkItem(
                id=str(ref["id"]),
                title=ref.get("title", ""),
                description="",
                state="Active",
            ))
        return items

    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        result = self._call_mcp_tool("update_item_state", {
            "action": "update",
            "project": self.project,
            "id": int(item_id),
            "fields": {"System.State": new_state},
        })
        if result is None:
            rc = self._ensure_rest_client()
            return rc.update_item_state(item_id, new_state, comment) if rc else False
        return bool(result)

    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        fields = {}
        if story_points is not None:
            fields["Microsoft.VSTS.Scheduling.StoryPoints"] = story_points
        if t_shirt_size is not None:
            fields["Microsoft.VSTS.Scheduling.Effort"] = t_shirt_size
        if not fields:
            return True
        result = self._call_mcp_tool("update_item_state", {
            "action": "update",
            "project": self.project,
            "id": int(item_id),
            "fields": fields,
        })
        if result is None:
            rc = self._ensure_rest_client()
            return rc.update_sizing(item_id, story_points, t_shirt_size) if rc else False
        return bool(result)

    def create_split_stories(self, parent_item_id: str, split_stories: list[dict[str, Any]]) -> list[DevOpsWorkItem]:
        created = []
        for story in split_stories:
            item = self.create_work_item(
                item_type="story",
                title=story.get("title", "Split story"),
                description=story.get("description", ""),
                parent_id=parent_item_id,
                story_points=story.get("story_points", 3),
            )
            if item:
                created.append(item)
        return created

    def create_pull_request(self, source_branch: str, target_branch: str, title: str,
                             description: str = "", work_item_ids: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        result = self._call_mcp_tool("create_pull_request", {
            "action": "create",
            "project": self.project,
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
            "title": title,
            "description": description,
        })
        if result is None:
            rc = self._ensure_rest_client()
            return rc.create_pull_request(source_branch, target_branch, title, description, work_item_ids) if rc else None
        return result

    def apply_iterations(self, iterations: list[dict]) -> dict[str, Any]:
        rc = self._ensure_rest_client()
        if rc is None:
            return {"error": "No REST client available"}
        setup = AzureDevOpsProjectSetup(rc, {**self.config, "iterations": iterations})
        setup.apply_iterations()
        return {"iterations": iterations, "results": setup.results}

    def assign_iteration_to_team(self, team_id: str, iteration_id: str) -> bool:
        rc = self._ensure_rest_client()
        if rc is None:
            return False
        setup = AzureDevOpsProjectSetup(rc, self.config)
        return setup.assign_iteration_to_team(team_id, iteration_id)

    def get_project_info(self) -> dict[str, Any]:
        result = self._call_mcp_tool("get_project_info", {})
        if result is None:
            rc = self._ensure_rest_client()
            if rc is None:
                return {"error": "No client available"}
            org = "https://" + rc.base_url.split("/")[2]
            return rc._request("GET", f"{org}/_apis/projects/{quote(self.project)}?api-version=7.1") or {}
        return result

    def create_project(self, name: str, process_type: str) -> dict[str, Any]:
        rc = self._ensure_rest_client()
        if rc is None:
            raise NotImplementedError("No REST client available for create_project")
        return rc.create_project(name, process_type)

    def import_repository(self, repo_name: str, remote_url: str, credentials: dict) -> dict[str, Any]:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from azure_devops_repo_importer import import_repo
        return import_repo(repo_name, remote_url, self.organization, self.project, self.pat_token, credentials)

    def create_work_item(self, item_type: str, title: str, description: str = "",
                         parent_id: Optional[str] = None, story_points: Optional[int] = None) -> Optional[DevOpsWorkItem]:
        result = self._call_mcp_tool("create_work_item", {
            "action": "create",
            "project": self.project,
            "workItemType": item_type,
            "title": title,
            "description": description,
            "parentId": parent_id,
            "storyPoints": story_points,
        })
        if result is None:
            rc = self._ensure_rest_client()
            return rc.create_work_item(item_type, title, description, parent_id, story_points) if rc else None
        fields = result.get("fields", {})
        return DevOpsWorkItem(
            id=str(result.get("id", "")),
            title=fields.get("System.Title", title),
            description=fields.get("System.Description", description),
            state=fields.get("System.State", "New"),
            story_points=story_points,
        )

    def __del__(self):
        self._stop_mcp_server()
