from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from .common import new_token
from .db import Database
from .http import serve
from .mcp import HubClient, main as mcp_main
from .service import AgentService
from .worker import parse_command, run_worker


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _request(client: HubClient, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> object:
    return client.call(method, path, payload, query)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentmesh", description="Run a small vendor-neutral coordination hub for coding agents")
    parser.add_argument("--verbose", action="store_true", help="Enable hub logs")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create the SQLite database")
    init.add_argument("--db", default="agentmesh.db")

    start = commands.add_parser("start", help="Start the HTTP hub")
    start.add_argument("--db", default=os.environ.get("AGENTMESH_DB", "agentmesh.db"))
    start.add_argument("--host", default=os.environ.get("AGENTMESH_HOST", "127.0.0.1"))
    start.add_argument("--port", type=int, default=int(os.environ.get("AGENTMESH_PORT", "8765")))
    start.add_argument("--admin-token", default=os.environ.get("AGENTMESH_ADMIN_TOKEN"))

    status = commands.add_parser("status", help="Check a running hub")
    status.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))

    register = commands.add_parser("register", help="Register an agent and print its one-time token")
    register.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    register.add_argument("--admin-token", default=os.environ.get("AGENTMESH_ADMIN_TOKEN"))
    register.add_argument("--name", required=True)
    register.add_argument("--owner", required=True)
    register.add_argument("--runtime", required=True)
    register.add_argument("--role", default="worker")
    register.add_argument("--project", required=True)
    register.add_argument("--capability", action="append", default=[])

    mcp = commands.add_parser("mcp", help="Run the stdio MCP server")
    mcp.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    mcp.add_argument("--token", default=os.environ.get("AGENTMESH_TOKEN"))

    worker = commands.add_parser("worker", help="Run a managed worker around a CLI command")
    worker.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    worker.add_argument("--token", default=os.environ.get("AGENTMESH_TOKEN"))
    worker.add_argument("--project")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--command", dest="worker_command", required=True, help="Command that reads a task JSON object on stdin")

    task = commands.add_parser("task", help="Inspect or create tasks from a shell")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    task_create.add_argument("--token", default=os.environ.get("AGENTMESH_TOKEN"))
    task_create.add_argument("--project", required=True)
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--goal", required=True)
    task_create.add_argument("--assignee-agent")
    task_create.add_argument("--idempotency-key")
    task_get = task_sub.add_parser("get")
    task_get.add_argument("task_id")
    task_get.add_argument("--hub", default=os.environ.get("AGENTMESH_HUB", "http://127.0.0.1:8765"))
    task_get.add_argument("--token", default=os.environ.get("AGENTMESH_TOKEN"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.command == "init":
        Database(args.db)
        print(f"Initialized {Path(args.db).expanduser()}")
        return 0
    if args.command == "start":
        admin_token = args.admin_token or new_token()
        if not args.admin_token:
            print(f"Generated admin token (save it): {admin_token}", file=sys.stderr)
        service = AgentService(Database(args.db), admin_token)
        serve(service, args.host, args.port)
        return 0
    if args.command == "status":
        request = Request(args.hub.rstrip("/") + "/healthz")
        with urlopen(request, timeout=5) as response:
            _json(json.loads(response.read().decode("utf-8")))
        return 0
    if args.command == "register":
        if not args.admin_token:
            parser.error("register needs --admin-token or AGENTMESH_ADMIN_TOKEN")
        result = HubClient(args.hub, args.admin_token).call("POST", "/v1/agents/register", {"name": args.name, "owner": args.owner, "runtime": args.runtime, "role": args.role, "project": args.project, "capabilities": args.capability})
        _json(result)
        return 0
    if args.command == "mcp":
        if not args.token:
            parser.error("mcp needs --token or AGENTMESH_TOKEN")
        return mcp_main(["--hub", args.hub, "--token", args.token])
    if args.command == "worker":
        if not args.token:
            parser.error("worker needs --token or AGENTMESH_TOKEN")
        run_worker(HubClient(args.hub, args.token), project=args.project, command=parse_command(args.worker_command), poll_seconds=args.poll_seconds, once=args.once)
        return 0
    if args.command == "task":
        if not args.token:
            parser.error("task commands need --token or AGENTMESH_TOKEN")
        client = HubClient(args.hub, args.token)
        if args.task_command == "create":
            payload = {"project": args.project, "title": args.title, "goal": args.goal}
            if args.assignee_agent:
                payload["assignee_agent"] = args.assignee_agent
            if args.idempotency_key:
                payload["idempotency_key"] = args.idempotency_key
            _json(client.call("POST", "/v1/tasks", payload))
        else:
            _json(client.call("GET", f"/v1/tasks/{args.task_id}"))
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
