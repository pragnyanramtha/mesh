from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any

from .common import clamp_limit
from .errors import AgentMeshError
from .service import AgentService

LOGGER = logging.getLogger("agentmesh.http")


class HubRequestHandler(BaseHTTPRequestHandler):
    server_version = "AgentMesh/0.1"

    @property
    def hub(self) -> AgentService:
        return self.server.agentmesh_service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if path == "/healthz":
                self._write(200, {"ok": True, "service": "agentmesh"})
                return
            principal = self._principal()
            if path == "/v1/me":
                if principal.admin:
                    self._write(200, {"id": "admin", "owner": "admin", "admin": True})
                else:
                    self._write(200, {"agent": self._agent_dict(principal.agent_id)})
                return
            if path == "/v1/agents":
                self._write(
                    200,
                    {"agents": self.hub.list_agents(principal, project=_first(query, "project"), capability=_first(query, "capability"), status=_first(query, "status"), limit=clamp_limit(_first(query, "limit")))},
                )
                return
            if path == "/v1/events":
                self._write(
                    200,
                    self.hub.read_events(principal, after=int(_first(query, "after") or 0), limit=clamp_limit(_first(query, "limit"))),
                )
                return
            if path == "/v1/contexts/search":
                project = _first(query, "project") or self.hub._principal_project(principal)
                self._write(200, {"contexts": self.hub.search_context(principal, project=project, query=_first(query, "q") or "", kind=_first(query, "kind"), limit=clamp_limit(_first(query, "limit"), 20, 100))})
                return
            parts = [item for item in path.split("/") if item]
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "tasks":
                self._write(200, {"task": self.hub.get_task(principal, parts[2])})
                return
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "contexts":
                self._write(200, {"context": self.hub.get_context(principal, parts[2])})
                return
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "artifacts":
                self._write(200, {"artifact": self.hub.get_artifact(principal, parts[2])})
                return
            raise AgentMeshError("Route not found", 404, "not_found")
        except Exception as error:
            self._handle_error(error)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            body = self._json_body()
            principal = self._principal()
            if path == "/v1/agents/register":
                result = self.hub.register_agent(
                    principal,
                    name=body.get("name", ""),
                    owner=body.get("owner", ""),
                    runtime=body.get("runtime", ""),
                    role=body.get("role", "worker"),
                    project=body.get("project", ""),
                    capabilities=body.get("capabilities") or [],
                )
                self._write(201, {"agent": result})
                return
            if path == "/v1/agents/heartbeat":
                self._write(200, {"agent": self.hub.heartbeat(principal, status=body.get("status", "online"))})
                return
            if path == "/v1/tasks":
                result = self.hub.create_task(principal, body)
                self._write(201 if result["created"] else 200, result)
                return
            if path == "/v1/tasks/claim-next":
                self._write(200, {"task": self.hub.claim_next(principal, project=body.get("project"), lease_seconds=body.get("lease_seconds", 300))})
                return
            if path == "/v1/messages":
                self._write(201, {"message": self.hub.send_message(principal, body)})
                return
            if path == "/v1/contexts":
                self._write(201, {"context": self.hub.publish_context(principal, body)})
                return
            if path == "/v1/artifacts":
                self._write(201, {"artifact": self.hub.publish_artifact(principal, body)})
                return
            if path.startswith("/v1/events/") and path.endswith("/ack"):
                event_id = path.split("/")[3]
                self._write(200, self.hub.acknowledge_event(principal, event_id))
                return
            parts = [item for item in path.split("/") if item]
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "tasks" and parts[3] == "claim":
                self._write(200, {"task": self.hub.claim_task(principal, parts[2], body.get("lease_seconds", 300))})
                return
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "tasks" and parts[3] == "update":
                self._write(200, {"task": self.hub.update_task(principal, parts[2], body)})
                return
            raise AgentMeshError("Route not found", 404, "not_found")
        except Exception as error:
            self._handle_error(error)

    def _principal(self):
        value = self.headers.get("Authorization", "")
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return self.hub.authenticate(None)
        return self.hub.authenticate(token.strip())

    def _agent_dict(self, agent_id: str) -> dict[str, Any]:
        with self.hub.db.read() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise AgentMeshError("Agent not found", 401, "unauthorized")
        from .models import row_to_agent

        return row_to_agent(row)

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 5_000_000:
            raise AgentMeshError("Request is too large", 413, "payload_too_large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentMeshError("Request body must be valid JSON", 400, "invalid_json") from error
        if not isinstance(value, dict):
            raise AgentMeshError("Request body must be a JSON object", 400, "invalid_json")
        return value

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _handle_error(self, error: Exception) -> None:
        if isinstance(error, AgentMeshError):
            status, code, message = error.status, error.code, error.message
        else:
            LOGGER.exception("Unhandled request error")
            status, code, message = 500, "internal_error", "Internal server error"
        self._write(status, {"error": {"code": code, "message": message}})


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


class AgentMeshHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: AgentService):
        super().__init__(address, HubRequestHandler)
        self.agentmesh_service = service


def serve(service: AgentService, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = AgentMeshHTTPServer((host, port), service)
    LOGGER.info("AgentMesh hub listening on http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
