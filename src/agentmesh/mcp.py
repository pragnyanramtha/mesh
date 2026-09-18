from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOLS: list[dict[str, Any]] = [
    {
        "name": "agents.find",
        "description": "Find permitted agents in a project by capability or status.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "capability": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "tasks.create",
        "description": "Create durable, bounded work for another agent. Returns immediately with a task ID.",
        "inputSchema": {"type": "object", "required": ["project", "title", "goal"], "properties": {"project": {"type": "string"}, "title": {"type": "string"}, "goal": {"type": "string"}, "assignee_agent": {"type": "string"}, "input": {"type": "object"}, "constraints": {"type": "object"}, "base_revision": {"type": "string"}, "idempotency_key": {"type": "string"}}},
    },
    {
        "name": "tasks.get",
        "description": "Read authoritative task state, attempts, lease, and result.",
        "inputSchema": {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}}},
    },
    {
        "name": "tasks.claim",
        "description": "Claim a task or the next available task in the agent's project.",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "project": {"type": "string"}, "lease_seconds": {"type": "integer"}}},
    },
    {
        "name": "tasks.update",
        "description": "Report accepted, running, blocked, completed, failed, or cancelled task state.",
        "inputSchema": {"type": "object", "required": ["task_id", "status"], "properties": {"task_id": {"type": "string"}, "status": {"type": "string"}, "result": {"type": "object"}}},
    },
    {
        "name": "messages.send",
        "description": "Send a durable follow-up to a permitted agent.",
        "inputSchema": {"type": "object", "required": ["recipient_agent", "body"], "properties": {"recipient_agent": {"type": "string"}, "body": {"type": "string"}, "task_id": {"type": "string"}, "kind": {"type": "string"}}},
    },
    {
        "name": "events.read",
        "description": "Read durable incoming events after a cursor. The hub marks delivery separately from acknowledgement.",
        "inputSchema": {"type": "object", "properties": {"after": {"type": "integer"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "events.ack",
        "description": "Acknowledge one received event after processing it.",
        "inputSchema": {"type": "object", "required": ["event_id"], "properties": {"event_id": {"type": "string"}}},
    },
    {
        "name": "context.search",
        "description": "Search approved project knowledge without loading full old transcripts.",
        "inputSchema": {"type": "object", "required": ["project"], "properties": {"project": {"type": "string"}, "q": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "context.read",
        "description": "Read one approved context record by ID.",
        "inputSchema": {"type": "object", "required": ["context_id"], "properties": {"context_id": {"type": "string"}}},
    },
    {
        "name": "context.publish",
        "description": "Publish a project decision, finding, test result, or other approved context record.",
        "inputSchema": {"type": "object", "required": ["project", "title", "content"], "properties": {"project": {"type": "string"}, "kind": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}, "visibility": {"type": "string"}, "source_revision": {"type": "string"}}},
    },
    {
        "name": "artifacts.publish",
        "description": "Publish an immutable patch, test log, or other bounded artifact.",
        "inputSchema": {"type": "object", "required": ["project", "name", "content"], "properties": {"project": {"type": "string"}, "name": {"type": "string"}, "media_type": {"type": "string"}, "content": {"type": "string"}, "metadata": {"type": "object"}}},
    },
    {
        "name": "artifacts.get",
        "description": "Fetch an artifact by ID within the project scope.",
        "inputSchema": {"type": "object", "required": ["artifact_id"], "properties": {"artifact_id": {"type": "string"}}},
    },
    {
        "name": "agent.heartbeat",
        "description": "Update the calling agent's availability state.",
        "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
]


class HubClient:
    def __init__(self, hub: str, token: str):
        self.hub = hub.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> Any:
        url = self.hub + path
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, method=method, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
            except Exception:
                detail = {"error": {"message": str(error)}}
            raise RuntimeError(json.dumps(detail, ensure_ascii=False)) from error
        except URLError as error:
            raise RuntimeError(f"Cannot reach AgentMesh hub: {error.reason}") from error


def dispatch(client: HubClient, name: str, arguments: dict[str, Any]) -> Any:
    if name == "agents.find":
        return client.call("GET", "/v1/agents", query=arguments)
    if name == "tasks.create":
        return client.call("POST", "/v1/tasks", arguments)
    if name == "tasks.get":
        return client.call("GET", f"/v1/tasks/{arguments['task_id']}")
    if name == "tasks.claim":
        task_id = arguments.get("task_id")
        if task_id:
            return client.call("POST", f"/v1/tasks/{task_id}/claim", {"lease_seconds": arguments.get("lease_seconds", 300)})
        return client.call("POST", "/v1/tasks/claim-next", arguments)
    if name == "tasks.update":
        return client.call("POST", f"/v1/tasks/{arguments['task_id']}/update", arguments)
    if name == "messages.send":
        return client.call("POST", "/v1/messages", arguments)
    if name == "events.read":
        return client.call("GET", "/v1/events", query=arguments)
    if name == "events.ack":
        return client.call("POST", f"/v1/events/{arguments['event_id']}/ack", {})
    if name == "context.search":
        return client.call("GET", "/v1/contexts/search", query=arguments)
    if name == "context.read":
        return client.call("GET", f"/v1/contexts/{arguments['context_id']}")
    if name == "context.publish":
        return client.call("POST", "/v1/contexts", arguments)
    if name == "artifacts.publish":
        return client.call("POST", "/v1/artifacts", arguments)
    if name == "artifacts.get":
        return client.call("GET", f"/v1/artifacts/{arguments['artifact_id']}")
    if name == "agent.heartbeat":
        return client.call("POST", "/v1/agents/heartbeat", arguments)
    raise RuntimeError(f"Unknown tool: {name}")


def handle_rpc(client: HubClient, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}, "resources": {}}, "serverInfo": {"name": "agentmesh", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = dispatch(client, name, arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "isError": False}}
        except Exception as error:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(error)}], "isError": True}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def run_stdio(client: HubClient) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_rpc(client, request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AgentMesh's stdio MCP server")
    parser.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("AGENTMESH_TOKEN"))
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token or AGENTMESH_TOKEN is required")
    run_stdio(HubClient(args.hub, args.token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

