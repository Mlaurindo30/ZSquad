import os
import tempfile
import json
import pytest
from integrations.kilo_adapter import configure_kilo_mcp, KILO_SCHEMA

def test_generate_from_scratch():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "kilo.jsonc")
        configure_kilo_mcp(target, "/my/python/path")
        
        with open(target, 'r') as f:
            data = json.load(f)
            
        assert data["$schema"] == KILO_SCHEMA
        assert "mcp" in data
        assert "agent-squad" in data["mcp"]
        assert data["mcp"]["agent-squad"]["type"] == "local"
        assert data["mcp"]["agent-squad"]["command"] == ["python", "-m", "integrations.mcp_runner"]
        assert data["mcp"]["agent-squad"]["environment"]["PYTHONPATH"] == "/my/python/path"

def test_idempotent_merge_preserves_comments():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "kilo.jsonc")
        initial_content = '''{
    // My custom schema
    "$schema": "https://app.kilo.ai/config.json",
    "mcp": {
        // existing server
        "other-server": {
            "type": "local"
        }
    }
}'''
        with open(target, 'w') as f:
            f.write(initial_content)
            
        configure_kilo_mcp(target, "/my/python/path")
        
        with open(target, 'r') as f:
            content = f.read()
            
        assert "// existing server" in content
        assert "// My custom schema" in content
        assert '"agent-squad"' in content
        
        # Check idempotency
        configure_kilo_mcp(target, "/my/python/path")
        with open(target, 'r') as f:
            content2 = f.read()
            
        assert content == content2

def test_malformed_jsonc():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "kilo.jsonc")
        with open(target, 'w') as f:
            f.write("{ malformed }")
            
        with pytest.raises(ValueError, match="Malformed JSONC"):
            configure_kilo_mcp(target, "/path")
