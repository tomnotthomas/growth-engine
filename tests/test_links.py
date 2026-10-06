"""The link check CI and the deploy job run on a built site."""

from __future__ import annotations

from growth.runner import run_now
from growth.site.links import check_links

from .helpers import NOW, HomeTestCase


class Links(HomeTestCase):
    def build(self):
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "fetch-data", now=NOW), "ok")
        self.assertEqual(run_now(engine, "example", "build-site", now=NOW), "ok")
        return engine.dist_dir / "example" / "public"

    def test_the_example_site_has_no_broken_links(self) -> None:
        self.assertEqual(check_links(self.build()), [])

    def test_a_broken_link_is_reported(self) -> None:
        public = self.build()
        page = public / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace("</body>", '<a href="/nowhere/">x</a><img src="/img/missing.png"></body>'))
        problems = check_links(public)
        self.assertEqual(len(problems), 2)
        self.assertIn("/nowhere/", problems[0])

    def test_worker_routes_and_external_links_are_not_files(self) -> None:
        public = self.build()
        page = public / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace("</body>", '<a href="/api/waitlist">a</a><a href="https://x.example/">b</a><a href="#top">c</a></body>'))
        self.assertEqual(check_links(public), [])
