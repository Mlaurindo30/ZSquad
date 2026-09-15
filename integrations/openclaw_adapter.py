"""
Component Contract:
- Definition: OpenClaw Adapter for Agent Squad MCP Server.
- Responsibility: Generates and merges the 'agent-squad' MCP server configuration idempotently into the OpenClaw configuration file (`openclaw.json`).
- Purpose: Expose Agent Squad capabilities to the OpenClaw environment via stdio MCP.
- Failure Behavior: Raises standard exceptions on file permission errors or invalid JSON5 syntax. Uses atomic writing to prevent configuration corruption.
- Connections: Modifies `~/.openclaw/openclaw.json` (or a custom path). Connects the Agent Squad execution environment to OpenClaw.
"""
import os
import json5
import tempfile
import pathlib

def configure_openclaw(openclaw_config_path=None, squad_runtime_path=None):
    """
    Idempotently configures the OpenClaw JSON5 configuration file to include the
    Agent Squad MCP server under `mcp.servers.agent-squad`.
    """
    if openclaw_config_path is None:
        openclaw_config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        
    if squad_runtime_path is None:
        # Defaults to the workspace root Path(__file__).parent.parent
        squad_runtime_path = str(pathlib.Path(__file__).parent.parent.absolute())
        
    config_file = pathlib.Path(openclaw_config_path)
    config = {}
    
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json5.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse existing configuration file at {config_file}: {e}") from e
            
    if "mcp" not in config or not isinstance(config["mcp"], dict):
        config["mcp"] = {}
        
    if "servers" not in config["mcp"] or not isinstance(config["mcp"]["servers"], dict):
        config["mcp"]["servers"] = {}
        
    # Idempotent merge
    config["mcp"]["servers"]["agent-squad"] = {
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
    fd, temp_path = tempfile.mkstemp(dir=config_file.parent, prefix="openclaw_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        # Use json5 to dump
        json5.dump(config, f, indent=2)
        
    os.replace(temp_path, config_file)
    
    return True

if __name__ == "__main__":
    configure_openclaw()
