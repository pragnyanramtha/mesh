from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT NOT NULL,
  runtime TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'worker',
  project TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online',
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  token_hash TEXT NOT NULL UNIQUE,
  last_seen TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  requester_agent TEXT NOT NULL,
  assignee_agent TEXT,
  status TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT,
  constraints_json TEXT NOT NULL DEFAULT '{}',
  base_revision TEXT,
  idempotency_key TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_agent TEXT,
  lease_expires_at REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(requester_agent, idempotency_key),
  FOREIGN KEY(requester_agent) REFERENCES agents(id),
  FOREIGN KEY(assignee_agent) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS tasks_project_status_idx ON tasks(project, status, created_at);
CREATE INDEX IF NOT EXISTS tasks_assignee_status_idx ON tasks(assignee_agent, status, created_at);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  sender_agent TEXT NOT NULL,
  recipient_agent TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'message',
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(sender_agent) REFERENCES agents(id),
  FOREIGN KEY(recipient_agent) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS messages_recipient_idx ON messages(recipient_agent, created_at);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  entity_id TEXT,
  recipient_agent TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  delivered_at TEXT,
  acknowledged_at TEXT,
  FOREIGN KEY(recipient_agent) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS events_recipient_seq_idx ON events(recipient_agent, seq);

CREATE TABLE IF NOT EXISTS contexts (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'project',
  source_agent TEXT NOT NULL,
  source_revision TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(source_agent) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS contexts_project_status_idx ON contexts(project, status, updated_at);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  name TEXT NOT NULL,
  media_type TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  source_agent TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_agent) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS artifacts_project_created_idx ON artifacts(project, created_at);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def execute(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return connection.execute(query, params)

