"""Tests for integrations/devops_platform_connector.py — Azure DevOps, Jira & Local Fallback."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.devops_platform_connector import (
    AzureDevOpsClient,
    DevOpsPlatformConnector,
    DevOpsWorkItem,
    JiraClient,
    LocalFilesystemFallbackClient,
)


class TestDevOpsPlatformConnector(unittest.TestCase):

    def test_local_filesystem_fallback_default(self):
        """Without Azure/Jira env vars, connector falls back to LocalFilesystemFallbackClient."""
        # Root isolado (sem .env real) — o repo tem um .env de verdade para uso ao vivo.
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            connector = DevOpsPlatformConnector(root_path=Path(tmp))
            self.assertIsInstance(connector.client, LocalFilesystemFallbackClient)

    def test_azure_devops_resolution(self):
        """With AZURE_DEVOPS_* env vars, connector resolves AzureDevOpsClient."""
        env = {
            "AZURE_DEVOPS_PAT": "fake-pat-token",
            "AZURE_DEVOPS_ORG": "fake-org",
            "AZURE_DEVOPS_PROJECT": "fake-proj",
        }
        with patch.dict(os.environ, env, clear=True):
            connector = DevOpsPlatformConnector(root_path=ROOT)
            self.assertIsInstance(connector.client, AzureDevOpsClient)
            self.assertEqual(connector.client.organization, "fake-org")
            self.assertEqual(connector.client.project, "fake-proj")

    def test_jira_resolution(self):
        """With JIRA_* env vars, connector resolves JiraClient."""
        env = {
            "JIRA_API_TOKEN": "fake-token",
            "JIRA_DOMAIN": "fake-domain",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_PROJECT_KEY": "PROJ",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True):
            connector = DevOpsPlatformConnector(root_path=Path(tmp))
            self.assertIsInstance(connector.client, JiraClient)
            self.assertEqual(connector.client.domain, "fake-domain")
            self.assertEqual(connector.client.project_key, "PROJ")

    def test_azure_devops_client_methods(self):
        """Test Azure DevOps Client REST calls, mocking urllib so no real network is hit."""
        client = AzureDevOpsClient("myorg", "myproj", "token123")
        responses = [
            {"workItems": [{"id": 101}]},                                          # WIQL query
            {"value": [{"id": 101, "fields": {"System.Title": "Item", "System.State": "New"}}]},  # batch GET
            {},                                                                     # update_item_state PATCH
            {},                                                                     # update_sizing PATCH
            {"id": 202, "fields": {"System.Title": "Part 1"}},                       # create_work_item POST
        ]

        def fake_request(self_client, method, url, body=None, content_type="application/json"):
            return responses.pop(0)

        with patch.object(AzureDevOpsClient, "_request", fake_request):
            items = client.pull_ready_items("Core")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].id, "101")
            self.assertTrue(client.update_item_state("101", "Active"))
            self.assertTrue(client.update_sizing("101", story_points=5))
            splits = client.create_split_stories("101", [{"title": "Part 1", "story_points": 3}])
            self.assertEqual(len(splits), 1)
            self.assertEqual(splits[0].id, "202")

    def test_azure_devops_client_pull_request(self):
        """create_pull_request builds the correct Azure Repos payload and URL."""
        client = AzureDevOpsClient("myorg", "myproj", "token123", config={"repo": "myrepo"})
        captured = {}

        def fake_request(self_client, method, url, body=None, content_type="application/json"):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = body
            return {"pullRequestId": 7}

        with patch.object(AzureDevOpsClient, "_request", fake_request):
            result = client.create_pull_request("feature/x", "main", "Title", "Desc", ["101"])
            self.assertEqual(result["pullRequestId"], 7)
            self.assertEqual(captured["method"], "POST")
            self.assertIn("myrepo/pullrequests", captured["url"])
            self.assertEqual(captured["body"]["sourceRefName"], "refs/heads/feature/x")
            self.assertEqual(captured["body"]["workItemRefs"], [{"id": "101"}])

    def test_azure_devops_request_handles_non_json_response(self):
        """_request must fail gracefully (return None) on non-JSON bodies instead of raising."""
        client = AzureDevOpsClient("myorg", "myproj", "token123")
        fake_response = MagicMock()
        fake_response.read.return_value = b"<html>not json</html>"
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=fake_response):
            result = client._request("GET", "https://example.invalid/_apis/wit/wiql")
            self.assertIsNone(result)

    def test_jira_client_methods(self):
        """Test Jira Client interface methods."""
        client = JiraClient("company", "user@co.com", "token", "SQUAD")
        items = client.pull_ready_items("Core")
        self.assertIsInstance(items, list)
        self.assertTrue(client.update_item_state("SQUAD-1", "In Progress"))
        self.assertTrue(client.update_sizing("SQUAD-1", story_points=3))
        splits = client.create_split_stories("SQUAD-1", [])
        self.assertEqual(len(splits), 0)

    def test_local_filesystem_fallback_operations(self):
        """Test local fallback client pull, sizing, and status update."""
        fallback = LocalFilesystemFallbackClient(ROOT)
        items = fallback.pull_ready_items()
        self.assertIsInstance(items, list)
        splits = fallback.create_split_stories("TASK-100", [{"title": "Split 1", "story_points": 5}])
        self.assertEqual(len(splits), 1)

    def test_split_story_if_exceeded(self):
        """Test Cognitive Protection Max 8 Points splitting logic."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            connector = DevOpsPlatformConnector(root_path=Path(tmp))
            # Below or equal to 8 points -> no split
            result = connector.split_story_if_exceeded("TASK-1", 5, [])
            self.assertIsNone(result)
            # Above 8 points -> split triggered
            splits = connector.split_story_if_exceeded("TASK-1", 13, [{"title": "Part A", "story_points": 5}])
            self.assertIsNotNone(splits)
            self.assertEqual(len(splits), 1)


if __name__ == "__main__":
    unittest.main()
