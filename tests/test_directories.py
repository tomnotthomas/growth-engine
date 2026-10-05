"""The launch-directory pipeline: import, scoring, drafts in the project's voice, launch-day-only
submission to verified sites, and everything else skipped and reported."""

from __future__ import annotations

import json
import os
import unittest
from datetime import timedelta
from unittest import mock

from growth.directories import Entry, entry_id, load_catalogue, parse_list, save_catalogue, score
from growth import net
from growth.runner import run_now

from .helpers import NOW, HomeTestCase

UPSTREAM_SAMPLE = """# Places To Post Your Startup

[Sub-Reddits](#reddit) | [Websites](#websites)

# Reddit

* /r/macgaming - https://www.reddit.com/r/macgaming/
* /r/SideProject - https://www.reddit.com/r/SideProject/

# Websites

* BetaList - https://betalist.com/submit
* Spieleportal - https://spiele.example.de/eintragen
* Capterra - https://www.capterra.com/vendors/sign-up
* BetaList again - https://www.betalist.com/submit/
* Show HN - https://news.ycombinator.com/showhn.html
* Hacker News - https://news.ycombinator.com/

## License
* Not an entry - https://creativecommons.org/
"""


class ParseAndScore(unittest.TestCase):
    def test_parses_the_upstream_format(self) -> None:
        entries = parse_list(UPSTREAM_SAMPLE)
        self.assertEqual(
            [e.id for e in entries],
            [
                "reddit.com/r/macgaming",
                "reddit.com/r/sideproject",
                "betalist.com/submit",
                "spiele.example.de/eintragen",
                "capterra.com/vendors/sign-up",
                "news.ycombinator.com/showhn.html",
                "news.ycombinator.com",
            ],
        )
        self.assertEqual(entries[0].section, "reddit")
        self.assertEqual(entry_id("https://www.BetaList.com./submit/"), "betalist.com/submit")

    def test_scores_for_the_audience_with_reasons(self) -> None:
        profile = {"audience_terms": ["mac", "spiele"], "regions": ["de"]}
        scored = {e.id: score(e, profile) for e in parse_list(UPSTREAM_SAMPLE)}
        german = scored["spiele.example.de/eintragen"]
        self.assertEqual(german.score, 3 + 3 + 1)
        self.assertIn("de domain", german.reasons)
        self.assertIn("early adopters", scored["betalist.com/submit"].value)
        self.assertLess(scored["capterra.com/vendors/sign-up"].score, 0)
        self.assertEqual(scored["reddit.com/r/macgaming"].status, "reddit")  # never automated

    def test_exclude_terms_never_rewrite_an_attempt(self) -> None:
        for status in ("submitted", "failed"):
            entry = Entry(id="x.example", name="X", url="https://x.example/", section="websites", status=status)
            self.assertEqual(score(entry, {"exclude_terms": ["x.example"]}).status, status)
        entry = Entry(id="x.example", name="X", url="https://x.example/", section="websites", status="drafted", listing={"tagline": "t"})
        self.assertEqual(score(entry, {"exclude_terms": ["x.example"]}).status, "excluded")
        self.assertEqual(score(entry, {}).status, "drafted")

    def test_an_old_catalogue_is_keyed_by_host_and_path(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            old = Entry(id="betahunt.example", name="Beta Hunt", url="https://betahunt.example/submit", section="websites", status="submitted")
            save_catalogue(Path(tmp), {old.id: old}, "list.md", "2026-10-01")
            loaded = load_catalogue(Path(tmp))
        self.assertEqual(list(loaded), ["betahunt.example/submit"])
        self.assertEqual(loaded["betahunt.example/submit"].status, "submitted")


class Pipeline(HomeTestCase):
    def catalogue(self, engine) -> dict:
        return json.loads((engine.state_dir / "projects" / "example" / "directories.json").read_text())["entries"]

    def test_sync_draft_and_submit_on_launch_day_only(self) -> None:
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "directories-sync", now=NOW), "ok")
        entries = self.catalogue(engine)
        self.assertEqual(entries["reddit.com/r/audioengineering"]["status"], "reddit")
        self.assertEqual(entries["vendor.example/vendors/sign-up"]["status"], "new")

        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "directories"}):
            self.assertEqual(run_now(engine, "example", "directories-draft", now=NOW), "ok")
        entries = self.catalogue(engine)
        drafted = [e for e in entries.values() if e["status"] == "drafted"]
        refused = [e for e in entries.values() if e["note"].startswith("draft refused")]
        self.assertTrue(drafted)
        self.assertEqual(len(refused), 1)  # the listing that invented "12000 producers"
        self.assertIn("12000", refused[0]["note"])
        self.assertNotIn("vendor.example/vendors/sign-up", [e["id"] for e in drafted])  # negative score: not drafted

        posted = []
        with mock.patch("growth.jobs.directories.net.request", side_effect=lambda *a, **k: posted.append((a, k)) or (200, b"ok")):
            run_now(engine, "example", "directories-submit", now=NOW)  # no launch date yet
            self.assertEqual(posted, [])
            self.edit("projects/example/project.toml", 'start = ""', f'start = "{(NOW + timedelta(days=2)).date()}"')
            engine = self.engine()
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(days=1))
            self.assertEqual(posted, [], "the day before launch nothing is submitted")
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(days=2))
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(days=3))
        self.assertEqual(len(posted), 1, "one submission, to the one verified site, once")
        (method, url), kwargs = posted[0]
        self.assertEqual((method, url), ("POST", "https://betahunt.example/api/submit"))
        self.assertEqual(kwargs["body"]["name"], "Kiln")
        self.assertEqual(kwargs["body"]["url"], "https://kiln.example")
        self.assertFalse(kwargs["follow_redirects"])
        entries = self.catalogue(engine)
        self.assertEqual(entries["betahunt.example/submit"]["status"], "submitted")
        self.assertEqual(entries["betahunt.example/submit"]["submitted_at"], str((NOW + timedelta(days=2)).date()))
        websites = [e for e in entries.values() if e["section"] == "websites" and e["status"] != "submitted"]
        self.assertTrue(websites)
        self.assertTrue(all(e["status"] == "skipped" and e["note"] for e in websites), "every other website is skipped with a reason")
        self.assertIn("needs a person", entries["plugins.example"]["note"])
        self.assertIn("no listing drafted", entries["vendor.example/vendors/sign-up"]["note"])
        self.assertIn("draft refused", entries["audiotools.de/eintragen"]["note"])

    def launched(self):
        engine = self.engine()
        run_now(engine, "example", "directories-sync", now=NOW)
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "directories"}):
            run_now(engine, "example", "directories-draft", now=NOW)
        self.edit("projects/example/project.toml", 'start = ""', f'start = "{NOW.date()}"')
        return self.engine()

    def verify(self, site_id: str, **overrides) -> None:
        site = {
            "id": site_id,
            "method": "form",
            "endpoint": f"https://{site_id.split('/')[0]}/add",
            "terms_url": "https://x.example/terms",
            "terms_checked": "2026-10-01",
            "terms_allow_automation": True,
            "captcha": False,
            "account": False,
            **overrides,
        }
        lines = ["", "[[site]]"] + [f"{k} = {json.dumps(v)}" for k, v in site.items()] + ['fields = { name = "{brand}" }']
        self.append("projects/example/directories/submit.toml", "\n".join(lines) + "\n")

    def test_one_failing_site_does_not_stop_the_others(self) -> None:
        engine = self.launched()
        self.verify("plugins.example")
        posted = []

        def reply(method, url, **kwargs):
            posted.append(url)
            if "betahunt" in url:
                raise net.HttpError("betahunt answered 500")
            return 200, b"ok"

        with mock.patch("growth.jobs.directories.net.request", side_effect=reply):
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(hours=8))
        self.assertEqual(sorted(posted), ["https://betahunt.example/api/submit", "https://plugins.example/add"])
        entries = self.catalogue(engine)
        self.assertEqual(entries["betahunt.example/submit"]["status"], "failed")
        self.assertIn("500", entries["betahunt.example/submit"]["note"])
        self.assertEqual(entries["betahunt.example/submit"]["submitted_at"], "")
        self.assertEqual(entries["plugins.example"]["status"], "submitted")

    def test_redirects_and_unsafe_endpoints_are_not_submissions(self) -> None:
        engine = self.launched()
        self.edit("projects/example/directories/submit.toml", 'method = "api"', 'method = "magic"')
        self.verify("plugins.example")
        with mock.patch("growth.jobs.directories.net.request", return_value=(302, b"")) as request:
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(hours=8))
        self.assertEqual(request.call_count, 1)
        entries = self.catalogue(engine)
        self.assertEqual(entries["plugins.example"]["status"], "failed")
        self.assertIn("302", entries["plugins.example"]["note"])
        self.assertEqual(entries["betahunt.example/submit"]["status"], "skipped")
        self.assertIn("method", entries["betahunt.example/submit"]["note"])

        for endpoint, why in (("http://betahunt.example/add", "https"), ("https://elsewhere.example/add", "not on")):
            self.edit("projects/example/directories/submit.toml", 'method = "magic"', 'method = "form"')
            self.edit("projects/example/directories/submit.toml", 'endpoint = "https://betahunt.example/api/submit"', f'endpoint = "{endpoint}"')
            with mock.patch("growth.jobs.directories.net.request", return_value=(200, b"")) as request:
                run_now(self.engine(), "example", "directories-submit", now=NOW + timedelta(hours=9 if why == "https" else 10))
            request.assert_not_called()
            self.assertIn(why, self.catalogue(engine)["betahunt.example/submit"]["note"])
            self.edit("projects/example/directories/submit.toml", f'endpoint = "{endpoint}"', 'endpoint = "https://betahunt.example/api/submit"')
            self.edit("projects/example/directories/submit.toml", 'method = "form"', 'method = "magic"')

    def test_a_policy_refusal_skips_and_a_skipped_site_is_retried_once_verified(self) -> None:
        engine = self.launched()
        self.edit("projects/example/directories/submit.toml", "terms_allow_automation = true", 'terms_allow_automation = "true"')
        with mock.patch("growth.jobs.directories.net.request", return_value=(200, b"")) as request:
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(hours=8))
            request.assert_not_called()
            self.assertIn("terms-allow-automation", self.catalogue(engine)["betahunt.example/submit"]["note"])
            self.assertEqual(self.catalogue(engine)["plugins.example"]["status"], "skipped")
            self.verify("plugins.example")
            run_now(self.engine(), "example", "directories-submit", now=NOW + timedelta(hours=9))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.catalogue(engine)["plugins.example"]["status"], "submitted")
        self.assertEqual(self.catalogue(engine)["betahunt.example/submit"]["status"], "skipped")

    def test_an_attempt_that_never_reported_back_is_skipped_not_failed(self) -> None:
        engine = self.launched()
        store = self.store(engine)
        store.effect_begin("example", "directory:betahunt.example/submit", "directory-submit", "submit", retry_failed=False)
        with mock.patch("growth.jobs.directories.net.request", return_value=(200, b"")) as request:
            run_now(engine, "example", "directories-submit", now=NOW + timedelta(hours=8))
        request.assert_not_called()
        entry = self.catalogue(engine)["betahunt.example/submit"]
        self.assertEqual(entry["status"], "skipped")
        self.assertEqual(entry["submitted_at"], "")
        self.assertIn("never reported back", entry["note"])
