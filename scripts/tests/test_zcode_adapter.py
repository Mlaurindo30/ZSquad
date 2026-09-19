import json
import pytest
from integrations.zcode_adapter import configure_zcode

def test_configure_zcode_from_scratch(tmp_path):
    """Test generating .zcode/config.json from scratch."""
    config_path = tmp_path / ".zcode" / "config.json"
    squad_path = str(tmp_path)
    
    # Run the adapter
    assert configure_zcode(zcode_config_path=str(config_path), squad_runtime_path=squad_path)
    
    # Verify file was created
    assert config_path.exists()
    
    # Verify contents
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    assert "mcpServers" in config
    assert "agent-squad" in config["mcpServers"]
    
    agent_config = config["mcpServers"]["agent-squad"]
    assert agent_config["command"] == "python"
    assert agent_config["args"] == ["-m", "integrations.mcp_runner"]
    assert agent_config["env"]["PYTHONPATH"] == squad_path
    assert agent_config["env"]["SQUAD_RUNTIME"] == squad_path

def test_configure_zcode_idempotent_merge(tmp_path):
    """Test merging idempotently while preserving existing servers."""
    config_path = tmp_path / ".zcode" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    initial_config = {
        "mcpServers": {
            "existing-server": {
                "command": "node",
                "args": ["server.js"]
            }
        },
        "otherSetting": True
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(initial_config, f)
        
    squad_path = str(tmp_path)
    
    # Run the adapter
    assert configure_zcode(zcode_config_path=str(config_path), squad_runtime_path=squad_path)
    
    # Verify contents
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    assert "otherSetting" in config
    assert config["otherSetting"] is True
    assert "existing-server" in config["mcpServers"]
    assert "agent-squad" in config["mcpServers"]

def test_configure_zcode_fail_fast_corrupted_json(tmp_path):
    """Test that a ValueError is raised on corrupted JSON and file is not destroyed."""
    config_path = tmp_path / ".zcode" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    corrupt_json = "{ invalid_json: "
    
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(corrupt_json)
        
    squad_path = str(tmp_path)
    
    # Run the adapter, should raise ValueError
    with pytest.raises(ValueError, match="Failed to parse existing configuration file"):
        configure_zcode(zcode_config_path=str(config_path), squad_runtime_path=squad_path)
        
    # Verify file is unmodified and still corrupted
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == corrupt_json
