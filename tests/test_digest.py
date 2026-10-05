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
        self.assertEqual(report["deadline"], "2026-10-30")
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
