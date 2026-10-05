"""What a public launch needs from the generator: legal pages before any form, languages that can be
switched on per wave, an on-site game search, guide links, and a goal that counts only the zone."""

from __future__ import annotations

import json
import os
import unittest
from datetime import date
from unittest import mock

from growth import analytics
from growth.config import ConfigError
from growth.goal import goal_report
from growth.runner import run_now
from growth.site.build import BuildError, build_site

from .helpers import NOW, HomeTestCase


class SiteBuild(HomeTestCase):
    def build(self):
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "fetch-data", now=NOW), "ok")
        project = engine.projects["example"]
        result = build_site(project, engine.state_dir / "projects" / "example", engine.dist_dir, now=NOW)
        self.public = engine.dist_dir / "example" / "public"
        return result

    def page(self, path: str) -> str:
        return (self.public / path.strip("/") / "index.html").read_text(encoding="utf-8")

    def test_no_sign_up_form_without_a_complete_legal_notice(self) -> None:
        self.edit("projects/example/project.toml", 'street = "Example Street 1"', 'street = ""')
        with self.assertRaises(BuildError) as caught:
            self.build()
        self.assertIn("[legal] street is empty", str(caught.exception))

    def test_no_sign_up_form_without_a_privacy_page(self) -> None:
        self.edit("projects/example/project.toml", 'privacy = "privacy"', 'privacy = ""')
        with self.assertRaises(BuildError) as caught:
            self.build()
        self.assertIn("[site] privacy is not set", str(caught.exception))

    def test_legal_pages_are_generated_per_language_and_linked(self) -> None:
        self.build()
        self.assertIn("Example Street 1", self.page("/legal/"))
        self.assertIn("Example Street 1", self.page("/de/impressum/"))
        self.assertIn('<a href="/de/datenschutz/">', self.page("/de/"))
        self.assertIn('<a href="/privacy/">Privacy</a>', self.page("/"))

    def test_a_language_that_is_not_live_is_validated_but_not_built(self) -> None:
        self.edit("projects/example/project.toml", 'out = "example"', 'out = "example"\nlive_languages = ["en"]')
        result = self.build()
        self.assertFalse(any(p.lang == "de" for p in result.pages))
        self.assertFalse((self.public / "de").exists())
        self.assertNotIn('hreflang="de"', self.page("/"))
        worker = (self.public.parent / "worker" / "config.js").read_text()
        self.assertNotIn('"de"', worker.split('"languages"')[1].split("]")[0])

    def test_live_languages_must_be_non_empty_and_include_the_default(self) -> None:
        for live, why in (("[]", "is empty"), ('["de"]', "must include the default language 'en'")):
            self.edit("projects/example/project.toml", 'out = "example"', f'out = "example"\nlive_languages = {live}')
            with self.assertRaises(ConfigError) as caught:
                self.engine()
            self.assertIn(why, str(caught.exception))
            self.edit("projects/example/project.toml", f'out = "example"\nlive_languages = {live}', 'out = "example"')

    def test_game_search_data_lists_running_blocked_and_native_items_with_links(self) -> None:
        self.build()
        rows = json.loads((self.public / "data" / "plugins-en.json").read_text())
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Tapeworm Delay"]["status"], "runs")
        self.assertEqual(by_name["Tapeworm Delay"]["href"], "/plugins/tapeworm-delay/")
        self.assertEqual(by_name["Copper Comp"]["status"], "blocked")
        self.assertIn("copy protection", by_name["Copper Comp"]["note"])
        self.assertEqual(by_name["Sable EQ"]["status"], "native")
        self.assertNotIn("image", json.dumps(rows))
        hub = self.page("/plugins/")
        self.assertIn('data-game-search="/data/plugins-en.json"', hub)
        self.assertIn("data-search-miss", hub)  # no match offers the sign-up form

    def test_footer_links_the_guides_and_the_404_speaks_every_live_language(self) -> None:
        self.build()
        self.assertIn('<ul class="foot-guides"', self.page("/"))
        self.assertIn('href="/guide/"', self.page("/"))
        not_found = (self.public / "404.html").read_text()
        self.assertIn('lang="en"', not_found)
        self.assertIn('lang="de"', not_found)
        self.assertIn('href="/de/"', not_found)


