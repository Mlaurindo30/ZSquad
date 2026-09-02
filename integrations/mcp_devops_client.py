#!/usr/bin/env python3
"""McpDevOpsClient — wrapper MCP stdio JSON-RPC para @azure-devops/mcp.

Transport: subprocess stdio JSON-RPC (não HTTP SSE).
Fallback: se MCP falhar ou não suportar operation, delega para rest_client (AzureDevOpsClient REST).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("McpDevOpsClient")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.devops_platform_connector import BaseDevOpsClient, DevOpsWorkItem


class McpDevOpsClient(BaseDevOpsClient):
    """Wrapper MCP para Azure DevOps via @azure-devops/mcp stdio JSON-RPC.
    
    Fallback automático para rest_client (AzureDevOpsClient) quando:
    - MCP tool não existe para a operação
    - MCP timeout (30s)
    - MCP retorna erro
    """

    TOOL_MAP = {
        "pull_ready_items": "wit_query.wiql",
        "update_item_state": "wit_work_item_write.update",
        "create_work_item": "wit_work_item_write.create",
        "create_pull_request": "repo_pull_request_write.create",
        "get_team_settings": "work.get_team_settings",
        "get_project_info": "work.get_project_info",
    }

    REST_ONLY = {"apply_iterations", "assign_iteration_to_team", "create_project", "import_repository"}

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
        self._start_mcp_server()

    def _mcp_binary_available(self) -> bool:
        try:
            result = subprocess.run(
                ["npx", "--yes", "@azure-devops/mcp@1.0.0", "--help"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _start_mcp_server(self) -> None:
        org_url = self.config.get("org_url") or f"https://dev.azure.com/{self.organization}"
        env = {
            **os.environ,
            "AZURE_DEVOPS_ORG": self.organization,
            "AZURE_DEVOPS_PROJECT": self.project,
            "AZURE_DEVOPS_PAT": self.pat_token,
        }
        try:
            self._process = subprocess.Popen(
                ["npx", "--yes", "@azure-devops/mcp@1.0.0", org_url],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            logger.info("[McpDevOpsClient] MCP server started (PID=%s)", self._process.pid)
        except Exception as exc:
            logger.warning("[McpDevOpsClient] Falha ao iniciar MCP server: %s. Usando REST only.", exc)
            self._process = None

    def _call_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
        if self._process is None:
            return None
        
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": tool_name,
            "params": arguments,
        }
        
        try:
            self._process.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            
            deadline = time.time() + 30
            while time.time() < deadline:
                line = self._process.stdout.readline()
                if not line:
                    break
                try:
                    resp = json.loads(line.decode("utf-8"))
                    if resp.get("id") == req_id:
                        if "error" in resp:
                            logger.warning("[McpDevOpsClient] MCP error %s: %s", tool_name, resp["error"])
                            return None
                        return resp.get("result")
                except json.JSONDecodeError:
                    continue
            logger.warning("[McpDevOpsClient] Timeout waiting for MCP response: %s", tool_name)
            return None
        except Exception as exc:
            logger.warning("[McpDevOpsClient] MCP call failed: %s → %s", tool_name, exc)
            return None

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
        result = self._call_mcp_tool("wit_query.wiql", {
            "query": f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '{self.project}' AND [System.State] IN ('New', 'Active')"
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
        result = self._call_mcp_tool("wit_work_item_write.update", {
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
        result = self._call_mcp_tool("wit_work_item_write.update", {
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
        result = self._call_mcp_tool("repo_pull_request_write.create", {
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
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from azure_devops_project_setup import AzureDevOpsProjectSetup
        setup = AzureDevOpsProjectSetup(rc)
        return setup.apply_iterations(iterations)

    def assign_iteration_to_team(self, team_id: str, iteration_id: str) -> bool:
        rc = self._ensure_rest_client()
        if rc is None:
            return False
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from azure_devops_project_setup import AzureDevOpsProjectSetup
        setup = AzureDevOpsProjectSetup(rc)
        return setup.assign_iteration_to_team(team_id, iteration_id)

    def get_project_info(self) -> dict[str, Any]:
        result = self._call_mcp_tool("work.get_project_info", {"projectName": self.project})
        if result is None:
            rc = self._ensure_rest_client()
            if rc is None:
                return {"error": "No client available"}
            return rc._request("GET", f"{rc.base_url}?api-version=7.1") or {}
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
        result = self._call_mcp_tool("wit_work_item_write.create", {
            "type": item_type,
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
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                pass
