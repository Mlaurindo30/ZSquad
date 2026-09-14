"""
Component Contract

Definition: test_mcp_runner is the unit test suite for the mcp_runner stdio daemon.
Responsibility: Validates the behavior of the mcp_runner script, including happy paths and error handling.
Purpose: Ensures the MCP runner correctly processes valid requests and gracefully handles malformed input.
Failure Behavior: Fails the test execution if the mcp_runner does not return the expected JSON-RPC 2.0 responses or error codes.
Connections: Executes integrations/mcp_runner.py as a subprocess and asserts on its stdout/stderr.
"""

import subprocess
import json
import sys
import os

def test_mcp_runner_stdio_malformed_json():
    # Caminho base do projeto
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # Inicia o processo
    process = subprocess.Popen(
        [sys.executable, "integrations/mcp_runner.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
        text=True
    )
    
    # Send malformed JSON
    malformed_json = "{ invalid json"
    
    try:
        stdout_data, stderr_data = process.communicate(malformed_json + "\n", timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout_data, stderr_data = process.communicate()
        assert False, f"Process timed out. Stderr: {stderr_data}"
    
    assert process.returncode == 0, f"Process exited with {process.returncode}: {stderr_data}"
    
    lines = [line for line in stdout_data.strip().split("\n") if line.strip()]
    assert len(lines) > 0, "No output from server"
    
    try:
        res = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        assert False, f"Failed to parse JSON: {e}\nOutput: {lines[-1]}"
        
    assert res.get("jsonrpc") == "2.0"
    assert res.get("id") is None
    assert "error" in res, "Server did not return an error for malformed JSON"
    assert res["error"].get("code") == -32700
    assert res["error"].get("message") == "Parse error"

def test_mcp_runner_stdio():
    # Caminho base do projeto
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # Inicia o processo
    process = subprocess.Popen(
        [sys.executable, "integrations/mcp_runner.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
        text=True
    )
    
    # Payload de teste
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    }
    
    # Envia o request via stdin e obtém a resposta (com timeout para não travar)
    try:
        stdout_data, stderr_data = process.communicate(json.dumps(req) + "\n", timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout_data, stderr_data = process.communicate()
        assert False, f"Process timed out. Stderr: {stderr_data}"
    
    assert process.returncode == 0, f"Process exited with {process.returncode}: {stderr_data}"
    
    # stdout_data deve conter uma linha de resposta
    lines = [line for line in stdout_data.strip().split("\n") if line.strip()]
    assert len(lines) > 0, "No output from server"
    
    # Valida o json da última linha
    try:
        res = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        assert False, f"Failed to parse JSON: {e}\nOutput: {lines[-1]}"
        
    assert res.get("jsonrpc") == "2.0"
    assert res.get("id") == 1
    assert "error" not in res, f"Server returned error: {res.get('error')}"
