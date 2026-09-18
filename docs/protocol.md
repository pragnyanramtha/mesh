# AgentMesh protocol

The hub is a small JSON API. Every route except `/healthz` requires
`Authorization: Bearer <token>`.

## Identity

`POST /v1/agents/register` requires the admin token and returns a random
per-agent token once. Subsequent calls derive the sender from that token. The
client must not send a claimed sender identity as authority.

## Task lifecycle

```text
pending -> accepted -> running -> completed
                         |       -> failed
                         -> blocked
pending/accepted/blocked -> cancelled
```

`POST /v1/tasks` accepts an idempotency key. Repeating the same request from the
same agent returns the original task. `POST /v1/tasks/{id}/claim` creates an
attempt and a time-limited lease. `POST /v1/tasks/{id}/update` records the
worker's claim. The result is not proof of correctness; a separate validator
can publish tests or review evidence as an artifact.

## Event delivery

Task creation, task updates, task completion, task claims, and messages create
targeted events. `GET /v1/events?after=<seq>` returns only events for the
authenticated agent. `events.read` and `events.ack` are separate so a crash
between receipt and processing can be recovered.

## Context and artifacts

Context is an approved record, not an automatic transcript dump. Search results
respect project membership and private visibility before titles or snippets are
returned. Artifacts are immutable text records in the MVP. Store a patch or
test log there and put its ID in a task result.

## MCP mapping

| MCP tool | HTTP route |
| --- | --- |
| `agents.find` | `GET /v1/agents` |
| `tasks.create` | `POST /v1/tasks` |
| `tasks.get` | `GET /v1/tasks/{id}` |
| `tasks.claim` | `POST /v1/tasks/{id}/claim` or `/v1/tasks/claim-next` |
| `tasks.update` | `POST /v1/tasks/{id}/update` |
| `messages.send` | `POST /v1/messages` |
| `events.read` | `GET /v1/events` |
| `events.ack` | `POST /v1/events/{id}/ack` |
| `context.search` | `GET /v1/contexts/search` |
| `context.read` | `GET /v1/contexts/{id}` |
| `context.publish` | `POST /v1/contexts` |
| `artifacts.publish` | `POST /v1/artifacts` |
| `artifacts.get` | `GET /v1/artifacts/{id}` |

