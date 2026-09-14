import os
import pytest
import yaml

from integrations.hermes_adapter import install_hermes_mcp_server

def test_generate_config_from_scratch(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    runtime_path = "C:/Users/miche/OneDrive/Documentos/agent_squad"
    
    install_hermes_mcp_server(config_path, runtime_path)
    
    assert os.path.exists(config_path)
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)
        
    assert 'mcp_servers' in data
    assert 'agent-squad' in data['mcp_servers']
    server = data['mcp_servers']['agent-squad']
    assert server['command'] == 'python'
    assert runtime_path.replace('\\', '/') in server['env']['SQUAD_RUNTIME']

def test_idempotent_merge_preserves_other_servers(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    initial_data = {
        'other_root_key': 'value',
        'mcp_servers': {
            'other-server': {
                'command': 'node',
                'args': ['index.js']
            }
        }
    }
    with open(config_path, 'w') as f:
        yaml.dump(initial_data, f)
        
    runtime_path = "C:/Users/miche/OneDrive/Documentos/agent_squad"
    install_hermes_mcp_server(config_path, runtime_path)
    
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)
        
    assert data['other_root_key'] == 'value'
    assert 'other-server' in data['mcp_servers']
    assert 'agent-squad' in data['mcp_servers']

def test_fail_fast_on_corrupted_yaml(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    with open(config_path, 'w') as f:
        f.write("corrupted: :\n yaml: ]")
        
    runtime_path = "C:/Users/miche/OneDrive/Documentos/agent_squad"
    with pytest.raises(ValueError, match="Corrupted YAML"):
        install_hermes_mcp_server(config_path, runtime_path)

def test_pythonpath_validation(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    runtime_path = "relative/path" # not absolute
    with pytest.raises(ValueError, match="PYTHONPATH validation failed"):
        install_hermes_mcp_server(config_path, runtime_path)
