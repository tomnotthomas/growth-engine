"""Config loading: the example home loads; every class of mistake is reported, not half-run."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from growth.config import ConfigError, default_home, load_engine
from growth.runner import run_now
from growth.schedule import parse_schedule

from .helpers import HomeTestCase


class LoadExample(HomeTestCase):
    def test_example_home_loads(self) -> None:
        engine = self.engine()
        self.assertEqual(sorted(engine.projects), ["example"])
        project = engine.projects["example"]
        self.assertEqual(project.languages, ["en", "de"])
        self.assertEqual(project.brand["name"], "Kiln")
        self.assertTrue(project.channel_enabled("website"))
        self.assertFalse(project.channel_enabled("reddit"))
        self.assertEqual(engine.jobs["weekly-digest"].schedule, parse_schedule("weekly mon 05:30"))
        self.assertEqual(project.rules.footage_publishers, frozenset({"Kiln Audio"}))

    def test_home_comes_from_the_environment_never_the_repo(self) -> None:
        with mock.patch.dict(os.environ, {"GROWTH_HOME": "/srv/private-home"}):
            self.assertEqual(str(default_home()), "/srv/private-home")

    def test_missing_home_is_explained(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_engine(self.tmp / "nowhere")
        self.assertIn("GROWTH_HOME", str(caught.exception))

    def test_relative_state_dir_from_the_environment_lives_in_the_home(self) -> None:
        with mock.patch.dict(os.environ, {"GROWTH_STATE_DIR": "elsewhere/state"}):
            self.assertEqual(load_engine(self.home).state_dir, self.home.resolve() / "elsewhere" / "state")
        with mock.patch.dict(os.environ, {"GROWTH_STATE_DIR": "~/growth-state"}):
            self.assertEqual(load_engine(self.home).state_dir, Path("~/growth-state").expanduser())

    def test_disabled_project_is_skipped(self) -> None:
        self.edit("projects/example/project.toml", "enabled = true", "enabled = false")
        self.assertEqual(self.engine().projects, {})


class Rejects(HomeTestCase):
    def assertRejected(self, fragment: str) -> None:
        with self.assertRaises(ConfigError) as caught:
            self.engine()
        joined = "\n".join(caught.exception.errors)
        self.assertIn(fragment, joined)

    def test_channel_the_platform_forbids(self) -> None:
        self.append("projects/example/project.toml", "[channels.forums]\nenabled = true\n")
        self.assertRejected("channels.forums: cannot be enabled")

    def test_channel_that_needs_an_approval(self) -> None:
        self.append("projects/example/project.toml", "[channels.tiktok]\nenabled = true\n")
        self.assertRejected("channels.tiktok: needs a platform approval first")

    def test_channel_with_a_recorded_approval_loads(self) -> None:
        self.append(
            "projects/example/project.toml",
            '[channels.tiktok]\nenabled = true\napproval = { granted = "2026-09-01", evidence = "audit email" }\n',
        )
        self.assertTrue(self.engine().projects["example"].channel_enabled("tiktok"))

    def test_more_than_one_account(self) -> None:
        self.append("projects/example/project.toml", '[channels.reddit]\nenabled = false\naccounts = ["a", "b"]\n')
        self.assertRejected("one account per platform")

    def test_unknown_channel(self) -> None:
        self.append("projects/example/project.toml", "[channels.myspace]\nenabled = true\n")
        self.assertRejected("unknown channel")

    def test_job_on_a_channel_that_is_off(self) -> None:
        self.append("projects/example/project.toml", '[jobs.ping]\nkind = "indexnow"\nschedule = "daily 05:00"\nkey = "abcdefgh12"\n')
        self.assertRejected("needs channel 'indexnow' enabled")

    def test_switched_off_job_on_a_channel_that_is_off_loads_but_never_runs(self) -> None:
        self.only_core_jobs()
        self.edit("projects/example/project.toml", "[channels.directory-submit]\nenabled = true", "[channels.directory-submit]\nenabled = false")
        engine = self.engine()
        with self.assertRaisesRegex(KeyError, "switched off"):
            run_now(engine, "example", "directories-submit")

    def test_pre_launch_home_with_indexnow_off_loads(self) -> None:
        self.append("projects/example/project.toml", '[jobs.ping]\nkind = "indexnow"\nschedule = "daily 05:00"\nenabled = false\nkey = ""\n')
        engine = self.engine()
        self.assertFalse(engine.projects["example"].channel_enabled("indexnow"))
        with self.assertRaisesRegex(KeyError, "switched off"):
            run_now(engine, "example", "ping")

    def test_ai_args_that_need_a_paid_key(self) -> None:
        self.edit("engine.toml", "extra_args = []", 'extra_args = ["--max-budget-usd=5"]')
        self.assertRejected("may not contain --max-budget-usd=5")
        self.edit("engine.toml", 'extra_args = ["--max-budget-usd=5"]', "extra_args = []")
        self.edit("engine.toml", 'command = ["claude"]', 'command = ["claude", "--bare"]')
        self.assertRejected("may not contain --bare")

    def test_bad_schedule(self) -> None:
        self.edit("projects/example/project.toml", 'schedule = "every 6h"', 'schedule = "every 1m"')
        self.assertRejected("shortest interval is 5m")

    def test_unknown_job_kind(self) -> None:
        self.append("projects/example/project.toml", '[jobs.post]\nkind = "auto-post"\nschedule = "daily 05:00"\n')
        self.assertRejected("unknown kind 'auto-post'")

    def test_engine_job_in_a_project(self) -> None:
        self.append("projects/example/project.toml", '[jobs.digest]\nkind = "digest"\nschedule = "weekly mon 05:00"\n')
        self.assertRejected("runs once for the engine")

    def test_indexable_needs_domain_and_legal_pages(self) -> None:
        self.edit("projects/example/project.toml", "indexable = false", "indexable = true")
        self.assertRejected("indexable needs domain_decided")

    def test_google_indexing_api_as_a_data_source(self) -> None:
        self.append(
            "projects/example/project.toml",
            '[data.bad]\nkind = "http-json"\nurl = "https://indexing.googleapis.com/v3/urlNotifications:publish"\n',
        )
        self.assertRejected("no-google-indexing-api")

    def test_brand_written_out_in_copy(self) -> None:
        self.edit("projects/example/pages/guide.toml", "Three ways, compared.", "Kiln compared.")
        self.assertRejected("write {brand} instead of 'Kiln'")

    def test_unknown_placeholder(self) -> None:
        self.edit("projects/example/pages/guide.toml", "Three ways, compared.", "Three {wayz}.")
        self.assertRejected("unknown placeholder {wayz}")

    def test_link_to_a_missing_page(self) -> None:
        self.edit("projects/example/pages/landing.toml", "[See the list](page:plugins)", "[See](page:nope)")
        self.assertRejected("link to unknown page page:nope")

    def test_forbidden_character(self) -> None:
        self.edit("projects/example/pages/guide.toml", "Three ways, compared.", "Three ways — compared.")
        self.assertRejected("which the project's style forbids")

    def test_keyword_missing_from_title(self) -> None:
        self.edit("projects/example/keywords.toml", 'primary = "audio plugins browser"', 'primary = "vst hosting"')
        self.assertRejected("primary keyword 'vst hosting'")

    def test_blocked_item_needs_a_reason(self) -> None:
        self.edit("projects/example/data/plugins.toml", 'reason = "Its copy protection refuses to run on a server."\n', "")
        self.assertRejected("a blocked item needs its reason")

    def test_all_errors_are_listed_together(self) -> None:
        self.append("projects/example/project.toml", "[channels.forums]\nenabled = true\n[channels.myspace]\nenabled = true\n")
        with self.assertRaises(ConfigError) as caught:
            self.engine()
        self.assertGreaterEqual(len(caught.exception.errors), 2)
