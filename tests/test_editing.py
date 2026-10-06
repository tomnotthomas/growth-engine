"""Editing a project from the app: schedule, website text, legal notice, connections, new projects,
the guided first run, and the rule that every server listens on loopback only."""

from __future__ import annotations

import os
import socket

from growth import guard, secrets
from growth.control import ops
from growth.control.plain import schedule_words
from growth.control.server import ControlServer
from growth.control.snapshot import snapshot
from growth.site.spec import load_pages, page_text

from .helpers import HomeTestCase


class Editing(HomeTestCase):
    def home_(self) -> ops.Home:
        return ops.Home(self.home)

    def project(self, pid: str = "example"):
        return self.engine().projects[pid]

    def test_a_schedule_change_is_validated_and_applied(self) -> None:
        ops.set_schedule(self.home_(), "captain", "example", "fetch-data", "every 3h")
        self.assertEqual(self.project().jobs["fetch-data"].schedule.text, "every 3h")
        with self.assertRaises(ops.ControlError):
            ops.set_schedule(self.home_(), "captain", "example", "fetch-data", "whenever")
        self.assertEqual(guard.read_audit(self.home / "state", 1)[0]["event"], "control.schedule")

    def test_website_text_edits_reach_the_built_pages(self) -> None:
        ops.set_page_text(self.home_(), "captain", "example", "landing", "en",
                          {"description": "Edited from the app.", "headline": "Edited\nheading", "sub": "A new subline."})
        pages = load_pages(self.project())
        text = page_text(pages["landing"].langs["en"])
        self.assertEqual((text["description"], text["headline"], text["sub"]), ("Edited from the app.", "Edited\nheading", "A new subline."))
        self.assertNotEqual(page_text(pages["landing"].langs["de"])["description"], "Edited from the app.")
        with self.assertRaises(ops.ControlError):
            ops.set_page_text(self.home_(), "captain", "example", "landing", "en", {"sections": "x"})

    def test_a_text_edit_that_breaks_a_page_rule_is_refused(self) -> None:
        with self.assertRaises(ops.ControlError):  # the brand must never be written out literally
            ops.set_page_text(self.home_(), "captain", "example", "landing", "en", {"title": "Kiln is great"})

    def test_legal_notice(self) -> None:
        ops.set_legal(self.home_(), "captain", "example", {"name": "Test GmbH", "country": "Germany"})
        self.assertEqual(self.project().raw["legal"]["name"], "Test GmbH")
        with self.assertRaises(ops.ControlError):
            ops.set_legal(self.home_(), "captain", "example", {"phone": "1"})

    def test_secrets_are_stored_encrypted_and_never_logged(self) -> None:
        ops.set_secret(self.home_(), "captain", "example", "CLOUDFLARE_ACCOUNT_ID", "acct-123")
        secrets.forget_cache()
        self.assertEqual(secrets.get("CLOUDFLARE_ACCOUNT_ID"), "acct-123")
        self.assertNotIn("acct-123", (self.home / "state" / "engine" / "audit.jsonl").read_text())
        with self.assertRaises(ops.ControlError):
            ops.set_secret(self.home_(), "captain", "example", "AWS_SECRET", "x")
        data = snapshot(self.engine())
        self.assertIn({"name": "CLOUDFLARE_ACCOUNT_ID", "set": True}, data["projects"][0]["connections"])
        self.assertNotIn("acct-123", str(data))

    def test_adding_a_project_starts_quiet(self) -> None:
        ops.add_project(self.home_(), "captain", "my-shop", "My Shop", "https://my-shop.com")
        project = self.project("my-shop")
        self.assertEqual((project.name, project.site["base_url"], project.brand["name"]), ("My Shop", "https://my-shop.com", "My Shop"))
        self.assertFalse(project.channel_enabled("directory-submit"))
        self.assertFalse(any(j.enabled for j in project.jobs.values() if j.kind.startswith("directories-") or j.kind == "deploy"))
        self.assertFalse(project.launched)
        with self.assertRaises(ops.ControlError):
            ops.add_project(self.home_(), "captain", "my-shop", "Again", "https://x.com")
        with self.assertRaises(ops.ControlError):
            ops.add_project(self.home_(), "captain", "Bad Id", "x", "https://x.com")

    def test_the_guided_first_run_moves_step_by_step(self) -> None:
        def next_step():
            return snapshot(self.engine())["projects"][0]["next_step"]["id"]

        self.assertEqual(next_step(), "basics")
        ops.set_settings(self.home_(), "captain", "example", {"base_url": "https://kiln.com"})
        self.assertEqual(next_step(), "goal")
        ops.set_goal(self.home_(), "captain", "example", {"start": "2026-11-01"})
        self.assertEqual(next_step(), "build")
        pid = ops.build_now(self.home_(), "captain", "example")
        self.assertEqual(os.waitpid(pid, 0)[1], 0, "growth build failed")
        self.assertEqual(next_step(), "cloudflare")

    def test_plain_schedule_words(self) -> None:
        self.assertEqual(schedule_words("every 6h"), "Every 6 hours")
        self.assertEqual(schedule_words("every 1d"), "Every day")
        self.assertEqual(schedule_words("daily 04:00"), "Every day at 04:00")
        self.assertEqual(schedule_words("weekly sun 03:00"), "Every Sunday at 03:00")


class LoopbackOnly(HomeTestCase):
    def test_the_control_api_refuses_any_address_but_loopback(self) -> None:
        for host in ("0.0.0.0", "", "192.168.1.10", "::"):
            with self.assertRaises(ValueError):
                ControlServer(0, ops.Home(self.home), "t", host=host)

    def test_it_is_not_reachable_on_other_interfaces(self) -> None:
        server = ControlServer(0, ops.Home(self.home), "t")
        self.addCleanup(server.server_close)
        host, port = server.server_address[:2]
        self.assertEqual(host, "127.0.0.1")
        others = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)} - {"127.0.0.1"}
        for address in others:
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertNotEqual(probe.connect_ex((address, port)), 0, f"reachable on {address}")

    def test_the_demo_server_is_loopback_too(self) -> None:
        server = ControlServer(0, ops.Home(self.home), "demo", demo=True)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
