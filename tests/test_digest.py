"""The weekly digest, the goal tracker and the headless Claude runner."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from growth.ai import build_command, subscription_env
from growth.config import AIConfig
from growth.goal import goal_report
from growth.jobs.digest import invented_numbers
from growth.runner import tick

from .helpers import HomeTestCase

MONDAY = datetime(2026, 10, 5, 3, 31, tzinfo=timezone.utc)

STATS = {
    "totals": {"confirmed": 1200, "pending": 90},
    "days": [
        {"date": "2026-10-01", "role": "player", "confirmed": 150, "referred": 40},
        {"date": "2026-10-02", "role": "player", "confirmed": 160, "referred": 50},
        {"date": "2026-10-02", "role": "host", "confirmed": 10, "referred": 0},
        {"date": "2026-10-04", "role": "player", "confirmed": 200, "referred": 80},
    ],
    "sources": [
        {"source": "referral", "role": "player", "confirmed": 300},
        {"source": "search", "role": "player", "confirmed": 500},
        {"source": "reddit", "role": "player", "confirmed": 400},
    ],
    "cohorts": [
        {"week": "2026-09-14", "size": 200, "invited": 90},
        {"week": "2026-09-21", "size": 300, "invited": 120},
        {"week": "2026-09-28", "size": 700, "invited": 90},
    ],
}


class GoalTracker(unittest.TestCase):
    def test_progress_against_the_target(self) -> None:
        goal = {"name": "confirmed sign-ups", "target": 20000, "days": 46, "start": "2026-09-14"}
        report = goal_report(STATS, goal, date(2026, 10, 5))
        self.assertEqual(report["confirmed_total"], 1200)
        self.assertEqual(report["last_7_days"], 520)
        self.assertEqual(report["avg_per_day_7"], 74.3)
        self.assertEqual(report["days_elapsed"], 21)
        self.assertEqual(report["days_left"], 25)
        self.assertEqual(report["deadline"], "2026-10-29")
        self.assertEqual(report["needed_per_day"], round(18800 / 25, 1))
        self.assertFalse(report["on_track"])
        self.assertEqual(report["status"], "behind plan")
        self.assertEqual(report["sources"][0], {"source": "search", "confirmed": 500})
        self.assertEqual(len(report["series"]), 14)
        self.assertEqual(report["series"][-1]["date"], "2026-10-04")
        self.assertEqual(report["series"][-1]["cumulative"], 1200)

    def test_k_factor_uses_only_cohorts_old_enough(self) -> None:
        report = goal_report(STATS, {"target": 20000, "days": 46, "start": "2026-09-14"}, date(2026, 10, 5))
        self.assertEqual(report["k_factor"], round((90 + 120) / (200 + 300), 2))
        self.assertEqual(report["k_cohorts"][-1]["k"], round(90 / 700, 2))

    def test_clock_not_started_and_no_data(self) -> None:
        self.assertIn("not started", goal_report(STATS, {"target": 20000, "days": 46}, date(2026, 10, 5))["status"])
        self.assertIn("no data", goal_report(None, {"target": 20000}, date(2026, 10, 5))["status"])


class InventedNumbers(unittest.TestCase):
    def test_numbers_must_come_from_the_facts(self) -> None:
        facts = {"pageviews": 1234, "share": 0.375, "drafts": 4}
        self.assertEqual(invented_numbers("1,234 pageviews and 37.5% from Macs; 4 drafts; three changes", facts), [])
        self.assertEqual(invented_numbers("Traffic grew to 48213 visitors in 2026.", facts), ["48213"])

    def test_scaled_numbers_and_units_are_checked(self) -> None:
        facts = {"signups": 3500, "views": 1200000, "drafts": 4}
        self.assertEqual(invented_numbers("3.5k sign-ups and 1.2 million views; 3,5 Tsd. und 1,2 Mio.", facts), [])
        self.assertEqual(
            invented_numbers("We need 3.5k more, about 1.2 million views", {"drafts": 4}), ["3.5k", "1.2 million"]
        )
        self.assertEqual(invented_numbers("up 7% with 2 Mio. views and 3 drafts in 2026", {"drafts": 4}), ["7%", "2 Mio."])

    def test_negative_facts_match_their_absolute_value(self) -> None:
        self.assertEqual(invented_numbers("We are 5,000 behind plan.", {"goal": {"gap_to_plan": -5000}}), [])


class ClaudeRunner(unittest.TestCase):
    def test_no_paid_key_reaches_claude(self) -> None:
        env = subscription_env({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u", "CLAUDE_CODE_USE_BEDROCK": "1", "HOME": "/h"})
        self.assertEqual(env, {"HOME": "/h"})

    def test_command_is_headless_and_toolless_and_never_bare(self) -> None:
        cfg = AIConfig(["claude"], [], 3, 10, None, None, timedelta(minutes=5), timedelta(hours=5))
        cmd = build_command(cfg, "be brief")
        self.assertEqual(cmd[:5], ["claude", "-p", "--output-format", "json", "--tools"])
        self.assertNotIn("--bare", cmd)
        with self.assertRaises(ValueError):
            build_command(AIConfig(["claude"], ["--bare"], 3, 10, None, None, timedelta(minutes=5), timedelta(hours=5)), None)


class WeeklyDigest(HomeTestCase):
    def test_digest_is_written_by_claude_and_lists_what_ran(self) -> None:
        engine = self.engine()
        tick(engine, now=MONDAY)
        folder = engine.state_dir / "engine" / "digests"
        markdown = (folder / "2026-10-05.md").read_text()
        self.assertIn("Everything ran.", markdown)
        self.assertIn("## Kiln", markdown)
        self.assertIn("Reddit drafts waiting: 0", markdown)
        self.assertIn("waitlist stats need", markdown)  # not deployed yet: said plainly, never guessed
        html = (folder / "latest.html").read_text()
        self.assertIn("<h1>Growth digest</h1>", html)
        self.assertIn("Not connected", html)

    def test_ai_text_with_invented_numbers_is_dropped(self) -> None:
        engine = self.engine()
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "invent"}):
            tick(engine, now=MONDAY)
        markdown = (engine.state_dir / "engine" / "digests" / "2026-10-05.md").read_text()
        self.assertNotIn("Traffic grew", markdown)
        self.assertIn("AI narrative discarded", markdown)

    def test_digest_goes_out_even_when_claude_is_rate_limited(self) -> None:
        engine = self.engine()
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "limit"}):
            tick(engine, now=MONDAY)
        markdown = (engine.state_dir / "engine" / "digests" / "2026-10-05.md").read_text()
        self.assertIn("written without AI", markdown)


class GoalClock(unittest.TestCase):
    GOAL = {"name": "confirmed sign-ups", "target": 20000, "days": 46, "start": "2026-10-15"}

    def test_before_the_start_the_goal_days_are_all_still_ahead(self) -> None:
        report = goal_report({"totals": {"confirmed": 0}, "days": []}, self.GOAL, date(2026, 10, 5))
        self.assertEqual(report["status"], "starts on 2026-10-15")
        self.assertEqual(report["days_left"], 46)
        self.assertEqual(report["needed_per_day"], round(20000 / 46, 1))
        self.assertEqual(report["deadline"], "2026-11-29")
        self.assertNotIn("on_track", report)

    def test_first_week_pace_counts_only_days_since_the_start(self) -> None:
        stats = {"totals": {"confirmed": 900}, "days": [{"date": "2026-10-15", "confirmed": 400}, {"date": "2026-10-16", "confirmed": 500}]}
        report = goal_report(stats, self.GOAL, date(2026, 10, 17))
        self.assertEqual(report["avg_per_day_7"], 450.0)
        self.assertEqual(report["last_7_days"], 900)
        self.assertEqual(report["days_left"], 44)
        self.assertEqual(report["projection"], 900 + 450 * 44)


class TrafficFunnel(unittest.TestCase):
    def test_traffic_and_the_sign_up_funnel_come_from_the_facts(self) -> None:
        from growth.jobs.digest_render import render_html, render_markdown
        from growth.site.html import esc

        funnel = {"waitlist_form_view": 120, "waitlist_submit": 40, "waitlist_confirmed": 31, "referral_sent": 9}
        project = {
            "name": "Kiln",
            "traffic": {"connected": True, "pageviews_7d": 0, "top_pages": [], "funnel_7d": funnel},
            "site": {"pages": 3, "indexable": 0, "last_build": "never", "public": False},
            "reddit_drafts_waiting": 0,
            "jobs": {},
            "problems": [],
            "blocked": [],
        }
        facts = {"week": {"from": "2026-09-28", "to": "2026-10-05"}, "ai": {"runs_7d": 0, "budget_per_week": 10}, "projects": [project]}
        markdown = render_markdown(facts, "", "")
        html = render_html(facts, "", "", esc)
        self.assertIn("waitlist_form_view 120, waitlist_submit 40, waitlist_signup 0, waitlist_confirmed 31", markdown)
        self.assertIn('<td>referral_sent</td><td class="num">9</td>', html)
        self.assertNotIn("None", markdown + html)

class ClaudeBudgetRows(HomeTestCase):
    def _runner(self, **changes):
        import dataclasses

        from growth.ai import ClaudeRunner
        from growth.budget import Budget

        engine = self.engine()
        cfg = dataclasses.replace(engine.ai, window=None, **changes)
        store = self.store(engine)
        return ClaudeRunner(cfg, store, Budget(cfg, store, engine.tz), "engine", "weekly-digest", str(self.tmp)), store

    def test_forbidden_args_use_no_budget(self) -> None:
        runner, store = self._runner(extra_args=["--max-budget-usd=5"])
        with self.assertRaises(ValueError):
            runner.run("hi")
        self.assertEqual(store.ai_runs_since(MONDAY - timedelta(days=1)), 0)

    def test_a_crash_after_starting_still_closes_the_budget_row(self) -> None:
        runner, store = self._runner()
        with mock.patch("growth.ai.subprocess.run", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            runner.run("hi")
        row = store.db.execute("SELECT finished_at, ok FROM ai_runs").fetchone()
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(row["ok"], 0)


class HttpErrors(unittest.TestCase):
    def _serve(self, status: int) -> str:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(status)
                self.send_header("Location", "https://elsewhere.example/next")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_redirects_can_be_refused(self) -> None:
        from growth import net

        base = self._serve(302)
        with self.assertRaises(net.HttpError) as caught:
            net.request("POST", base + "/submit?token=secret", body={"a": 1}, follow_redirects=False)
        self.assertIn("302", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_errors_name_only_scheme_and_host(self) -> None:
        from growth import net

        with self.assertRaises(net.HttpError) as caught:
            net.get_json("http://127.0.0.1:9/hook/secret-token?key=hidden", timeout=2)
        self.assertIn("http://127.0.0.1", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("hidden", str(caught.exception))
