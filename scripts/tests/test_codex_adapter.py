"""
Component Contract
Definition: Codex Adapter Tests
Responsibility: Verify Codex MCP adapter idempotency, atomic writes, and fail-fast behavior.
Purpose: Ensure Codex config generator operates without data loss on corrupted TOML files.
Failure Behavior: Fails the test suite if adapter behavior deviates from expectations.
Connections: Tests integrations/codex_adapter.py.
"""
import os
import tempfile
import pytest
from integrations.codex_adapter import register_codex_mcp, rollback_codex_mcp

def test_codex_adapter_creates_new():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.toml")
        runtime_path = "/mock/runtime"
        
        register_codex_mcp(config_path, runtime_path)
        
        assert os.path.exists(config_path)
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        assert "[mcp_servers.agent_squad_mcp]" in content
        assert 'command = "python"' in content
        assert '"-u"' in content
        assert '"/mock/runtime/integrations/mcp_runner.py"' in content
        assert "PYTHONPATH = \"/mock/runtime\"" in content

def test_codex_adapter_preserves_existing():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("[other_server]\nkey = 'value'\n# My Comment\n")
            
        runtime_path = "/mock/runtime"
        register_codex_mcp(config_path, runtime_path)
        
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        assert "[other_server]" in content
        assert "# My Comment" in content
        assert "[mcp_servers.agent_squad_mcp]" in content

def test_codex_adapter_corrupt_toml():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("[corrupted\nkey = value")
            
        runtime_path = "/mock/runtime"
        with pytest.raises(ValueError, match="Corrupted TOML file"):
            register_codex_mcp(config_path, runtime_path)

def test_codex_adapter_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.toml")
        runtime_path = "/mock/runtime"
        
        register_codex_mcp(config_path, runtime_path)
        
        with open(config_path, "r", encoding="utf-8") as f:
            content1 = f.read()
            
        register_codex_mcp(config_path, runtime_path)
        
        with open(config_path, "r", encoding="utf-8") as f:
            content2 = f.read()
            
        assert content1 == content2

def test_codex_adapter_rollback():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("[other_server]\nkey = 'value'\n")
            
        runtime_path = "/mock/runtime"
        register_codex_mcp(config_path, runtime_path)
        
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "[mcp_servers.agent_squad_mcp]" in content
        
        rollback_codex_mcp(config_path)
        
        with open(config_path, "r", encoding="utf-8") as f:
            content_rolled_back = f.read()
            
        assert "[mcp_servers.agent_squad_mcp]" not in content_rolled_back
        assert "[other_server]" in content_rolled_back
