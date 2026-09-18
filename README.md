# AgentMesh

AgentMesh is a small coordination plane for already-running coding agents.
It gives Claude Code, Codex, OpenCode, Gemini CLI, and custom workers one
durable interface for tasks, messages, events, approved context, and artifacts.

It does not try to replace an agent runtime. The hub stores state and routes
work. A local adapter or managed worker decides how a particular CLI starts a
turn. That keeps the protocol vendor-neutral and makes the same interface work
on one laptop or across machines.

## What is included

- A SQLite-backed HTTP hub with bearer-token authentication.
- Durable task state with idempotency keys, leases, attempts, and result state.
- Targeted event delivery with a cursor, delivery timestamp, and acknowledgement.
- Project-scoped agent discovery and colleague-safe permissions.
- Searchable, explicitly published project context. Raw chats stay private by default.
- Immutable text artifacts for patches, logs, and evidence.
- A dependency-free stdio MCP server with tools such as `tasks.create`,
  `tasks.claim`, `events.read`, `context.search`, and `artifacts.publish`.
- A generic managed worker adapter for any CLI that reads a task JSON object and
  writes a result to stdout.

The first release intentionally supports pull-based delivery. A host that has a
native event or channel API can add a small adapter later. MCP alone cannot
force an arbitrary host to start a model turn when an event arrives.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

# Terminal 1: the hub. Save the printed admin token.
agentmesh start --db .agentmesh/mesh.db --admin-token 'replace-with-a-long-random-token'

# Terminal 2: register a Codex worker.
agentmesh register \
  --hub http://127.0.0.1:8765 \
  --admin-token 'replace-with-a-long-random-token' \
  --name codex-reviewer --owner pragnyan --runtime codex \
  --role reviewer --project demo --capability code-review
```

The register command prints a per-agent token. Keep it in the agent's local
environment, not in a repository:

```bash
export AGENTMESH_HUB=http://127.0.0.1:8765
export AGENTMESH_TOKEN='the-token-printed-by-register'
```

Create work from a shell or an MCP-connected agent:

```bash
agentmesh task create --project demo --title 'Review auth change' \
  --goal 'Check API compatibility and return findings with file locations.'
```

## Connect MCP-capable clients

Configure the client to start this stdio server:

```json
{
  "mcpServers": {
    "agentmesh": {
      "command": "agentmesh-mcp",
      "env": {
        "AGENTMESH_HUB": "http://127.0.0.1:8765",
        "AGENTMESH_TOKEN": "agent-token"
      }
    }
  }
}
```

The server exposes structured tools. A task handoff should carry a repository
ID, base revision, constraints, completion criteria, and artifact references.
Send a short summary through the task result instead of copying a complete
transcript into the next model context.

## Remote machines

Run the hub on an always-on Linux VM and bind it to a private network:

```bash
agentmesh start --host 0.0.0.0 --port 8765 --db /var/lib/agentmesh/mesh.db \
  --admin-token "$AGENTMESH_ADMIN_TOKEN"
```

Agents make outbound HTTPS requests to the hub. Put TLS and access control in
front of the HTTP listener with a reverse proxy, Tailscale, or a private
network. The hub does not need provider API keys. Keep model credentials on
each local runner. Do not expose an unauthenticated runtime control socket.

For unattended work, run a worker on a VM or container with a separate checkout
and restricted credentials. A hosted hub can retain a task while a laptop sleeps;
it cannot make that sleeping laptop execute the task.

## Managed worker adapter

The generic worker claims the next task and passes this JSON object to a command
on stdin:

```bash
agentmesh worker --token "$AGENTMESH_TOKEN" --project demo \
  --command 'python examples/echo_worker.py'
```

The command's exit code becomes the task status. This is a lifecycle adapter,
not a claim that every vendor CLI has the same session API. For a real Codex or
Claude integration, wrap that runtime's supported session interface and keep
the AgentMesh task ID attached to the exact session ID.

## Trust model

- The admin token can register agents and inspect project metadata.
- Each registered agent receives its own token. The hub derives sender identity
  from that token; an `agent_id` in a message body is not trusted.
- Agents can communicate only inside their registered project.
- Private context is visible only to its publisher. Project context must be
  explicitly published before it becomes searchable.
- Human approval is not represented by a peer message. Keep approvals on a
  separate authenticated path.
- A worktree separates files, but it is not a security sandbox. Isolate test
  databases, ports, credentials, and external side effects as well.

## Delivery and failure semantics

The hub separates three states: an event was stored, an adapter received it,
and an agent processed it. `events.read` marks delivery. `events.ack` records
processing. Cursors allow reconnects without losing events. Task leases and
idempotency keys prevent most duplicate work, but no queue can promise exactly
once execution for an external side effect. Reconcile after timeouts before
retrying.

## Current scope

This is a focused MVP, not a production SaaS service. It uses SQLite, bearer
tokens, and a polling MCP client. It does not yet provide built-in TLS,
end-to-end encrypted transcript search, native injection into every coding
agent, binary artifact streaming, or a full A2A gateway. Those belong after the
task and permission semantics are measured against a single-agent baseline.

See [docs/protocol.md](docs/protocol.md) for the HTTP and MCP contract.

