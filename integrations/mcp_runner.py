"""
Component Contract

Definition: mcp_runner is the main entry point for the Agent Squad MCP server running over standard input/output.
Responsibility: Reads JSON-RPC 2.0 requests from stdin, passes them to the MCP server, and writes responses to stdout.
Purpose: Enables MCP clients to run the Agent Squad MCP server as a subprocess using stdio transport.
Failure Behavior: Returns valid JSON-RPC 2.0 error responses (-32700) for malformed JSON input and handles internal server errors safely.
Connections: Integrates with AgentSquadMCPServer and communicates with any compliant MCP client via stdio.
"""

import sys
import json
import logging
try:
    from integrations.mcp_server import AgentSquadMCPServer
except ModuleNotFoundError:
    from mcp_server import AgentSquadMCPServer

logging.basicConfig(level=logging.ERROR)

def main():
    server = AgentSquadMCPServer()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            res = server.handle_request(req)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except Exception as e:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": str(e)
                }
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
