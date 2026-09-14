import pytest
import json
from integrations.mcp_client import MCPParser

def test_parse_valid_response():
    parser = MCPParser()
    raw_msg = '{"jsonrpc": "2.0", "id": 1, "result": {"foo": "bar"}}'
    msg = parser.parse_message(raw_msg)
    assert msg.get("id") == 1
    assert msg.get("result") == {"foo": "bar"}

def test_parse_error_response():
    parser = MCPParser()
    raw_msg = '{"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "Invalid Request"}}'
    msg = parser.parse_message(raw_msg)
    assert msg.get("id") == 2
    assert "error" in msg
    assert msg["error"]["code"] == -32600
