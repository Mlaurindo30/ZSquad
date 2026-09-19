import pytest
import json5
from integrations.openclaw_adapter import configure_openclaw

def test_configure_openclaw_creates_new_file(tmp_path):
    config_path = tmp_path / ".openclaw" / "openclaw.json"
    squad_runtime = "/fake/squad/runtime"
    
    assert not config_path.exists()
    
    result = configure_openclaw(openclaw_config_path=str(config_path), squad_runtime_path=squad_runtime)
    
    assert result is True
    assert config_path.exists()
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json5.load(f)
        
    assert "mcp" in config
    assert "servers" in config["mcp"]
    assert "agent-squad" in config["mcp"]["servers"]
    
    squad_config = config["mcp"]["servers"]["agent-squad"]
    assert squad_config["command"] == "python"
    assert squad_config["args"] == ["-m", "integrations.mcp_runner"]
    assert squad_config["env"]["PYTHONPATH"] == squad_runtime
    assert squad_config["env"]["SQUAD_RUNTIME"] == squad_runtime

def test_configure_openclaw_idempotent_merge(tmp_path):
    config_dir = tmp_path / ".openclaw"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "openclaw.json"
    
    initial_config = {
        "mcp": {
            "servers": {
                "existing-server": {
                    "command": "node",
                    "args": ["server.js"]
                }
            }
        },
        "other_setting": True
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        json5.dump(initial_config, f)
        
    squad_runtime = "/fake/squad/runtime"
    result = configure_openclaw(openclaw_config_path=str(config_path), squad_runtime_path=squad_runtime)
    
    assert result is True
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json5.load(f)
        
    assert config["other_setting"] is True
    assert "existing-server" in config["mcp"]["servers"]
    assert "agent-squad" in config["mcp"]["servers"]
    
    squad_config = config["mcp"]["servers"]["agent-squad"]
    assert squad_config["env"]["PYTHONPATH"] == squad_runtime
    
    # Run again to test idempotency
    result = configure_openclaw(openclaw_config_path=str(config_path), squad_runtime_path=squad_runtime)
    assert result is True
    
    with open(config_path, "r", encoding="utf-8") as f:
        config_after = json5.load(f)
        
    assert config == config_after

def test_configure_openclaw_invalid_json(tmp_path):
    config_dir = tmp_path / ".openclaw"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "openclaw.json"
    
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("invalid json content")
        
    squad_runtime = "/fake/squad/runtime"
    
    with pytest.raises(ValueError, match="Failed to parse existing configuration file"):
        configure_openclaw(openclaw_config_path=str(config_path), squad_runtime_path=squad_runtime)
    
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert content == "invalid json content"
