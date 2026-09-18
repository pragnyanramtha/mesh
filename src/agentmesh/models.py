from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import json_loads


@dataclass(frozen=True)
class Principal:
    """The identity attached to an authenticated request.

    The request body is never trusted for this identity. It is resolved from
    the bearer token by the hub.
    """

    agent_id: str
    owner: str
    admin: bool = False


def row_to_agent(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "owner": row["owner"],
        "runtime": row["runtime"],
        "role": row["role"],
        "project": row["project"],
        "status": row["status"],
        "capabilities": json_loads(row["capabilities_json"], []),
        "last_seen": row["last_seen"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_task(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "goal": row["goal"],
        "requester_agent": row["requester_agent"],
        "assignee_agent": row["assignee_agent"],
        "status": row["status"],
        "input": json_loads(row["input_json"], {}),
        "result": json_loads(row["result_json"], None),
        "constraints": json_loads(row["constraints_json"], {}),
        "base_revision": row["base_revision"],
        "idempotency_key": row["idempotency_key"],
        "attempt": row["attempt"],
        "lease_agent": row["lease_agent"],
        "lease_expires_at": row["lease_expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def row_to_context(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project": row["project"],
        "kind": row["kind"],
        "title": row["title"],
        "content": row["content"],
        "visibility": row["visibility"],
        "source_agent": row["source_agent"],
        "source_revision": row["source_revision"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

