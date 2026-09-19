import os
from integrations.mcp_server import AgentSquadMCPServer

def test_mcp_server_initialize():
    server = AgentSquadMCPServer()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Antigravity", "version": "1.0.0"}
        }
    }
    response = server.handle_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "error" not in response
    assert response["result"]["serverInfo"]["name"] == "agent-squad"
    assert response["result"]["protocolVersion"] == "2024-11-05"

def test_mcp_server_notifications():
    server = AgentSquadMCPServer()
    request = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    }
    response = server.handle_request(request)
    assert response is None

def test_mcp_server_list_tools():
    server = AgentSquadMCPServer()
    
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    }
    
    response = server.handle_request(request)
    
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert "error" not in response
    assert "tools" in response["result"]
    
    tools = {t["name"]: t for t in response["result"]["tools"]}
    expected_tools = [
        "start_session", "resume_session", "get_assignment",
        "prepare_delegation", "record_execution", "record_evidence", "evaluate_gate"
    ]
    
    for tool in expected_tools:
        assert tool in tools
        assert "inputSchema" in tools[tool]

def test_mcp_server_call_start_session():
    server = AgentSquadMCPServer()
    
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "start_session",
            "arguments": {
                "host": "test-host",
                "project_root": os.getcwd(),
                "work_item": "TASK-123",
                "capability_report_hash": "abc"
            }
        }
    }
    
    response = server.handle_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 2
    assert "error" not in response
    assert "content" in response["result"]
    assert not response["result"].get("isError", False)

def test_mcp_server_call_unknown_tool():
    server = AgentSquadMCPServer()
    
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "unknown_tool",
            "arguments": {}
        }
    }
    
    response = server.handle_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 3
    assert "error" in response
    assert response["error"]["code"] == -32601

def test_mcp_server_unknown_method():
    server = AgentSquadMCPServer()
    
    request = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "unknown/method",
        "params": {}
    }
    
    response = server.handle_request(request)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 4
    assert "error" in response
    assert response["error"]["code"] == -32601

def test_mcp_server_invalid_request_missing_jsonrpc():
    server = AgentSquadMCPServer()
    request = {
        "id": 5,
        "method": "tools/list",
        "params": {}
    }
    response = server.handle_request(request)
    assert response["error"]["code"] == -32600

def test_mcp_server_invalid_request_missing_method():
    server = AgentSquadMCPServer()
    request = {
        "jsonrpc": "2.0",
        "id": 6,
        "params": {}
    }
    response = server.handle_request(request)
    assert response["error"]["code"] == -32600

def test_mcp_server_invalid_params_missing_required_arg():
    server = AgentSquadMCPServer()
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "start_session",
            "arguments": {
                "host": "test-host"
                # Missing project_root, work_item, capability_report_hash
            }
        }
    }
    response = server.handle_request(request)
    assert response["error"]["code"] == -32602

