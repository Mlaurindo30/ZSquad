import json
import os
from typing import Dict, Any

try:
    from integrations.mcp_session_store import SessionStore
    from integrations.mcp_db_client import DBClient
    from integrations.resolvers.session_manager import start_session, resume_session
    from integrations.resolvers.assignment_resolver import (
        get_assignment,
        prepare_delegation,
        create_handoff,
    )
    from integrations.resolvers.context_engine import (
        get_context,
        memory_query,
        memory_propose_delta,
    )
    from integrations.resolvers.gate_evaluator import evaluate_gate
    from integrations.resolvers.impact_analyzer import preflight, impact_analysis
    from integrations.resolvers.doctor import doctor
    from integrations.resolvers.execution_recorder import (
        record_execution,
        record_evidence,
        report_failure,
        replay_receipt,
    )
    from integrations.resolvers.skill_manager import discover_skill, curate_skill
    from integrations.resolvers import ResolverContext
except ModuleNotFoundError:
    from mcp_session_store import SessionStore
    from mcp_db_client import DBClient
    from resolvers.session_manager import start_session, resume_session
    from resolvers.assignment_resolver import (
        get_assignment,
        prepare_delegation,
        create_handoff,
    )
    from resolvers.context_engine import get_context, memory_query, memory_propose_delta
    from resolvers.gate_evaluator import evaluate_gate
    from resolvers.impact_analyzer import preflight, impact_analysis
    from resolvers.doctor import doctor
    from resolvers.execution_recorder import (
        record_execution,
        record_evidence,
        report_failure,
        replay_receipt,
    )
    from resolvers.skill_manager import discover_skill, curate_skill
    from resolvers import ResolverContext


