"""The deploy job: a local check first, nothing uploaded before the launch go, smoke checks, rollback."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from unittest import mock

from growth import secrets
from growth.runner import run_now
from growth.util import read_json

from .helpers import NOW, HomeTestCase

FAKE_WRANGLER = textwrap.dedent(
    """
    import http.server, json, os, sys, uuid
    args = sys.argv[1:]
    state_path = os.environ["FAKE_WRANGLER_STATE"]
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"dbs": [], "n": 0}
    with open(os.environ["FAKE_WRANGLER_LOG"], "a") as log:
        log.write(json.dumps({"args": args, "token": os.environ.get("CLOUDFLARE_API_TOKEN")}) + "\\n")
    if args[:1] == ["dev"]:
        class Ok(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            def log_message(self, *a):
                pass
        http.server.HTTPServer((args[args.index("--ip") + 1], int(args[args.index("--port") + 1])), Ok).serve_forever()
    if args[:2] == ["d1", "list"]:
        print(json.dumps(state["dbs"]))
    elif args[:2] == ["d1", "create"]:
        state["dbs"].append({"name": args[2], "uuid": "11111111-2222-3333-4444-555555555555"})
    elif args[:2] == ["versions", "upload"]:
        state["n"] += 1
        print(f"Worker Version ID: 00000000-0000-0000-0000-{state['n']:012d}")
    elif args[:2] == ["secret", "list"]:
        print(json.dumps([{"name": n} for n in os.environ.get("FAKE_SECRETS", "").split(",") if n]))
    json.dump(state, open(state_path, "w"))
    """
)
LOCAL = "http://127.0.0.1:"
REMOTE = (["versions"], ["d1", "create"], ["d1", "list"], ["secret"])


class Deploy(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        fake = self.tmp / "fake_wrangler.py"
        fake.write_text(FAKE_WRANGLER)
        self.log = self.tmp / "wrangler.log"
        env = mock.patch.dict(os.environ, {"FAKE_WRANGLER_LOG": str(self.log), "FAKE_WRANGLER_STATE": str(self.tmp / "wrangler.json")})
        env.start()
        self.addCleanup(env.stop)
        self.only_core_jobs()
        self.edit("projects/example/project.toml", "enabled = false                           # switch on", "# switch on")
        self.append("engine.toml", f"[deploy]\nwrangler = {json.dumps([sys.executable, str(fake)])}\n")
        secrets.put("CLOUDFLARE_API_TOKEN", "cf-token")
        secrets.put("CLOUDFLARE_ACCOUNT_ID", "acct")
        self.smoked: list[str] = []
        self.failing: set[str] = set()

    def smoke(self, base, paths, waitlist):
        self.smoked.append(base)
        return ["/: answered 500"] if any(f in base for f in self.failing) else []

    def deploy(self, now=NOW) -> str:
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "fetch-data", now=now), "ok")
        with mock.patch("growth.jobs.deploy.smoke", side_effect=self.smoke):
            return run_now(engine, "example", "deploy", now=now)

    def calls(self) -> list[list[str]]:
        return [json.loads(line)["args"] for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def state(self) -> dict:
        return read_json(self.home / "state" / "projects" / "example" / "deploy.json", {})

    def remote_calls(self) -> list[list[str]]:
        return [c for c in self.calls() if "--remote" in c or any(c[: len(r)] == r for r in REMOTE)]

    def decide(self) -> None:
        self.edit("projects/example/project.toml", "domain_decided = false", "domain_decided = true")
        self.edit("projects/example/project.toml", 'email_provider = "log"', 'email_provider = "brevo"')
        os.environ["FAKE_SECRETS"] = "EMAIL_API_KEY,STATS_TOKEN,HASH_SALT"
        self.addCleanup(os.environ.pop, "FAKE_SECRETS", None)

    def launch(self) -> None:
        self.decide()
        self.edit("projects/example/project.toml", "launched = false", "launched = true")

    def test_before_launch_nothing_leaves_the_machine(self) -> None:
        self.assertEqual(self.deploy(), "ok")
        self.assertEqual(self.remote_calls(), [], "nothing may reach Cloudflare before the launch go")
        dev = [c for c in self.calls() if c[:1] == ["dev"]]
        self.assertEqual(len(dev), 1)
        self.assertEqual(dev[0][dev[0].index("--ip") + 1], "127.0.0.1")
        self.assertIn(["d1", "execute", "kiln-waitlist", "--local"], [c[:4] for c in self.calls()])
        self.assertTrue(all(json.loads(line)["token"] is None for line in self.log.read_text().splitlines()), "no credentials for local runs")
        self.assertTrue(self.smoked and all(base.startswith(LOCAL) for base in self.smoked))
        self.assertEqual(self.state()["check"]["status"], "checked")
        self.assertNotIn("production", self.state())
        self.assertEqual(self.deploy(NOW.replace(minute=30)), "ok")
        self.assertEqual(len([c for c in self.calls() if c[:1] == ["dev"]]), 1, "an unchanged build is not checked again")

    def test_the_generated_worker_has_no_preview_urls(self) -> None:
        import tomllib

        self.assertEqual(self.deploy(), "ok")
        config = tomllib.loads((self.home / "dist" / "example" / "wrangler.toml").read_text())
        self.assertIs(config["preview_urls"], False)
        self.assertIs(config["workers_dev"], False)

    def test_after_launch_without_credentials_nothing_happens(self) -> None:
        self.launch()
        secrets.delete("CLOUDFLARE_API_TOKEN")
        self.assertEqual(self.deploy(), "failed")
        self.assertEqual(self.calls(), [])

    def test_a_failing_local_check_uploads_nothing_and_is_retried(self) -> None:
        self.launch()
        self.failing = {LOCAL}
        self.assertEqual(self.deploy(), "failed")
        self.assertEqual(self.remote_calls(), [])
        self.assertEqual(self.state()["check"]["status"], "failed")
        self.failing = set()
        self.assertEqual(self.deploy(NOW.replace(hour=3)), "ok")
        self.assertEqual(self.state()["production"]["status"], "promoted")

    def test_a_build_checked_before_launch_goes_live_when_only_the_switch_flips(self) -> None:
        self.decide()
        self.assertEqual(self.deploy(), "ok")
        checked = self.state()["check"]["fingerprint"]
        self.assertEqual(self.remote_calls(), [])
        self.edit("projects/example/project.toml", "launched = false", "launched = true")
        self.assertEqual(self.deploy(NOW.replace(hour=3)), "ok")
        production = self.state()["production"]
        self.assertEqual((production["status"], production["fingerprint"]), ("promoted", checked))
        self.assertIn(["versions", "deploy", f"{production['version_id']}@100%"], [c[:3] for c in self.calls()])
        self.assertEqual(len([c for c in self.calls() if c[:1] == ["dev"]]), 1, "the passed local check is reused")

    def test_after_launch_production_is_promoted_and_rolled_back_on_failure(self) -> None:
        self.launch()
        self.assertEqual(self.deploy(), "ok")
        calls = self.calls()
        self.assertIn(["d1", "create", "kiln-waitlist"], calls)
        self.assertEqual(json.loads(self.log.read_text().splitlines()[-1])["token"], "cf-token")
        self.assertEqual(self.state()["d1_database_id"], "11111111-2222-3333-4444-555555555555")
        first = self.state()["last_good"]
        self.assertIn(["versions", "deploy", f"{first}@100%", "--yes", "--message"], [c[:5] for c in calls])
        self.assertEqual(self.state()["production"]["status"], "promoted")
        # A content change whose live check fails goes back to the last good version.
        self.edit("projects/example/project.toml", 'name = "Kiln"\nwordmark', 'name = "Kiln Two"\nwordmark')
        self.failing = {"https://kiln.example"}
        self.assertEqual(self.deploy(NOW.replace(hour=3)), "failed")
        deploys = [c for c in self.calls() if c[:2] == ["versions", "deploy"]]
        self.assertEqual(deploys[-1][2], f"{first}@100%")
        self.assertEqual(self.state()["production"]["status"], "rolled-back")
        self.assertEqual(self.state()["last_good"], first)
        # The rolled-back build is not pushed to production again.
        self.assertEqual(self.deploy(NOW.replace(hour=4)), "ok")
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["versions", "deploy"]]), len(deploys))

    def test_a_failed_first_production_deploy_is_rechecked_until_the_live_site_passes(self) -> None:
        self.launch()
        self.failing = {"https://kiln.example"}
        self.assertEqual(self.deploy(), "failed")
        self.assertEqual(self.state()["production"]["status"], "failed")
        self.assertNotIn("last_good", self.state())
        promotes = len([c for c in self.calls() if c[:2] == ["versions", "deploy"]])
        self.assertEqual(self.deploy(NOW.replace(hour=3)), "failed", "a failing live site is never reported as success")
        self.failing = set()
        self.assertEqual(self.deploy(NOW.replace(hour=4)), "ok")
        production = self.state()["production"]
        self.assertEqual(production["status"], "promoted")
        self.assertEqual(self.state()["last_good"], production["version_id"])
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["versions", "deploy"]]), promotes)

    def test_production_needs_a_real_mail_provider_and_the_worker_secrets(self) -> None:
        self.launch()
        os.environ["FAKE_SECRETS"] = "STATS_TOKEN"
        self.assertEqual(self.deploy(), "failed")
        self.assertFalse(any(c[:1] == ["versions"] for c in self.calls()))
