"""
Component Contract for test_copilot_adapter
Definition: Unit tests for the copilot_adapter integration.
Responsibility: Verify idempotent generation and merging of MCP server config for GitHub Copilot.
Purpose: Ensure config is not destroyed, environment is correct, and merging works.
Failure Behavior: Test failure on incorrect JSON structure or unhandled exceptions.
Connections: Tests integrations.copilot_adapter.
"""
import json
import os
import tempfile
from pathlib import Path
import pytest

from integrations.copilot_adapter import merge_copilot_config

@pytest.fixture
def temp_config_file():
    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
    tmp.close()
    yield Path(tmp.name)
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)

def test_generate_new_config(temp_config_file):
    repo_path = "C:\\path\\to\\agent_squad"
    merge_copilot_config(temp_config_file, repo_path)
    
    with open(temp_config_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    assert "mcpServers" in data
    assert "agent-squad" in data["mcpServers"]
    server = data["mcpServers"]["agent-squad"]
    assert server["command"] == "python"
    assert "-m" in server["args"]
    assert "integrations.mcp_runner" in server["args"]
    assert server["env"]["PYTHONPATH"] == repo_path

def test_merge_existing_config(temp_config_file):
    # Setup existing config
    initial_data = {
        "mcpServers": {
            "existing-server": {
                "command": "node",
                "args": ["index.js"]
            }
        },
        "globalSettings": True
    }
    with open(temp_config_file, 'w', encoding='utf-8') as f:
        json.dump(initial_data, f)
        
    repo_path = "C:\\path\\to\\agent_squad"
    merge_copilot_config(temp_config_file, repo_path)
    
    with open(temp_config_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    # Check preserved existing server and settings
    assert "existing-server" in data["mcpServers"]
    assert data["mcpServers"]["existing-server"]["command"] == "node"
    assert data.get("globalSettings") is True
    
    # Check new server added
    assert "agent-squad" in data["mcpServers"]
    assert data["mcpServers"]["agent-squad"]["env"]["PYTHONPATH"] == repo_path

def test_idempotent_merge(temp_config_file):
    repo_path = "C:\\path\\to\\agent_squad"
    
    # Run twice
    merge_copilot_config(temp_config_file, repo_path)
    merge_copilot_config(temp_config_file, repo_path)
    
    with open(temp_config_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    assert "mcpServers" in data
    assert "agent-squad" in data["mcpServers"]
    assert len(data["mcpServers"]) == 1

def test_invalid_json(temp_config_file):
    with open(temp_config_file, 'w', encoding='utf-8') as f:
        f.write("{ invalid json")
        
    repo_path = "C:\\path\\to\\agent_squad"
    with pytest.raises(ValueError, match="Invalid JSON"):
        merge_copilot_config(temp_config_file, repo_path)
