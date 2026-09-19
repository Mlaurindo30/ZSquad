"""
Component Contract for claude_adapter
Definition: Adapter to configure Agent Squad as an MCP server for Claude Desktop.
Responsibility: Safely generate or merge the agent-squad MCP server block into claude_desktop_config.json.
Purpose: Enable Claude Desktop to communicate with the Agent Squad runtime via STDIO.
Failure Behavior: Raises ValueError on invalid JSON, EnvironmentError if APPDATA is missing.
Connections: Reads/Writes to %APPDATA%/Claude/claude_desktop_config.json.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Union

def merge_claude_config(config_path: Union[Path, str], repo_path: str) -> None:
    """
    Idempotently generate or merge the agent-squad config into the Claude config file.
    
    Args:
        config_path: The path to claude_desktop_config.json
        repo_path: The absolute path to the agent_squad repository.
    """
    config_path = Path(config_path)
    data = {}
    
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {config_path}: {e}")
            
    if "mcpServers" not in data:
        data["mcpServers"] = {}
        
    data["mcpServers"]["agent-squad"] = {
        "command": "python",
        "args": [
            "-m",
            "integrations.mcp_runner"
        ],
        "env": {
            "PYTHONPATH": repo_path
        }
    }
    
    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    tmp_file = tempfile.NamedTemporaryFile(
        mode='w', 
        delete=False, 
        dir=config_path.parent, 
        encoding='utf-8'
    )
    try:
        json.dump(data, tmp_file, indent=2)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
        tmp_file.close()
        os.replace(tmp_file.name, config_path)
    except Exception:
        tmp_file.close()
        if os.path.exists(tmp_file.name):
            os.unlink(tmp_file.name)
        raise

def setup_claude_desktop(repo_path: str = None) -> Path:
    """
    Main entry point to install the Claude Desktop configuration.
    Defaults to resolving repo_path based on current file location and %APPDATA%.
    """
    if not repo_path:
        repo_path = str(Path(__file__).parent.parent.resolve())
        
    appdata = os.environ.get('APPDATA')
    if not appdata:
        raise EnvironmentError("APPDATA environment variable not found.")
        
    config_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
    
    merge_claude_config(config_path, repo_path)
    return config_path

if __name__ == "__main__":
    path = setup_claude_desktop()
    print(f"Claude Desktop configured successfully at {path}")
