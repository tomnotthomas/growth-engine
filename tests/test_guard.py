"""The guards: encrypted secrets, the kill switch, the hash-chained audit log, rate limits, the
outbound allow-list, and settings changed from the control app."""

from __future__ import annotations

import json
import os
import stat
from datetime import timedelta
from unittest import mock

from growth import guard, net, secrets
from growth.config import ConfigError
from growth.control import ops
from growth.policy import Action, PolicyViolation
from growth.runner import JobContext, run_now, tick
from growth.budget import Budget

from .helpers import NOW, HomeTestCase


class Secrets(HomeTestCase):
    def test_values_are_encrypted_at_rest_and_read_back(self) -> None:
        secrets.put("CLOUDFLARE_API_TOKEN", "cf-very-secret-value")
        store = self.tmp / "config" / "secrets.enc"
        self.assertNotIn(b"cf-very-secret-value", store.read_bytes())
        self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.tmp / "config" / "master.key").stat().st_mode), 0o600)
        secrets.forget_cache()
        self.assertEqual(secrets.get("CLOUDFLARE_API_TOKEN"), "cf-very-secret-value")
        self.assertEqual(secrets.names(), ["CLOUDFLARE_API_TOKEN"])

    def test_a_changed_store_fails_its_integrity_check(self) -> None:
        secrets.put("A_TOKEN", "x")
        path = self.tmp / "config" / "secrets.enc"
        box = json.loads(path.read_text())
        box["data"] = box["data"][:-4] + ("AAAA" if not box["data"].endswith("AAAA") else "BBBB")
        path.write_text(json.dumps(box))
        with self.assertRaises(secrets.SecretsError):
            secrets.load()

    def test_a_readable_key_file_is_refused(self) -> None:
        secrets.put("A_TOKEN", "x")
        os.chmod(self.tmp / "config" / "master.key", 0o644)
        with self.assertRaises(secrets.SecretsError):
            secrets.load()

    def test_environment_is_the_fallback(self) -> None:
        with mock.patch.dict(os.environ, {"KILN_STATS_TOKEN": "from-env"}):
            secrets.forget_cache()
            self.assertEqual(secrets.get("KILN_STATS_TOKEN"), "from-env")

    def test_names_are_checked(self) -> None:
        with self.assertRaises(secrets.SecretsError):
            secrets.put("not a name", "x")


class KillSwitch(HomeTestCase):
    def test_a_halted_engine_runs_nothing_and_misses_nothing(self) -> None:
        engine = self.engine()
        guard.set_kill(engine.state_dir, True, reason="test", actor="cli")
        report = tick(engine, now=NOW)
        self.assertTrue(report.halted)
        self.assertEqual(report.ran, [])
        store = self.store(engine)
        self.assertEqual(store.runs_since(NOW - timedelta(days=30)), [])
        with self.assertRaises(guard.Halted):
            run_now(engine, "example", "fetch-data", now=NOW)
        guard.set_kill(engine.state_dir, False, reason="", actor="cli")
        self.assertIn("example/fetch-data", " ".join(tick(engine, now=NOW).ran))

    def test_outward_actions_check_the_switch(self) -> None:
        engine = self.engine()
        ctx = self._ctx(engine)
        guard.set_kill(engine.state_dir, True, reason="stop", actor="captain")
        with self.assertRaises(guard.Halted):
            ctx.act(Action(channel="website", kind="publish", url="https://kiln.example"), "k1", lambda: "done")

    def _ctx(self, engine):
        store = self.store(engine)
        project = engine.projects["example"]
        return JobContext(engine=engine, project=project, job=project.jobs["build-site"], store=store,
                          budget=Budget(engine.ai, store, engine.tz), slot=NOW, now=NOW)


class AuditLog(HomeTestCase):
    def test_the_chain_detects_edits_and_removals(self) -> None:
        state = self.home / "state"
        for i in range(4):
            guard.audit(state, "engine", "action.done", scope="example", n=i)
        self.assertEqual(guard.verify_audit(state)[:2], (True, 4))
        path = state / "engine" / "audit.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
        self.assertFalse(guard.verify_audit(state)[0])
        edited = json.loads(lines[1])
        edited["detail"]["n"] = 99
        path.write_text("\n".join([lines[0], json.dumps(edited), *lines[2:]]) + "\n")
        intact, _, problem = guard.verify_audit(state)
        self.assertFalse(intact)
        self.assertIn("changed", problem)

    def test_outward_actions_and_blocks_are_audited(self) -> None:
        engine = self.engine()
        ctx = KillSwitch._ctx(self, engine)
        ctx.act(Action(channel="website", kind="publish", url="https://kiln.example"), "deploy:1", lambda: "ok")
        with self.assertRaises(PolicyViolation):
            ctx.act(Action(channel="reddit", kind="post", community="r/x", text="hi"), "reddit:1", lambda: "posted")
        events = [e["event"] for e in guard.read_audit(engine.state_dir)]
        self.assertIn("action.done", events)
        self.assertIn("blocked", events)


