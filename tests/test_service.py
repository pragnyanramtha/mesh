import tempfile
import unittest
from pathlib import Path

from agentmesh.db import Database
from agentmesh.errors import Forbidden
from agentmesh.service import AgentService


class AgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "mesh.db")
        self.service = AgentService(self.db, "admin-secret")
        self.admin = self.service.authenticate("admin-secret")
        first = self.service.register_agent(
            self.admin,
            name="requester",
            owner="pragnyan",
            runtime="codex",
            role="lead",
            project="demo",
            capabilities=["delegate"],
        )
        second = self.service.register_agent(
            self.admin,
            name="reviewer",
            owner="alok",
            runtime="claude",
            role="reviewer",
            project="demo",
            capabilities=["code-review"],
        )
        self.requester = self.service.authenticate(first["token"])
        self.reviewer = self.service.authenticate(second["token"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_idempotency_and_lifecycle(self):
        payload = {
            "project": "demo",
            "title": "Review auth",
            "goal": "Find compatibility issues",
            "assignee_agent": self.reviewer.agent_id,
            "idempotency_key": "auth-review-1",
            "base_revision": "abc123",
        }
        created = self.service.create_task(self.requester, payload)
        repeated = self.service.create_task(self.requester, payload)
        self.assertTrue(created["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(created["task"]["id"], repeated["task"]["id"])

        incoming = self.service.read_events(self.reviewer)
        self.assertEqual(incoming["events"][0]["event_type"], "task.created")
        self.service.acknowledge_event(self.reviewer, incoming["events"][0]["id"])

        task_id = created["task"]["id"]
        running = self.service.claim_task(self.reviewer, task_id)
        self.assertEqual(running["status"], "running")
        finished = self.service.update_task(self.reviewer, task_id, {"status": "completed", "result": {"passed": 12}})
        self.assertEqual(finished["result"], {"passed": 12})
        self.assertEqual(self.service.get_task(self.requester, task_id)["status"], "completed")

    def test_project_and_private_context_permissions(self):
        private = self.service.publish_context(
            self.requester,
            {"project": "demo", "title": "Private note", "content": "secret", "visibility": "private"},
        )
        with self.assertRaises(Forbidden):
            self.service.get_context(self.reviewer, private["id"])
        self.assertEqual(self.service.get_context(self.requester, private["id"])["content"], "secret")

    def test_message_creates_targeted_event(self):
        message = self.service.send_message(self.requester, {"recipient_agent": self.reviewer.agent_id, "body": "Please review the patch", "kind": "follow_up"})
        events = self.service.read_events(self.reviewer)["events"]
        self.assertEqual(message["id"], events[0]["payload"]["message_id"])
        self.assertEqual(events[0]["event_type"], "message.created")

    def test_cross_project_message_is_rejected(self):
        other = self.service.register_agent(
            self.admin,
            name="other",
            owner="someone",
            runtime="opencode",
            project="other",
        )
        with self.assertRaises(Forbidden):
            self.service.send_message(self.requester, {"recipient_agent": other["id"], "body": "hello"})

