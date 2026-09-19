import pytest
import json
from unittest.mock import AsyncMock, patch
from integrations.mcp_client import MCPClient

@pytest.mark.asyncio
async def test_mcp_client_handshake() -> None:
    mock_process = AsyncMock()
    mock_stdout = AsyncMock()
    mock_stdin = AsyncMock()
    
    init_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "1",
            "serverCapabilities": {},
            "serverInfo": {"name": "test-server", "version": "1.0"}
        }
    }
    
    mock_stdout.readline.side_effect = [
        (json.dumps(init_response) + "\n").encode('utf-8')
    ]
    
    mock_process.stdout = mock_stdout
    mock_process.stdin = mock_stdin
    # mock write as a regular mock to avoid coroutine warning
    mock_process.stdin.write = lambda x: None
    
    with patch('asyncio.create_subprocess_exec', return_value=mock_process):
        client = MCPClient("dummy_command", ["arg1"])
        await client.start()
        
        result = await client.initialize()
        
        assert result["serverInfo"]["name"] == "test-server"

@pytest.mark.asyncio
async def test_eager_discovery() -> None:
    mock_process = AsyncMock()
    mock_stdout = AsyncMock()
    mock_stdin = AsyncMock()
    
    init_res = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "test-server"}}}
    tools_res = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "tool1"}]}}
    resources_res = {"jsonrpc": "2.0", "id": 3, "result": {"resources": [{"uri": "file://test"}]}}
    
    mock_stdout.readline.side_effect = [
        (json.dumps(init_res) + "\n").encode('utf-8'),
        (json.dumps(tools_res) + "\n").encode('utf-8'),
        (json.dumps(resources_res) + "\n").encode('utf-8'),
    ]
    
    mock_process.stdout = mock_stdout
    mock_process.stdin = mock_stdin
    mock_process.stdin.write = lambda x: None
    
    with patch('asyncio.create_subprocess_exec', return_value=mock_process):
        client = MCPClient("dummy_command", ["arg1"])
        await client.connect()
        
        assert client.server_info["name"] == "test-server"
        assert len(client.tools) == 1
        assert client.tools[0]["name"] == "tool1"
        assert len(client.resources) == 1
        assert client.resources[0]["uri"] == "file://test"
