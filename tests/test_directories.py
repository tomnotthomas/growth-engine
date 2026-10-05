"""The launch-directory pipeline: import, scoring, drafts in the project's voice, launch-day-only
submission to verified sites, and everything else skipped and reported."""

from __future__ import annotations

import json
import os
import unittest
from datetime import timedelta
from unittest import mock

from growth.directories import entry_id, parse_list, score
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
* BetaList again - https://www.betalist.com/

## License
* Not an entry - https://creativecommons.org/
"""


class ParseAndScore(unittest.TestCase):
    def test_parses_the_upstream_format(self) -> None:
        entries = parse_list(UPSTREAM_SAMPLE)
        self.assertEqual([e.id for e in entries], ["reddit.com/r/macgaming", "reddit.com/r/sideproject", "betalist.com", "spiele.example.de", "capterra.com"])
        self.assertEqual(entries[0].section, "reddit")
        self.assertEqual(entry_id("https://www.BetaList.com/submit"), "betalist.com")

    def test_scores_for_the_audience_with_reasons(self) -> None:
        profile = {"audience_terms": ["mac", "spiele"], "regions": ["de"]}
        scored = {e.id: score(e, profile) for e in parse_list(UPSTREAM_SAMPLE)}
        german = scored["spiele.example.de"]
        self.assertEqual(german.score, 3 + 3 + 1)
        self.assertIn("de domain", german.reasons)
        self.assertIn("early adopters", scored["betalist.com"].value)
        self.assertLess(scored["capterra.com"].score, 0)
        self.assertEqual(scored["reddit.com/r/macgaming"].status, "reddit")  # never automated


class Pipeline(HomeTestCase):
    def catalogue(self, engine) -> dict:
        return json.loads((engine.state_dir / "projects" / "example" / "directories.json").read_text())["entries"]

    def test_sync_draft_and_submit_on_launch_day_only(self) -> None:
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "directories-sync", now=NOW), "ok")
        entries = self.catalogue(engine)
        self.assertEqual(entries["reddit.com/r/audioengineering"]["status"], "reddit")
        self.assertEqual(entries["vendor.example"]["status"], "new")

        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "directories"}):
            self.assertEqual(run_now(engine, "example", "directories-draft", now=NOW), "ok")
        entries = self.catalogue(engine)
        drafted = [e for e in entries.values() if e["status"] == "drafted"]
        refused = [e for e in entries.values() if e["note"].startswith("draft refused")]
        self.assertTrue(drafted)
        self.assertEqual(len(refused), 1)  # the listing that invented "12000 producers"
        self.assertIn("12000", refused[0]["note"])
        self.assertNotIn("vendor.example", [e["id"] for e in drafted])  # negative score: not drafted

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
        entries = self.catalogue(engine)
        self.assertEqual(entries["betahunt.example"]["status"], "submitted")
        skipped = [e for e in entries.values() if e["status"] == "skipped"]
        self.assertTrue(skipped)
        self.assertTrue(all("needs a person" in e["note"] for e in skipped))
