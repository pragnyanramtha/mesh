from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from typing import Sequence

from .mcp import HubClient

LOGGER = logging.getLogger("agentmesh.worker")


def run_worker(
    client: HubClient,
    *,
    project: str | None,
    command: Sequence[str],
    poll_seconds: float = 2.0,
    once: bool = False,
) -> None:
    """Run a small managed-worker adapter.

    The command receives one JSON task object on stdin and should write a
    useful result to stdout. This makes the bridge usable with any CLI that
    can be wrapped by a tiny script, without pretending that every vendor has
    the same session-control API.
    """
    while True:
        try:
            client.call("POST", "/v1/agents/heartbeat", {"status": "idle"})
            response = client.call("POST", "/v1/tasks/claim-next", {"project": project, "lease_seconds": 300})
            task = response.get("task")
            if not task:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            client.call("POST", f"/v1/agents/heartbeat", {"status": "busy"})
            try:
                completed = subprocess.run(
                    list(command),
                    input=json.dumps(task, ensure_ascii=False),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                result = {
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-1_000_000:],
                    "stderr": completed.stderr[-1_000_000:],
                }
                status = "completed" if completed.returncode == 0 else "failed"
            except Exception as error:
                result = {"error": str(error)}
                status = "failed"
            client.call("POST", f"/v1/tasks/{task['id']}/update", {"status": status, "result": result})
            if once:
                return
        except Exception:
            LOGGER.exception("Worker loop failed; retrying")
            if once:
                raise
            time.sleep(poll_seconds)


def parse_command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise ValueError("Worker command cannot be empty")
    return command

