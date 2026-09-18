from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .common import clamp_limit, json_dumps, json_loads, new_id, new_token, now_iso, token_digest, unix_time
from .db import Database
from .errors import Conflict, Forbidden, NotFound, Unauthorized, AgentMeshError
from .models import Principal, row_to_agent, row_to_context, row_to_task


TASK_STATUSES = {"pending", "accepted", "running", "blocked", "completed", "failed", "cancelled"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
MAX_TEXT = 2_000_000


class AgentService:
    """Business rules for the hub.

    The HTTP and MCP layers are deliberately thin. This class is also easy to
    call from tests or a local runner without opening a network listener.
    """

    def __init__(self, database: Database, admin_token: str):
        if not admin_token:
            raise ValueError("An admin token is required")
        self.db = database
        self.admin_token_digest = token_digest(admin_token)

    # ---------- authentication and registration ----------

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise Unauthorized()
        digest = token_digest(token)
        if digest == self.admin_token_digest:
            return Principal(agent_id="admin", owner="admin", admin=True)
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT id, owner FROM agents WHERE token_hash = ?", (digest,)
            ).fetchone()
        if row is None:
            raise Unauthorized("Invalid bearer token")
        return Principal(agent_id=row["id"], owner=row["owner"], admin=False)

    def register_agent(
        self,
        principal: Principal,
        *,
        name: str,
        owner: str,
        runtime: str,
        role: str = "worker",
        project: str,
        capabilities: Iterable[str] = (),
    ) -> dict[str, Any]:
        self._require_admin(principal)
        for value, label in ((name, "name"), (owner, "owner"), (runtime, "runtime"), (project, "project")):
            if not isinstance(value, str) or not value.strip():
                raise AgentMeshError(f"{label} is required")
        agent_id = new_id("agent")
        token = new_token()
        timestamp = now_iso()
        capabilities_list = sorted({str(item) for item in capabilities if str(item).strip()})
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO agents
                   (id, name, owner, runtime, role, project, status, capabilities_json,
                    token_hash, last_seen, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    name.strip(),
                    owner.strip(),
                    runtime.strip(),
                    role.strip() or "worker",
                    project.strip(),
                    json_dumps(capabilities_list),
                    token_digest(token),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        result = row_to_agent(row)
        result["token"] = token
        return result

    def list_agents(
        self,
        principal: Principal,
        *,
        project: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        project = project or (None if principal.admin else self._principal_project(principal))
        if not principal.admin and project != self._principal_project(principal):
            raise Forbidden("Agents are scoped to a project")
        query = "SELECT * FROM agents"
        params: list[Any] = []
        if project:
            query += " WHERE project = ?"
            params.append(project)
        if status:
            query += " AND" if project else " WHERE"
            query += " status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(clamp_limit(limit))
        with self.db.read() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        agents = [row_to_agent(row) for row in rows]
        if capability:
            agents = [agent for agent in agents if capability in agent["capabilities"]]
        return agents

    def heartbeat(self, principal: Principal, *, status: str = "online") -> dict[str, Any]:
        if principal.admin:
            return {"status": "online", "admin": True}
        if status not in {"online", "idle", "busy", "blocked", "offline"}:
            raise AgentMeshError("Invalid agent status")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE agents SET status = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (status, timestamp, timestamp, principal.agent_id),
            ).rowcount
            if not updated:
                raise Unauthorized("Agent is no longer registered")
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (principal.agent_id,)).fetchone()
        return row_to_agent(row)

    # ---------- tasks ----------

    def create_task(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("Register an agent and use its token to create work")
        required = ("project", "title", "goal")
        for field in required:
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise AgentMeshError(f"{field} is required")
        project = payload["project"].strip()
        if not principal.admin and project != self._principal_project(principal):
            raise Forbidden("You can only create tasks in your project")
        idempotency_key = str(payload.get("idempotency_key") or new_id("request"))
        assignee = payload.get("assignee_agent") or None
        if assignee:
            self._assert_agent_in_project(assignee, project)
        timestamp = now_iso()
        task_id = new_id("task")
        status = "accepted" if assignee else "pending"
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM tasks WHERE requester_agent = ? AND idempotency_key = ?",
                (principal.agent_id, idempotency_key),
            ).fetchone()
            if existing:
                return {"created": False, "task": row_to_task(existing)}
            connection.execute(
                """INSERT INTO tasks
                   (id, project, title, goal, requester_agent, assignee_agent, status,
                    input_json, constraints_json, base_revision, idempotency_key,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    project,
                    payload["title"].strip(),
                    payload["goal"].strip(),
                    principal.agent_id,
                    assignee,
                    status,
                    json_dumps(payload.get("input") or {}),
                    json_dumps(payload.get("constraints") or {}),
                    payload.get("base_revision"),
                    idempotency_key,
                    timestamp,
                    timestamp,
                ),
            )
            if assignee:
                self._emit(
                    connection,
                    recipient_agent=assignee,
                    event_type="task.created",
                    entity_id=task_id,
                    payload={"task_id": task_id, "status": status},
                )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return {"created": True, "task": row_to_task(row)}

    def get_task(self, principal: Principal, task_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFound("Task not found")
        self._assert_task_access(principal, row)
        return row_to_task(row)

    def claim_task(self, principal: Principal, task_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("The admin token cannot claim work")
        now = unix_time()
        lease_seconds = max(10, min(int(lease_seconds), 86_400))
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise NotFound("Task not found")
            self._assert_task_access(principal, row)
            eligible = row["assignee_agent"] in (None, principal.agent_id)
            lease_expired = row["lease_expires_at"] is None or row["lease_expires_at"] <= now
            if not eligible:
                raise Forbidden("Task is assigned to another agent")
            if row["status"] in TERMINAL_TASK_STATUSES:
                raise Conflict("Task is already finished")
            if row["lease_agent"] not in (None, principal.agent_id) and not lease_expired:
                raise Conflict("Task is leased by another attempt")
            timestamp = now_iso()
            connection.execute(
                """UPDATE tasks SET assignee_agent = ?, status = 'running',
                   lease_agent = ?, lease_expires_at = ?, attempt = attempt + 1,
                   updated_at = ? WHERE id = ?""",
                (principal.agent_id, principal.agent_id, now + lease_seconds, timestamp, task_id),
            )
            self._emit(
                connection,
                recipient_agent=row["requester_agent"],
                event_type="task.claimed",
                entity_id=task_id,
                payload={"task_id": task_id, "agent_id": principal.agent_id},
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(updated)

    def claim_next(self, principal: Principal, project: str | None = None, lease_seconds: int = 300) -> dict[str, Any] | None:
        if principal.admin:
            raise Forbidden("The admin token cannot claim work")
        project = project or self._principal_project(principal)
        if project != self._principal_project(principal):
            raise Forbidden("You can only claim work in your project")
        now = unix_time()
        with self.db.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM tasks
                   WHERE project = ? AND status IN ('pending', 'accepted', 'blocked')
                     AND (assignee_agent IS NULL OR assignee_agent = ?)
                     AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                   ORDER BY CASE WHEN assignee_agent = ? THEN 0 ELSE 1 END, created_at
                   LIMIT 1""",
                (project, principal.agent_id, now, principal.agent_id),
            ).fetchone()
            if row is None:
                return None
            lease_seconds = max(10, min(int(lease_seconds), 86_400))
            timestamp = now_iso()
            connection.execute(
                """UPDATE tasks SET assignee_agent = ?, status = 'running',
                   lease_agent = ?, lease_expires_at = ?, attempt = attempt + 1,
                   updated_at = ? WHERE id = ?""",
                (principal.agent_id, principal.agent_id, now + lease_seconds, timestamp, row["id"]),
            )
            self._emit(
                connection,
                recipient_agent=row["requester_agent"],
                event_type="task.claimed",
                entity_id=row["id"],
                payload={"task_id": row["id"], "agent_id": principal.agent_id},
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
        return row_to_task(updated)

    def update_task(self, principal: Principal, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        status = payload.get("status")
        if status not in TASK_STATUSES:
            raise AgentMeshError(f"status must be one of: {', '.join(sorted(TASK_STATUSES))}")
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise NotFound("Task not found")
            self._assert_task_access(principal, row)
            if not principal.admin:
                if principal.agent_id == row["requester_agent"]:
                    if status not in {"cancelled"}:
                        raise Forbidden("The requester may only cancel a task")
                elif principal.agent_id == row["assignee_agent"]:
                    if status == "cancelled":
                        raise Forbidden("The worker cannot cancel a task")
                else:
                    raise Forbidden("Only the requester or assignee may update a task")
            if row["status"] in TERMINAL_TASK_STATUSES and status != row["status"]:
                raise Conflict("A finished task cannot be reopened")
            timestamp = now_iso()
            completed_at = timestamp if status in TERMINAL_TASK_STATUSES else row["completed_at"]
            result = payload.get("result") if "result" in payload else json_loads(row["result_json"], None)
            connection.execute(
                """UPDATE tasks SET status = ?, result_json = ?, lease_expires_at = NULL,
                   updated_at = ?, completed_at = ? WHERE id = ?""",
                (status, json_dumps(result) if result is not None else None, timestamp, completed_at, task_id),
            )
            recipients = {row["requester_agent"], row["assignee_agent"]}
            recipients.discard(None)
            event_type = "task.completed" if status in TERMINAL_TASK_STATUSES else "task.updated"
            for recipient in recipients:
                if recipient != principal.agent_id:
                    self._emit(
                        connection,
                        recipient_agent=recipient,
                        event_type=event_type,
                        entity_id=task_id,
                        payload={"task_id": task_id, "status": status},
                    )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(updated)

    # ---------- messages and event delivery ----------

    def send_message(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("Register an agent and use its token to send messages")
        recipient = payload.get("recipient_agent")
        body = payload.get("body")
        if not isinstance(recipient, str) or not recipient:
            raise AgentMeshError("recipient_agent is required")
        if not isinstance(body, str) or not body.strip():
            raise AgentMeshError("body is required")
        if len(body) > MAX_TEXT:
            raise AgentMeshError("Message is too large")
        recipient_row = self._get_agent(recipient)
        if not principal.admin:
            if self._principal_project(principal) != recipient_row["project"]:
                raise Forbidden("Agents may message only within their project")
        task_id = payload.get("task_id")
        if task_id:
            with self.db.read() as connection:
                task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                raise NotFound("Task not found")
            self._assert_task_access(principal, task_row)
        message_id = new_id("msg")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO messages (id, task_id, sender_agent, recipient_agent, kind, body, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message_id, task_id, principal.agent_id, recipient, payload.get("kind", "message"), body, timestamp),
            )
            self._emit(
                connection,
                recipient_agent=recipient,
                event_type="message.created",
                entity_id=message_id,
                payload={"message_id": message_id, "task_id": task_id, "sender_agent": principal.agent_id},
            )
        return {
            "id": message_id,
            "task_id": task_id,
            "sender_agent": principal.agent_id,
            "recipient_agent": recipient,
            "kind": payload.get("kind", "message"),
            "body": body,
            "created_at": timestamp,
        }

    def read_events(self, principal: Principal, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("The admin token cannot consume agent events")
        with self.db.transaction() as connection:
            rows = connection.execute(
                """SELECT * FROM events WHERE recipient_agent = ? AND seq > ?
                   ORDER BY seq LIMIT ?""",
                (principal.agent_id, max(0, int(after)), clamp_limit(limit)),
            ).fetchall()
            if rows:
                timestamp = now_iso()
                connection.executemany(
                    "UPDATE events SET delivered_at = COALESCE(delivered_at, ?) WHERE seq = ?",
                    [(timestamp, row["seq"]) for row in rows],
                )
        events = [
            {
                "seq": row["seq"],
                "id": row["id"],
                "event_type": row["event_type"],
                "entity_id": row["entity_id"],
                "payload": json_loads(row["payload_json"], {}),
                "created_at": row["created_at"],
                "delivered_at": row["delivered_at"] or now_iso(),
                "acknowledged_at": row["acknowledged_at"],
            }
            for row in rows
        ]
        return {"events": events, "next_cursor": events[-1]["seq"] if events else max(0, int(after))}

    def acknowledge_event(self, principal: Principal, event_id: str) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("The admin token cannot acknowledge agent events")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE events SET acknowledged_at = COALESCE(acknowledged_at, ?) WHERE id = ? AND recipient_agent = ?",
                (timestamp, event_id, principal.agent_id),
            ).rowcount
        if not updated:
            raise NotFound("Event not found")
        return {"id": event_id, "acknowledged_at": timestamp}

    # ---------- project context and artifacts ----------

    def publish_context(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("Register an agent and use its token to publish context")
        project = payload.get("project")
        title = payload.get("title")
        content = payload.get("content")
        visibility = payload.get("visibility", "project")
        if not all(isinstance(value, str) and value.strip() for value in (project, title, content)):
            raise AgentMeshError("project, title, and content are required")
        if visibility not in {"project", "private"}:
            raise AgentMeshError("visibility must be project or private")
        if not principal.admin and project != self._principal_project(principal):
            raise Forbidden("You can only publish context in your project")
        if len(content) > MAX_TEXT:
            raise AgentMeshError("Context is too large")
        context_id = new_id("ctx")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO contexts
                   (id, project, kind, title, content, visibility, source_agent,
                    source_revision, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    context_id,
                    project,
                    payload.get("kind", "note"),
                    title.strip(),
                    content,
                    visibility,
                    principal.agent_id,
                    payload.get("source_revision"),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute("SELECT * FROM contexts WHERE id = ?", (context_id,)).fetchone()
        return row_to_context(row)

    def search_context(self, principal: Principal, *, project: str, query: str, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if not project:
            raise AgentMeshError("project is required")
        if not principal.admin and project != self._principal_project(principal):
            raise Forbidden("You can only search context in your project")
        query_text = f"%{query.strip()}%" if query.strip() else "%"
        visibility_clause = "1 = 1" if principal.admin else "(visibility = 'project' OR source_agent = ?)"
        sql = f"""SELECT * FROM contexts
                 WHERE project = ? AND status = 'active'
                   AND {visibility_clause}
                   AND (title LIKE ? OR content LIKE ?)"""
        params: list[Any] = [project]
        if not principal.admin:
            params.append(principal.agent_id)
        params.extend([query_text, query_text])
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(clamp_limit(limit, default=20, maximum=100))
        with self.db.read() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [row_to_context(row) for row in rows]

    def get_context(self, principal: Principal, context_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = connection.execute("SELECT * FROM contexts WHERE id = ?", (context_id,)).fetchone()
        if row is None:
            raise NotFound("Context not found")
        if not principal.admin:
            if row["visibility"] == "private" and row["source_agent"] != principal.agent_id:
                raise Forbidden("Context is private")
            if row["project"] != self._principal_project(principal):
                raise Forbidden("Context is outside your project")
        return row_to_context(row)

    def publish_artifact(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        if principal.admin:
            raise Forbidden("Register an agent and use its token to publish artifacts")
        project, name, content = payload.get("project"), payload.get("name"), payload.get("content")
        if not all(isinstance(value, str) and value.strip() for value in (project, name, content)):
            raise AgentMeshError("project, name, and content are required")
        if not principal.admin and project != self._principal_project(principal):
            raise Forbidden("You can only publish artifacts in your project")
        if len(content) > MAX_TEXT:
            raise AgentMeshError("Artifact is too large")
        artifact_id = new_id("artifact")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO artifacts
                   (id, project, name, media_type, content, metadata_json, source_agent, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id,
                    project,
                    name.strip(),
                    payload.get("media_type", "text/plain"),
                    content,
                    json_dumps(payload.get("metadata") or {}),
                    principal.agent_id,
                    timestamp,
                ),
            )
        return {
            "id": artifact_id,
            "project": project,
            "name": name.strip(),
            "media_type": payload.get("media_type", "text/plain"),
            "metadata": payload.get("metadata") or {},
            "source_agent": principal.agent_id,
            "created_at": timestamp,
        }

    def get_artifact(self, principal: Principal, artifact_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise NotFound("Artifact not found")
        if not principal.admin and row["project"] != self._principal_project(principal):
            raise Forbidden("Artifact is outside your project")
        return {
            "id": row["id"],
            "project": row["project"],
            "name": row["name"],
            "media_type": row["media_type"],
            "content": row["content"],
            "metadata": json_loads(row["metadata_json"], {}),
            "source_agent": row["source_agent"],
            "created_at": row["created_at"],
        }

    # ---------- internal helpers ----------

    def _get_agent(self, agent_id: str) -> sqlite3.Row:
        with self.db.read() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise NotFound("Agent not found")
        return row

    def _principal_project(self, principal: Principal) -> str:
        if principal.admin:
            return ""
        with self.db.read() as connection:
            row = connection.execute("SELECT project FROM agents WHERE id = ?", (principal.agent_id,)).fetchone()
        if row is None:
            raise Unauthorized("Agent is no longer registered")
        return row["project"]

    def _assert_agent_in_project(self, agent_id: str, project: str) -> None:
        row = self._get_agent(agent_id)
        if row["project"] != project:
            raise Forbidden("Agent is outside this project")

    def _assert_task_access(self, principal: Principal, row: sqlite3.Row) -> None:
        if principal.admin:
            return
        if self._principal_project(principal) != row["project"]:
            raise Forbidden("Task is outside your project")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.admin:
            raise Forbidden("Admin token required")

    @staticmethod
    def _emit(
        connection: sqlite3.Connection,
        *,
        recipient_agent: str,
        event_type: str,
        entity_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO events
               (id, event_type, entity_id, recipient_agent, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id("evt"), event_type, entity_id, recipient_agent, json_dumps(payload), now_iso()),
        )
