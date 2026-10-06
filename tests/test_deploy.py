"""The deploy job: preview first, production only after the launch go, smoke checks, rollback."""

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
    import json, os, sys, uuid
    args = sys.argv[1:]
    state_path = os.environ["FAKE_WRANGLER_STATE"]
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"dbs": [], "n": 0}
    with open(os.environ["FAKE_WRANGLER_LOG"], "a") as log:
        log.write(json.dumps({"args": args, "token": os.environ.get("CLOUDFLARE_API_TOKEN")}) + "\\n")
    if args[:2] == ["d1", "list"]:
        print(json.dumps(state["dbs"]))
    elif args[:2] == ["d1", "create"]:
        state["dbs"].append({"name": args[2], "uuid": "11111111-2222-3333-4444-555555555555"})
    elif args[:2] == ["versions", "upload"]:
        state["n"] += 1
        vid = f"00000000-0000-0000-0000-{state['n']:012d}"
        print(f"Worker Version ID: {vid}\\nVersion Preview URL: https://{vid[-4:]}-kiln-site.acct.workers.dev")
    elif args[:2] == ["secret", "list"]:
        print(json.dumps([{"name": n} for n in os.environ.get("FAKE_SECRETS", "").split(",") if n]))
    json.dump(state, open(state_path, "w"))
    """
)


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

    def launch(self) -> None:
        self.edit("projects/example/project.toml", "domain_decided = false", "domain_decided = true")
        self.edit("projects/example/project.toml", 'email_provider = "log"', 'email_provider = "brevo"')
        self.edit("projects/example/project.toml", "launched = false", "launched = true")
        os.environ["FAKE_SECRETS"] = "EMAIL_API_KEY,STATS_TOKEN,HASH_SALT"
        self.addCleanup(os.environ.pop, "FAKE_SECRETS", None)

    def test_before_launch_only_a_checked_preview_is_uploaded(self) -> None:
        self.assertEqual(self.deploy(), "ok")
        calls = self.calls()
        self.assertIn(["d1", "create", "kiln-waitlist"], calls)
        self.assertTrue(any(c[:2] == ["versions", "upload"] for c in calls))
        self.assertFalse(any(c[:2] == ["versions", "deploy"] for c in calls), "nothing may reach production before the launch go")
        self.assertEqual(self.state()["preview"]["status"], "previewed")
        self.assertTrue(self.smoked[0].endswith(".workers.dev"))
        self.assertEqual(self.state()["d1_database_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(json.loads(self.log.read_text().splitlines()[0])["token"], "cf-token")
        uploads = sum(1 for c in calls if c[:2] == ["versions", "upload"])
        self.assertEqual(self.deploy(NOW.replace(minute=30)), "ok")
        self.assertEqual(sum(1 for c in self.calls() if c[:2] == ["versions", "upload"]), uploads, "an unchanged build is not uploaded again")

    def test_without_credentials_nothing_happens(self) -> None:
        secrets.delete("CLOUDFLARE_API_TOKEN")
        self.assertEqual(self.deploy(), "failed")
        self.assertEqual(self.calls(), [])

    def test_a_failing_preview_never_reaches_production(self) -> None:
        self.launch()
        self.failing = {"workers.dev"}
        self.assertEqual(self.deploy(), "failed")
        self.assertFalse(any(c[:2] == ["versions", "deploy"] for c in self.calls()))
        self.assertEqual(self.state()["preview"]["status"], "failed")

    def test_after_launch_production_is_promoted_and_rolled_back_on_failure(self) -> None:
        self.launch()
        self.assertEqual(self.deploy(), "ok")
        first = self.state()["last_good"]
        self.assertIn(["versions", "deploy", f"{first}@100%", "--yes", "--message"], [c[:5] for c in self.calls()])
        self.assertEqual(self.state()["production"]["status"], "promoted")
        # A content change whose live check fails goes back to the last good version.
        self.edit("projects/example/project.toml", 'name = "Kiln"\nwordmark', 'name = "Kiln Two"\nwordmark')
        self.failing = {"https://kiln.example"}
        self.assertEqual(self.deploy(NOW.replace(hour=3)), "failed")
        deploys = [c for c in self.calls() if c[:2] == ["versions", "deploy"]]
        self.assertEqual(deploys[-1][2], f"{first}@100%")
        self.assertEqual(self.state()["production"]["status"], "rolled-back")
        self.assertEqual(self.state()["last_good"], first)

    def test_production_needs_a_real_mail_provider_and_the_worker_secrets(self) -> None:
        self.launch()
        os.environ["FAKE_SECRETS"] = "STATS_TOKEN"
        self.assertEqual(self.deploy(), "failed")
        self.assertFalse(any(c[:2] == ["versions", "deploy"] for c in self.calls()))
