"""
Component Contract
Definition: Codex MCP Adapter
Responsibility: Registers the Agent Squad MCP server in Codex config.toml safely and idempotently.
Purpose: Allow Codex to consume Agent Squad MCP tools via standard STDIO configuration.
Failure Behavior: Raises ValueError if the TOML is corrupted. Uses atomic write to prevent data loss.
Connections: Integrates with integrations/mcp_runner.py.
"""

import os
import sys
import shutil
import tempfile
import argparse
import tomllib
import tomlkit

def validate_toml(config_path: str) -> None:
    with open(config_path, "rb") as f:
        try:
            tomllib.load(f)
        except Exception as e:
            raise ValueError(f"Corrupted TOML file: {e}") from e

def register_codex_mcp(config_path: str, runtime_path: str) -> None:
    """
    Idempotent function to register the agent_squad_mcp in Codex configuration.
    Uses atomic write and tomllib for fail-fast validation.
    """
    backup_path = config_path + ".bak"
    
    if os.path.exists(config_path):
        validate_toml(config_path)
        with open(config_path, "r", encoding="utf-8") as f:
            doc = tomlkit.load(f)
        # Create backup
        shutil.copy2(config_path, backup_path)
    else:
        doc = tomlkit.document()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

    if "mcp_servers" not in doc:
        doc.add("mcp_servers", tomlkit.table())
    
    mcp_servers = doc["mcp_servers"]
    
    agent_squad_mcp = tomlkit.table()
    agent_squad_mcp["command"] = "python"
    
    runner_path = os.path.join(runtime_path, "integrations", "mcp_runner.py").replace("\\", "/")
    
    args_array = tomlkit.array()
    args_array.append("-u")
    args_array.append(runner_path)
    agent_squad_mcp["args"] = args_array
    
    env_table = tomlkit.table()
    env_table["PYTHONPATH"] = runtime_path.replace("\\", "/")
    env_table["AZURE_DEVOPS_MCP_TRANSPORT"] = "azure-devops"
    
    agent_squad_mcp["env"] = env_table
    
    mcp_servers["agent_squad_mcp"] = agent_squad_mcp
    
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(config_path), text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
        
    os.replace(temp_path, config_path)

def rollback_codex_mcp(config_path: str) -> None:
    backup_path = config_path + ".bak"
    if os.path.exists(backup_path):
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(config_path), text=True)
        os.close(fd)
        shutil.copy2(backup_path, temp_path)
        os.replace(temp_path, config_path)
    else:
        raise FileNotFoundError("Backup file not found")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Codex MCP Adapter")
    parser.add_argument("--config", required=True, help="Path to config.toml")
    parser.add_argument("--runtime", required=False, help="Path to SQUAD_RUNTIME")
    parser.add_argument("--rollback", action="store_true", help="Rollback to previous config")
    args = parser.parse_args()
    
    if args.rollback:
        rollback_codex_mcp(args.config)
        print("Rollback successful.")
    else:
        if not args.runtime:
            print("--runtime is required when not rolling back")
            sys.exit(1)
        register_codex_mcp(args.config, args.runtime)
        print("Registration successful.")
