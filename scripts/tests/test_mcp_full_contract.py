import os
import json
import pytest
from integrations.mcp_server import AgentSquadMCPServer

def send_request(server, method, params=None):
    req = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        req["params"] = params
    res = server.handle_request(req)
    return res

def call_tool(server, name, args):
    return send_request(server, "tools/call", {"name": name, "arguments": args})

@pytest.fixture
def mcp_server():
    return AgentSquadMCPServer()

def test_tools_list_has_18_tools(mcp_server):
    res = send_request(mcp_server, "tools/list")
    assert "result" in res
    tools = res["result"]["tools"]
    
    expected_tools = [
        "start_session", "resume_session", "get_assignment", "get_context",
        "prepare_delegation", "preflight", "record_execution", "record_evidence",
        "create_handoff", "evaluate_gate", "report_failure", "doctor",
        "discover_skill", "curate_skill", "memory_query", "memory_propose_delta",
        "impact_analysis", "replay_receipt"
    ]
    names = [t["name"] for t in tools]
    for name in expected_tools:
        assert name in names, f"Missing tool: {name}"

def test_start_session(mcp_server):
    res = call_tool(mcp_server, "start_session", {
        "host": "test", 
        "project_root": os.getcwd(), 
        "work_item": "123", 
        "capability_report_hash": "hash"
    })
    assert not res.get("error")
    # Will fail right now because it's just a stub. 
    # Real implementation needs to return session_id, ttl, policy_hash
    content = res["result"]["content"][0]["text"]
    try:
        data = json.loads(content)
        assert "session_id" in data
    except Exception:
        pytest.fail(f"Could not parse start_session output as JSON with session_id: {content}")