class AgentSquadMCPServer:
    def __init__(self) -> None:
        self.session_store = SessionStore()
        self.db = DBClient("banco/squad.db")
        self.ctx = ResolverContext(
            db_path=self.db.db_path,
            config_dir=os.path.join(os.path.dirname(__file__), "..", "config"),
        )

        self.tools = [
            {
                "name": "start_session",
                "description": "Starts a new session",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "project_root": {"type": "string"},
                        "work_item": {"type": "string"},
                        "capability_report_hash": {"type": "string"},
                    },
                    "required": [
                        "host",
                        "project_root",
                        "work_item",
                        "capability_report_hash",
                    ],
                },
            },
            {
                "name": "resume_session",
                "description": "Resumes an existing session",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "last_revision": {"type": "string"},
                    },
                    "required": ["session", "last_revision"],
                },
            },
            {
                "name": "get_assignment",
                "description": "Gets an assignment",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "objective_digest": {"type": "string"},
                    },
                    "required": ["session", "objective_digest"],
                },
            },
            {
                "name": "get_context",
                "description": "Gets context from memory facts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "topics": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "topics"],
                },
            },
            {
                "name": "prepare_delegation",
                "description": "Prepares a delegation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "target_role": {"type": "string"},
                        "scope": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["session", "target_role", "scope", "action"],
                },
            },
            {
                "name": "preflight",
                "description": "Validates paths and sessions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "briefing_hash": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "briefing_hash"],
                },
            },
            {
                "name": "record_execution",
                "description": "Records an execution receipt",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "briefing_hash": {"type": "string"},
                        "output_refs": {"type": "array", "items": {"type": "string"}},
                        "receipt": {"type": "object"},
                    },
                    "required": ["session", "briefing_hash", "output_refs", "receipt"],
                },
            },
            {
                "name": "record_evidence",
                "description": "Records evidence",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "receipt_hash": {"type": "string"},
                        "verifier_refs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "receipt_hash", "verifier_refs"],
                },
            },
            {
                "name": "create_handoff",
                "description": "Creates a handoff",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "evidence_hash": {"type": "string"},
                    },
                    "required": ["session", "evidence_hash"],
                },
            },
            {
                "name": "evaluate_gate",
                "description": "Evaluates a gate",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "gate": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "gate", "evidence_refs"],
                },
            },
            {
                "name": "report_failure",
                "description": "Reports a failure",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["session", "reason"],
                },
            },
            {
                "name": "doctor",
                "description": "Health check",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "discover_skill",
                "description": "Discovers a new skill",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["session", "metadata"],
                },
            },
            {
                "name": "curate_skill",
                "description": "Curates a skill intake",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "intake_id": {"type": "string"},
                        "decision": {"type": "string"},
                    },
                    "required": ["session", "intake_id", "decision"],
                },
            },
            {
                "name": "memory_query",
                "description": "Queries memory facts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "topics": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "topics"],
                },
            },
            {
                "name": "memory_propose_delta",
                "description": "Proposes a delta to memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "proposal": {"type": "object"},
                    },
                    "required": ["session", "proposal"],
                },
            },
            {
                "name": "impact_analysis",
                "description": "Analyzes blast radius",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["session", "paths"],
                },
            },
            {
                "name": "replay_receipt",
                "description": "Replays an execution receipt",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string"},
                        "receipt_hash": {"type": "string"},
                    },
                    "required": ["session", "receipt_hash"],
                },
            },
        ]

    def _format_success(self, req_id: Any, result: Any) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "isError": False,
            },
        }

    def _format_error(self, req_id: Any, code: int, message: str) -> Dict[str, Any]:
        res = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
        if req_id is not None:
            res["id"] = req_id
        return res

    def handle_request(self, request: Dict[str, Any]) -> Any:
        req_id = request.get("id")

        if request.get("jsonrpc") != "2.0" or "method" not in request:
            return self._format_error(req_id, -32600, "Invalid Request")

        method = request.get("method")

        if method == "initialize":
            params = request.get("params", {})
            client_proto = params.get("protocolVersion", "2024-11-05")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": client_proto,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "agent-squad", "version": "1.0.0"},
                    "instructions": "Agent Squad delivery orchestration MCP server.",
                },
            }
        elif method in ("notifications/initialized", "initialized"):
            return None
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tools}}
        elif method == "tools/call":
            params = request.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})

            tool = next((t for t in self.tools if t["name"] == name), None)
            if not tool:
                return self._format_error(req_id, -32601, f"Tool {name} not found")

            for req_arg in tool["inputSchema"].get("required", []):
                if req_arg not in args:
                    return self._format_error(
                        req_id,
                        -32602,
                        f"Invalid params: missing required argument '{req_arg}'",
                    )

            try:
                result = self._route_tool(name, args)
                return self._format_success(req_id, result)
            except Exception as e:
                return self._format_error(req_id, -32603, str(e))

        return self._format_error(req_id, -32601, "Method not found")

    def _route_tool(self, name: str, args: Dict[str, Any]) -> Any:
        # Route to resolvers
        if name == "start_session":
            return start_session(args, self.ctx, self.session_store)
        elif name == "resume_session":
            return resume_session(args, self.ctx, self.session_store)
        elif name == "get_assignment":
            return get_assignment(args, self.ctx, self.session_store)
        elif name == "get_context":
            return get_context(args, self.ctx, self.session_store, self.db)
        elif name == "prepare_delegation":
            return prepare_delegation(args, self.ctx, self.session_store)
        elif name == "preflight":
            return preflight(args, self.ctx, self.session_store)
        elif name == "record_execution":
            return record_execution(args, self.ctx, self.session_store, self.db)
        elif name == "record_evidence":
            return record_evidence(args, self.ctx, self.session_store, self.db)
        elif name == "create_handoff":
            return create_handoff(args, self.ctx, self.session_store, self.db)
        elif name == "evaluate_gate":
            return evaluate_gate(args, self.ctx, self.session_store, self.db)
        elif name == "report_failure":
            return report_failure(args, self.ctx, self.session_store, self.db)
        elif name == "doctor":
            return doctor(args, self.ctx, self.session_store)
        elif name == "discover_skill":
            return discover_skill(args, self.ctx, self.session_store, self.db)
        elif name == "curate_skill":
            return curate_skill(args, self.ctx, self.session_store, self.db)
        elif name == "memory_query":
            return memory_query(args, self.ctx, self.session_store, self.db)
        elif name == "memory_propose_delta":
            return memory_propose_delta(args, self.ctx, self.session_store, self.db)
        elif name == "impact_analysis":
            return impact_analysis(args, self.ctx, self.session_store, self.db)
        elif name == "replay_receipt":
            return replay_receipt(args, self.ctx, self.session_store)

        return {"status": "success", "message": f"Successfully called {name}"}
