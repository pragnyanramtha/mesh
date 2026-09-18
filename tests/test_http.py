import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from agentmesh.db import Database
from agentmesh.http import AgentMeshHTTPServer
from agentmesh.service import AgentService


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        service = AgentService(Database(Path(self.tmp.name) / "mesh.db"), "admin-secret")
        self.server = AgentMeshHTTPServer(("127.0.0.1", 0), service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, token=None, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urlopen(Request(self.base + path, data=data, method=method, headers=headers), timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_register_and_me(self):
        status, body = self.request("POST", "/v1/agents/register", "admin-secret", {"name": "worker", "owner": "me", "runtime": "test", "project": "demo"})
        self.assertEqual(status, 201)
        token = body["agent"]["token"]
        status, me = self.request("GET", "/v1/me", token)
        self.assertEqual(status, 200)
        self.assertEqual(me["agent"]["name"], "worker")

    def test_health_is_public(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

