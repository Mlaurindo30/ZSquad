import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from sync_mcp_servers import MCPSyncManager


class MCPSyncManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.squad_root = Path(self.temp_dir.name)
        self.manager = MCPSyncManager(self.squad_root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_and_validate_mcp_config(self):
        """Generate mcp_config.json and verify all core servers are registered."""
        out_file = self.squad_root / "config" / "mcp_config.json"
        saved = self.manager.generate_and_save(out_file)
        self.assertTrue(saved.is_file())

        is_valid = self.manager.validate_config(saved)
        self.assertTrue(is_valid)

        data = json.loads(saved.read_text(encoding="utf-8"))
        # squad-local-db foi removido: local_agent_db.py é biblioteca, não servidor MCP.
        self.assertNotIn("squad-local-db", data["mcpServers"])
        self.assertIn("codebase-memory", data["mcpServers"])
        self.assertNotIn("trace-mcp", data["mcpServers"])
        self.assertIn("sinapse-hivemind", data["mcpServers"])

        # A ponte sinapse aponta para o script real (hífen, dentro de services/).
        sinapse_args = data["mcpServers"]["sinapse-hivemind"]["args"]
        self.assertEqual(sinapse_args, ["D:/Hive-Mind/scripts/services/sinapse-mcp.py"])

        # codebase-memory aponta diretamente para o executável binário canônico sem PYTHONPATH.
        codebase_cmd = data["mcpServers"]["codebase-memory"]["command"]
        self.assertTrue(codebase_cmd.endswith("integrations/vendor/codebase-memory-mcp/build/c/codebase-memory-mcp.exe"))
        self.assertEqual(data["mcpServers"]["codebase-memory"]["args"], [])
        self.assertNotIn("env", data["mcpServers"]["codebase-memory"])

        expected_tools = {
            "sinapse_health",
            "sinapse_query",
            "sinapse_save_decision",
            "sinapse_save_learning",
            "sinapse_session_end",
            "sinapse_temporal_search",
            "sinapse_temporal_timeline",
            "sinapse_temporal_get_observations",
            "sinapse_temporal_save",
            "sinapse_zettelkasten_split",
            "sinapse_capture_screen",
            "sinapse_plan_goal",
            "sinapse_promote_knowledge",
            "sinapse_temporal_graph_search",
            "sinapse_rag_query",
            "search_memories",
        }
        actual_tools = set(data["mcpServers"]["sinapse-hivemind"]["tools"])
        self.assertEqual(actual_tools, expected_tools)


if __name__ == "__main__":
    unittest.main()
