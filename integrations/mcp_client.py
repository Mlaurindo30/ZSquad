import json
import asyncio
from typing import Dict, Any, List

class MCPParser:
    def parse_message(self, raw_msg: str) -> Dict[str, Any]:
        msg = json.loads(raw_msg)
        if msg.get("jsonrpc") != "2.0":
            raise ValueError("Invalid JSON-RPC version")
        return msg

class MCPClient:
    def __init__(self, command: str, args: List[str]) -> None:
        self.command = command
        self.args = args
        self.process = None
        self._msg_id = 1
        self.parser = MCPParser()
        self.server_info: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []
        self.resources: List[Dict[str, Any]] = []
        
    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        
    async def close(self) -> None:
        if self.process:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None

    async def __aenter__(self) -> 'MCPClient':
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
        
    async def _send_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        if params is None:
            params = {}
            
        request = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": method,
            "params": params
        }
        self._msg_id += 1
        
        req_str = json.dumps(request) + "\n"
        self.process.stdin.write(req_str.encode('utf-8'))
        await self.process.stdin.drain()
        
        line = await self.process.stdout.readline()
        if not line:
            raise ConnectionError("Server disconnected")
            
        response = self.parser.parse_message(line.decode('utf-8').strip())
        if "error" in response:
            raise RuntimeError(f"JSON-RPC Error: {response['error']}")
            
        return response.get("result")

    async def _send_notification(self, method: str, params: Dict[str, Any] = None) -> None:
        if params is None:
            params = {}
            
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        req_str = json.dumps(notification) + "\n"
        self.process.stdin.write(req_str.encode('utf-8'))
        await self.process.stdin.drain()
        
    async def initialize(self) -> Dict[str, Any]:
        return await self._send_request(
            "initialize", 
            {
                "protocolVersion": "1",
                "clientCapabilities": {},
                "clientInfo": {"name": "npr-client", "version": "1.0"}
            }
        )

    async def list_tools(self) -> Dict[str, Any]:
        return await self._send_request("tools/list")
        
    async def list_resources(self) -> Dict[str, Any]:
        return await self._send_request("resources/list")

    async def connect(self) -> None:
        await self.start()
        init_res = await self.initialize()
        self.server_info = init_res.get("serverInfo", {})
        
        await self._send_notification("notifications/initialized")
        
        tools_res = await self.list_tools()
        self.tools = tools_res.get("tools", [])
        
        resources_res = await self.list_resources()
        self.resources = resources_res.get("resources", [])