class Zone(unittest.TestCase):
    STATS = {
        "totals": {"confirmed": 30},
        "days": [
            {"date": "2026-10-04", "role": "player", "country": "DE", "confirmed": 10, "referred": 2},
            {"date": "2026-10-04", "role": "player", "country": "NL", "confirmed": 5, "referred": 0},
            {"date": "2026-10-04", "role": "player", "country": "US", "confirmed": 15, "referred": 0},
        ],
        "sources": [
            {"source": "reddit", "role": "player", "country": "US", "confirmed": 15},
            {"source": "search", "role": "player", "country": "DE", "confirmed": 15},
        ],
        "cohorts": [],
    }

    def test_only_sign_ups_inside_the_zone_count_toward_the_goal(self) -> None:
        goal = {"target": 20000, "days": 46, "start": "2026-10-01", "zone": ["DE", "AT", "CH", "LU", "NL", "BE", "DK"]}
        report = goal_report(self.STATS, goal, date(2026, 10, 5))
        self.assertEqual(report["confirmed_total"], 15)
        self.assertEqual(report["outside_zone"], 15)
        self.assertEqual([s["source"] for s in report["sources"]], ["search"])

    def test_without_a_zone_everything_counts(self) -> None:
        report = goal_report(self.STATS, {"target": 100, "days": 46}, date(2026, 10, 5))
        self.assertEqual(report["confirmed_total"], 30)
        self.assertNotIn("outside_zone", report)


class StatsSource(HomeTestCase):
    def test_posthog_first_then_the_ledger(self) -> None:
        engine = self.engine()
        project = engine.projects["example"]
        project.analytics = {"provider": "posthog", "host": "https://eu.posthog.example", "project_id": "1", "api_key_env": "PH_KEY"}
        rows = [["2026-10-04", "player", "DE", "search", 4, 1]]
        with mock.patch.dict(os.environ, {"PH_KEY": "phx_test"}), mock.patch.object(analytics, "_hogql", return_value=rows), mock.patch.object(
            analytics, "waitlist_stats", return_value=(None, "no worker")
        ):
            stats, why = analytics.signup_stats(project)
        self.assertEqual(why, "")
        self.assertEqual(stats["source_of_numbers"], "PostHog")
        self.assertEqual(stats["days"][0]["confirmed"], 4)
        ledger = {"totals": {"confirmed": 1}, "days": [], "sources": [], "cohorts": []}
        with mock.patch.object(analytics, "waitlist_stats", return_value=(ledger, "")):
            stats, _ = analytics.signup_stats(project)  # no PostHog key in the environment
        self.assertEqual(stats["source_of_numbers"], "waitlist ledger")

    def test_the_ledger_wins_when_posthog_counts_fewer_confirmations(self) -> None:
        project = self.engine().projects["example"]
        project.analytics = {"provider": "posthog", "host": "https://eu.posthog.example", "project_id": "1", "api_key_env": "PH_KEY"}
        rows = [["2026-10-04", "player", "DE", "search", 2, 0]]
        ledger = {"totals": {"confirmed": 5}, "days": [], "sources": [], "cohorts": []}
        with mock.patch.dict(os.environ, {"PH_KEY": "phx_test"}), mock.patch.object(analytics, "_hogql", return_value=rows), mock.patch.object(
            analytics, "waitlist_stats", return_value=(ledger, "")
        ):
            stats, _ = analytics.signup_stats(project)
        self.assertEqual(stats["source_of_numbers"], "waitlist ledger")
        self.assertEqual(stats["totals"]["confirmed"], 5)

    def test_posthog_query_is_scoped_to_the_site(self) -> None:
        project = self.engine().projects["example"]
        project.analytics = {"provider": "posthog", "host": "https://eu.posthog.example", "project_id": "1", "api_key_env": "PH_KEY"}
        sent = []

        def answer(method, url, body=None, headers=None, timeout=None):
            sent.append(body["query"]["query"])
            return 200, b'{"results": []}'

        with mock.patch.dict(os.environ, {"PH_KEY": "phx_test"}), mock.patch.object(analytics.net, "request", side_effect=answer):
            stats, why = analytics.posthog_signup_stats(project)
        self.assertEqual(why, "")
        self.assertEqual(stats["totals"]["confirmed"], 0)
        self.assertIn("properties.site = 'example'", sent[0])
        self.assertIn(f"LIMIT {analytics.SIGNUP_ROWS}", sent[0])

    def test_posthog_rows_at_the_limit_fall_back_to_the_ledger(self) -> None:
        project = self.engine().projects["example"]
        project.analytics = {"provider": "posthog", "host": "https://eu.posthog.example", "project_id": "1", "api_key_env": "PH_KEY"}
        rows = [["2026-10-04", "player", "DE", "search", 1, 0]] * analytics.SIGNUP_ROWS
        ledger = {"totals": {"confirmed": 7}, "days": [], "sources": [], "cohorts": []}
        with mock.patch.dict(os.environ, {"PH_KEY": "phx_test"}), mock.patch.object(analytics, "_hogql", return_value=rows), mock.patch.object(
            analytics, "waitlist_stats", return_value=(ledger, "")
        ):
            stats, why = analytics.signup_stats(project)
        self.assertEqual(why, "")
        self.assertEqual(stats["source_of_numbers"], "waitlist ledger")
        self.assertEqual(stats["totals"]["confirmed"], 7)
