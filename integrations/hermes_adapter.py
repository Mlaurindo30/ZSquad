"""
Component Contract:
Definition: Adapter to configure Hermes Agent for Agent Squad MCP server.
Responsibility: Safely write and merge MCP server configuration into Hermes config.yaml.
Purpose: Enable Hermes Agent to communicate with Agent Squad via stdio transport.
Failure Behavior: Fail-fast with ValueError on corrupted YAML. Atomic writes prevent partial corruption.
Connections: Writes to ~/.hermes/config.yaml (or specified path), reads same file.
"""
import os
import tempfile
import yaml

def install_hermes_mcp_server(config_path: str, squad_runtime_path: str):
    """
    Idempotent function to generate or update Hermes config.yaml with the agent-squad MCP server.
    """
    if not squad_runtime_path:
        raise ValueError("SQUAD_RUNTIME path must be provided.")
    if not os.path.isabs(squad_runtime_path):
        raise ValueError("PYTHONPATH validation failed: SQUAD_RUNTIME path must be absolute.")

    config_data = {}
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if content.strip():
                    config_data = yaml.safe_load(content)
                    if config_data is None:
                        config_data = {}
                    if not isinstance(config_data, dict):
                        raise ValueError("Corrupted YAML: root is not a dictionary.")
        except yaml.YAMLError as e:
            raise ValueError(f"Corrupted YAML: {e}")
    
    if 'mcp_servers' not in config_data:
        config_data['mcp_servers'] = {}
        
    mcp_runner_path = os.path.join(squad_runtime_path, "integrations", "mcp_runner.py").replace('\\', '/')
    squad_runtime_path_unix = squad_runtime_path.replace('\\', '/')
    
    config_data['mcp_servers']['agent-squad'] = {
        'command': 'python',
        'args': [mcp_runner_path],
        'env': {
            'SQUAD_RUNTIME': squad_runtime_path_unix,
            'PYTHONPATH': squad_runtime_path_unix,
            'AZURE_DEVOPS_MCP_TRANSPORT': 'azure-devops'
        }
    }
    
    config_dir = os.path.dirname(config_path)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
        
    fd, temp_path = tempfile.mkstemp(dir=config_dir if config_dir else '.', text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
        os.replace(temp_path, config_path)
    except Exception as e:
        os.unlink(temp_path)
        raise e
