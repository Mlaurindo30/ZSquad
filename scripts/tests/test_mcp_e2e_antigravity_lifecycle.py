import json
import subprocess
import pytest

def send_request(proc, req_dict):
    req_str = json.dumps(req_dict) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()
    res_str = proc.stdout.readline()
    return json.loads(res_str)

def test_mcp_lifecycle():
    import os
    proc = subprocess.Popen(
        ["python", "mcp_runner.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), "..", "..", "integrations")
    )
    
    try:
        # 0) initialize
        req_init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Antigravity", "version": "1.0.0"}
            }
        }
        res_init = send_request(proc, req_init)
        assert "result" in res_init
        assert res_init["result"]["serverInfo"]["name"] == "agent-squad"

        # 0.1) notifications/initialized (no response expected)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # 1) tools/list
        req_list = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        res_list = send_request(proc, req_list)
        assert "result" in res_list
        tools = res_list["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "start_session" in tool_names
        
        # 2) tools/call -> start_session
        req_start = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "start_session",
                "arguments": {
                    "host": "antigravity",
                    "project_root": "C:\\Users\\miche\\OneDrive\\Documentos\\agent_squad",
                    "work_item": "WI-123",
                    "capability_report_hash": "hash123"
                }
            }
        }
        res_start = send_request(proc, req_start)
        assert "result" in res_start
        assert not res_start["result"]["isError"]
        
        # 3) tools/call -> get_assignment
        req_assign = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_assignment",
                "arguments": {
                    "session": "session123",
                    "objective_digest": "digest123"
                }
            }
        }
        res_assign = send_request(proc, req_assign)
        assert "result" in res_assign
        assert not res_assign["result"]["isError"]
        
        # 4) tools/call -> prepare_delegation
        req_deleg = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "prepare_delegation",
                "arguments": {
                    "session": "session123",
                    "target_role": "11-test-engineer",
                    "scope": "tests",
                    "action": "execute"
                }
            }
        }
        res_deleg = send_request(proc, req_deleg)
        assert "result" in res_deleg
        assert not res_deleg["result"]["isError"]
        
        # 5) tools/call -> record_execution
        req_exec = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "record_execution",
                "arguments": {
                    "session": "session123",
                    "briefing_hash": "bhash",
                    "output_refs": ["ref1"],
                    "receipt": {"status": "ok"}
                }
            }
        }
        res_exec = send_request(proc, req_exec)
        assert "result" in res_exec
        assert not res_exec["result"]["isError"]
        
        # 6) tools/call -> record_evidence
        req_evid = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "record_evidence",
                "arguments": {
                    "session": "session123",
                    "receipt_hash": "rhash",
                    "verifier_refs": ["vref1"]
                }
            }
        }
        res_evid = send_request(proc, req_evid)
        assert "result" in res_evid
        assert not res_evid["result"]["isError"]
        
        # 7) tools/call -> evaluate_gate
        req_gate = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "evaluate_gate",
                "arguments": {
                    "session": "session123",
                    "gate": "G5-quality",
                    "evidence_refs": ["vref1"]
                }
            }
        }
        res_gate = send_request(proc, req_gate)
        assert "result" in res_gate
        assert not res_gate["result"]["isError"]
        
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()
