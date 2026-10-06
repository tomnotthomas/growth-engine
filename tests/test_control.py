"""The control API the Mac app uses: loopback only, a login token, no browsers, every change audited."""

from __future__ import annotations

import http.client
import json
import shutil
import threading

from growth import guard
from growth.control import ops
from growth.control.demo import make_demo_home
from growth.control.server import ControlServer
from growth.control.snapshot import snapshot

from .helpers import HomeTestCase

TOKEN = "t0ken-for-tests"


class ControlAPI(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = ControlServer(0, ops.Home(self.home), TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body=None, token: str | None = TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        sent = {"Content-Type": "application/json", **(headers or {})}
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=sent)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        conn.close()
        return resp.status, data

    def test_it_listens_on_loopback_only(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_ping_is_open_everything_else_needs_the_token(self) -> None:
        self.assertEqual(self.call("GET", "/api/ping", token=None)[0], 200)
        self.assertEqual(self.call("GET", "/api/dashboard", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/dashboard", token="wrong")[0], 401)
        self.assertEqual(self.call("POST", "/api/kill", {"on": True}, token="wrong")[0], 401)
        self.assertIsNone(guard.kill_state(self.home / "state"))

    def test_browsers_and_foreign_hosts_are_refused(self) -> None:
        self.assertEqual(self.call("GET", "/api/dashboard", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/dashboard", headers={"Host": "evil.example"})[0], 421)

    def test_dashboard_and_controls(self) -> None:
        status, data = self.call("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(data["projects"][0]["id"], "example")
        self.assertIn("goal", data["projects"][0])
        status, data = self.call("POST", "/api/projects/example/goal", {"target": 7000})
        self.assertEqual(status, 200, data)
        status, data = self.call("POST", "/api/projects/example/settings", {"launched": True})
        self.assertEqual(status, 409)
        self.assertIn("domain_decided", data["error"])
        status, data = self.call("POST", "/api/kill", {"on": True, "reason": "test"})
        self.assertEqual((status, data["kill"]["by"]), (200, "captain"))
        self.assertEqual(self.call("POST", "/api/run", {"scope": "example", "job": "fetch-data"})[0], 409)
        status, data = self.call("GET", "/api/audit?limit=10")
        self.assertTrue(data["intact"])
        self.assertEqual([e["event"] for e in data["items"]][:2], ["kill-switch.on", "control.goal"])

    def test_bad_input_is_refused(self) -> None:
        self.assertEqual(self.call("POST", "/api/projects/example/goal", {"target": "lots"})[0], 409)
        self.assertEqual(self.call("POST", "/api/projects/nope/pause", {"paused": True})[0], 409)
        self.assertEqual(self.call("POST", "/api/projects/example/settings", {"base_url": "http://plain.example"})[0], 409)
        self.assertEqual(self.call("POST", "/api/projects/example/settings", {"theme": "x"})[0], 409)


class Demo(HomeTestCase):
    def test_the_demo_home_has_a_goal_a_queue_and_activity(self) -> None:
        from growth.config import load_engine

        home = make_demo_home()
        self.addCleanup(shutil.rmtree, home.parent, True)
        data = snapshot(load_engine(home), demo=True)
        project = data["projects"][0]
        self.assertTrue(data["demo"])
        self.assertGreater(project["goal"]["confirmed_total"], 0)
        self.assertEqual(len(project["goal"]["daily"]), 30)
        self.assertTrue(any(c["in_zone"] is False for c in project["goal"]["countries"]))
        self.assertEqual(len(project["drafts"]), 2)
        self.assertTrue(data["activity"])
        self.assertTrue(data["upcoming"])
