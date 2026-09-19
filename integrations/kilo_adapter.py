"""
Component Contract
Definition: Adapter for configuring Kilo Code MCP integration.
Responsibility: Generates and updates kilo.jsonc with the agent-squad MCP server configuration idempotently.
Purpose: Enables Agent Squad runtime to act as a local MCP server for Kilo Code.
Failure Behavior: Raises specific exceptions on malformed JSON/JSONC. Uses atomic write via tempfile to prevent corruption.
Connections: Interacts with the filesystem (read/write kilo.jsonc) and consumes mcp_runner.
"""
import os
import tempfile
import json
import re

KILO_SCHEMA = "https://app.kilo.ai/config.json"

def configure_kilo_mcp(target_path: str, python_path: str) -> None:
    """
    Idempotently configures the agent-squad MCP server in the target kilo.jsonc file.
    Preserves comments by using regex-based injection when possible.
    """
    agent_squad_block = {
        "type": "local",
        "command": ["python", "-m", "integrations.mcp_runner"],
        "enabled": True,
        "environment": {
            "PYTHONPATH": python_path
        }
    }
    
    agent_squad_json = json.dumps(agent_squad_block, indent=4)
    # Indent it properly to fit inside "mcp": { ... }
    agent_squad_json = "\n".join("        " + line if i > 0 else line for i, line in enumerate(agent_squad_json.split("\n")))

    if not os.path.exists(target_path):
        config = {
            "$schema": KILO_SCHEMA,
            "mcp": {
                "agent-squad": agent_squad_block
            }
        }
        _write_atomically(target_path, json.dumps(config, indent=2))
        return

    with open(target_path, 'r', encoding='utf-8') as f:
        content = f.read()

    try:
        import json5
        parsed = json5.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Configuration must be a JSON object.")
    except Exception as e:
        raise ValueError(f"Malformed JSONC in {target_path}: {e}")

    # If it's already exactly configured, do nothing
    if parsed.get("mcp", {}).get("agent-squad") == agent_squad_block:
        return

    if "mcp" not in parsed:
        parsed["mcp"] = {"agent-squad": agent_squad_block}
        _write_atomically(target_path, json.dumps(parsed, indent=2))
        return

    mcp_match = re.search(r'("mcp"\s*:\s*\{)', content)
    if mcp_match:
        if '"agent-squad"' not in content:
            insert_pos = mcp_match.end()
            new_content = content[:insert_pos] + f'\n        "agent-squad": {agent_squad_json},' + content[insert_pos:]
            # Validate new content
            try:
                json5.loads(new_content)
                _write_atomically(target_path, new_content)
            except Exception:
                # Fallback to json dump
                parsed["mcp"]["agent-squad"] = agent_squad_block
                _write_atomically(target_path, json.dumps(parsed, indent=2))
        else:
            # Updating existing agent-squad. Fallback to json dump
            parsed["mcp"]["agent-squad"] = agent_squad_block
            _write_atomically(target_path, json.dumps(parsed, indent=2))
    else:
        parsed["mcp"]["agent-squad"] = agent_squad_block
        _write_atomically(target_path, json.dumps(parsed, indent=2))

def _write_atomically(target_path: str, content: str) -> None:
    dir_name = os.path.dirname(target_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_name or '.', text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        os.remove(tmp_path)
        raise e

    os.replace(tmp_path, target_path)