class RateLimits(HomeTestCase):
    def test_a_channel_stops_at_its_limit(self) -> None:
        self.edit("engine.toml", 'website = "40/24h"', 'website = "2/24h"')
        engine = self.engine()
        ctx = KillSwitch._ctx(self, engine)
        action = Action(channel="website", kind="publish", url="https://kiln.example")
        ctx.act(action, "a", lambda: "ok")
        ctx.act(action, "b", lambda: "ok")
        with self.assertRaises(PolicyViolation) as caught:
            ctx.act(action, "c", lambda: "ok")
        self.assertEqual(caught.exception.rule, "rate-limit")

    def test_bad_limits_are_config_errors(self) -> None:
        self.edit("engine.toml", 'website = "40/24h"', 'website = "lots"')
        with self.assertRaises(ConfigError):
            self.engine()


class OutboundAllowList(HomeTestCase):
    def test_hosts_outside_the_list_are_refused_before_sending(self) -> None:
        self.engine()
        with mock.patch("urllib.request.OpenerDirector.open") as opened:
            with self.assertRaises(PolicyViolation) as caught:
                net.request("GET", "https://evil.example/collect")
            opened.assert_not_called()
        self.assertEqual(caught.exception.rule, "outbound-allow-list")

    def test_configured_and_derived_hosts_are_allowed(self) -> None:
        self.engine()
        for url in ("https://ntfy.sh/topic", "https://kiln.example/api/waitlist/stats", "https://api.indexnow.org/indexnow",
                    "https://betahunt.example/api/submit", "https://api.github.com/repos/x/y"):
            guard.check_outbound(url)


class ControlLayer(HomeTestCase):
    def home_(self) -> ops.Home:
        return ops.Home(self.home)

    def test_changes_are_validated_applied_and_audited(self) -> None:
        home = self.home_()
        ops.set_goal(home, "captain", "example", {"target": 8000, "zone": ["de", "at"]})
        engine = self.engine()
        self.assertEqual(engine.projects["example"].raw["goal"]["target"], 8000)
        self.assertEqual(engine.projects["example"].raw["goal"]["zone"], ["DE", "AT"])
        last = guard.read_audit(engine.state_dir, 1)[0]
        self.assertEqual((last["actor"], last["event"]), ("captain", "control.goal"))

    def test_an_invalid_change_is_refused_and_rolled_back(self) -> None:
        home = self.home_()
        with self.assertRaises(ops.ControlError) as caught:
            ops.set_settings(home, "captain", "example", {"launched": True})
        self.assertIn("domain_decided", str(caught.exception))
        self.assertFalse(self.engine().projects["example"].launched)
        with self.assertRaises(ops.ControlError):
            ops.set_settings(home, "captain", "example", {"keywords": [{"page": "landing", "primary": "cheap pizza delivery"}]})

    def test_the_launch_switch_needs_the_decided_domain(self) -> None:
        home = self.home_()
        ops.set_settings(home, "captain", "example", {"domain_decided": True})
        ops.set_settings(home, "captain", "example", {"launched": True})
        self.assertTrue(self.engine().projects["example"].launched)
        self.assertEqual(guard.read_audit(self.home / "state", 1)[0]["event"], "control.launch")

    def test_pausing_a_project_or_a_job_stops_its_runs(self) -> None:
        home = self.home_()
        ops.set_paused(home, "captain", "example", True)
        report = tick(self.engine(), now=NOW)
        self.assertFalse([r for r in report.ran if r.startswith("example/")])
        ops.set_paused(home, "captain", "example", False)
        ops.set_job(home, "captain", "example", "fetch-data", False)
        report = tick(self.engine(), now=NOW + timedelta(hours=7))
        self.assertFalse([r for r in report.ran if "fetch-data" in r])

    def test_a_paused_channel_refuses_outward_actions(self) -> None:
        ops.set_channel(self.home_(), "captain", "example", "website", paused=True)
        engine = self.engine()
        with self.assertRaises(PolicyViolation) as caught:
            KillSwitch._ctx(self, engine).act(Action(channel="website", kind="publish", url="https://kiln.example"), "x", lambda: "ok")
        self.assertEqual(caught.exception.rule, "channel-paused")

    def test_drafts_are_only_marked_never_posted(self) -> None:
        engine = self.engine()
        folder = engine.state_dir / "projects" / "example" / "queue" / "reddit"
        folder.mkdir(parents=True)
        (folder / "abc123.json").write_text(json.dumps({"id": "abc123", "channel": "reddit", "community": "r/x", "thread_url": "https://reddit.com/r/x",
                                                       "title": "t", "text": "x", "why": "y", "created_at": "2026-10-05T00:00:00Z", "status": "open"}))
        with mock.patch("growth.net.request") as request:
            ops.decide_draft(self.home_(), "captain", "example", "abc123", "approved")
            request.assert_not_called()
        self.assertEqual(json.loads((folder / "abc123.json").read_text())["status"], "approved")

    def test_the_kill_switch_works_when_the_config_is_broken(self) -> None:
        self.edit("engine.toml", 'timezone = "Europe/Berlin"', 'timezone = "Nowhere/Never"')
        ops.set_kill(self.home_(), "captain", True, "config broken")
        self.assertTrue(guard.kill_state(self.home / "state"))
