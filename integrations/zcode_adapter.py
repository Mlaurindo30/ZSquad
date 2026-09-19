"""
Component Contract:
- Definition: ZCode Adapter for Agent Squad MCP Server.
- Responsibility: Generates and merges the 'agent-squad' MCP server configuration idempotently into the ZCode configuration file (`.zcode/config.json`).
- Purpose: Expose Agent Squad capabilities to the ZCode environment via stdio MCP.
- Failure Behavior: Raises ValueError on invalid JSON syntax (fail-fast without destroying file). Uses atomic writing (tempfile + os.replace) to prevent configuration corruption.
- Connections: Modifies `<project root>/.zcode/config.json` or `~/.zcode/cli/config.json`. Connects the Agent Squad execution environment to ZCode.
"""
import os
import json
import tempfile
import pathlib

def configure_zcode(zcode_config_path=None, squad_runtime_path=None):
    """
    Idempotently configures the ZCode JSON configuration file to include the
    Agent Squad MCP server under `mcpServers.agent-squad`.
    """
    if squad_runtime_path is None:
        # Defaults to the workspace root
        squad_runtime_path = str(pathlib.Path(__file__).parent.parent.absolute())
        
    if zcode_config_path is None:
        zcode_config_path = os.path.join(squad_runtime_path, ".zcode", "config.json")
        
    config_file = pathlib.Path(zcode_config_path)
    config = {}
    
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                content = f.read()
                if content.strip():
                    config = json.loads(content)
        except Exception as e:
            raise ValueError(f"Failed to parse existing configuration file at {config_file}: {e}") from e
            
    if "mcpServers" not in config or not isinstance(config["mcpServers"], dict):
        config["mcpServers"] = {}
        
    # Idempotent merge
    config["mcpServers"]["agent-squad"] = {
        "command": "python",
        "args": [
            "-m",
            "integrations.mcp_runner"
        ],
        "env": {
            "PYTHONPATH": squad_runtime_path,
            "SQUAD_RUNTIME": squad_runtime_path
        }
    }
    
    config_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Atomic write
    fd, temp_path = tempfile.mkstemp(dir=config_file.parent, prefix="zcode_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        
    os.replace(temp_path, config_file)
    
    return True

if __name__ == "__main__":
    configure_zcode()
