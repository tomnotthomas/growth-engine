"""The page generator on the example project: pages, index rules, structured data, sitemap,
hreflang, the imagery and numbers checks, kept URLs and the waitlist bundle."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from growth.policy import PolicyViolation
from growth.runner import run_now
from growth.site.build import BuildError, build_site

from .helpers import NOW, HomeTestCase

LD = re.compile(r'<script type="application/ld\+json">\n(.*?)\n</script>', re.S)


class SiteTestCase(HomeTestCase):
    def build(self, now=NOW, fetch: bool = True):
        engine = self.engine()
        if fetch:
            self.assertEqual(run_now(engine, "example", "fetch-data", now=now), "ok")
        project = engine.projects["example"]
        result = build_site(project, engine.state_dir / "projects" / "example", engine.dist_dir, now=now)
        self.public = engine.dist_dir / "example" / "public"
        return result

    def page(self, path: str) -> str:
        return (self.public / path.strip("/") / "index.html").read_text(encoding="utf-8")

    def jsonld_types(self, html: str) -> set[str]:
        data = json.loads(LD.search(html).group(1))
        return {node["@type"] for node in data["@graph"]}

    def make_public(self) -> None:
        self.edit("projects/example/project.toml", "domain_decided = false", "domain_decided = true")
        self.edit("projects/example/project.toml", "indexable = false", "indexable = true")
        self.edit("projects/example/project.toml", 'legal_notice = ""', 'legal_notice = "/legal/"')
        self.edit("projects/example/project.toml", 'privacy = ""', 'privacy = "/privacy/"')


class Pages(SiteTestCase):
    def test_builds_every_page_and_only_eligible_item_pages(self) -> None:
        result = self.build()
        paths = sorted(p.path for p in result.pages)
        self.assertEqual(
            paths,
            ["/", "/de/", "/de/warteliste/", "/guide/", "/plugins/", "/plugins/glasshouse-reverb/", "/plugins/tapeworm-delay/", "/waitlist/"],
        )
        self.assertFalse((self.public / "plugins" / "mothwing-synth").exists())  # under 30 searches
        for path in paths:
            html = self.page(path)
            self.assertEqual(html.count("<h1"), 1, path)
            self.assertNotIn("{brand}", html)
            self.assertIn("Kiln", html)

    def test_nothing_is_indexable_until_the_site_is_public(self) -> None:
        result = self.build()
        self.assertEqual(result.indexable, [])
        self.assertIn("Disallow: /", (self.public / "robots.txt").read_text())
        self.assertFalse((self.public / "sitemap.xml").exists())
        for page in result.pages:
            self.assertIn('<meta name="robots" content="noindex, follow">', self.page(page.path))

    def test_public_site_indexes_only_pages_with_evidence(self) -> None:
        self.make_public()
        result = self.build()
        indexable = {p.path for p in result.indexable}
        self.assertIn("/plugins/tapeworm-delay/", indexable)  # has a measured number
        self.assertNotIn("/plugins/glasshouse-reverb/", indexable)  # thin: no clip, no numbers yet
        self.assertNotIn("/waitlist/", indexable)
        sitemap = (self.public / "sitemap.xml").read_text()
        self.assertIn("<loc>https://kiln.example/plugins/tapeworm-delay/</loc>", sitemap)
        self.assertNotIn("glasshouse", sitemap)
        self.assertNotIn("waitlist", sitemap)
        self.assertIn('hreflang="de" href="https://kiln.example/de/"', sitemap)
        self.assertIn("Sitemap: https://kiln.example/sitemap.xml", (self.public / "robots.txt").read_text())
        self.assertNotIn("noindex", self.page("/plugins/tapeworm-delay/"))
        self.assertIn("noindex", self.page("/plugins/glasshouse-reverb/"))

    def test_hreflang_and_canonical(self) -> None:
        self.build()
        html = self.page("/")
        self.assertIn('<link rel="canonical" href="https://kiln.example/">', html)
        self.assertIn('<link rel="alternate" hreflang="de" href="https://kiln.example/de/">', html)
        self.assertIn('<link rel="alternate" hreflang="x-default" href="https://kiln.example/">', html)
        self.assertIn('<html lang="de">', self.page("/de/"))
        self.assertNotIn('hreflang="de"', self.page("/guide/"))  # English only

    def test_structured_data(self) -> None:
        self.build()
        self.assertEqual(self.jsonld_types(self.page("/")), {"Organization", "WebSite", "FAQPage"})
        item = self.jsonld_types(self.page("/plugins/tapeworm-delay/"))
        self.assertTrue({"BreadcrumbList", "FAQPage", "Article"} <= item)
        self.assertIn("Article", self.jsonld_types(self.page("/guide/")))
        for page in ("/", "/plugins/tapeworm-delay/"):
            self.assertNotIn("Rating", self.page(page))

    def test_item_page_shows_curated_facts_and_measured_numbers_only(self) -> None:
        self.build()
        html = self.page("/plugins/tapeworm-delay/")
        self.assertIn("Tapeworm Delay only ships for Windows.", html)
        self.assertIn("18 ms", html)  # measured, with a source
        self.assertIn("Measured value to come", html)  # resolution and fps were not measured
        self.assertIn('href="/plugins/glasshouse-reverb/"', html)  # similar plugin with a page

    def test_hub_lists_running_blocked_and_native_items(self) -> None:
        self.build()
        html = self.page("/plugins/")
        self.assertIn("Its copy protection refuses to run on a server.", html)
        self.assertIn("Sable EQ", html)
        self.assertIn("Mothwing Synth", html)  # listed, though it has no page
        self.assertIn("Nothing new this fortnight.", html)  # the first fetch is the baseline

    def test_new_items_show_up_as_new(self) -> None:
        self.build()
        live = self.project_dir / "data" / "live.json"
        live.write_text(live.read_text().replace('{"id": 3, "name": "Mothwing Synth"}', '{"id": 3, "name": "Mothwing Synth"}, {"id": 6, "name": "Ember Chorus"}'))
        self.append("projects/example/data/plugins.toml", '[[item]]\nid = 6\nslug = "ember-chorus"\nname = "Ember Chorus"\nstatus = "playable"\nsearches = 40\nwave = 1\n')
        self.build(now=NOW + timedelta(days=1))
        listing = self.page("/plugins/").split("New</h2>")[1].split("</section>")[0]
        self.assertIn("Ember Chorus", listing)

    def test_status_changes_keep_the_url(self) -> None:
        self.build()
        live = self.project_dir / "data" / "live.json"
        live.write_text(json.dumps([{"id": 2, "name": "Glasshouse Reverb"}]))
        self.build(now=NOW + timedelta(hours=7))
        html = self.page("/plugins/tapeworm-delay/")
        self.assertIn("Not offered right now", html)

    def test_waitlist_forms_and_worker_bundle(self) -> None:
        self.build()
        html = self.page("/")
        self.assertIn('action="/api/waitlist" method="post"', html)
        self.assertIn('name="website"', html)  # honeypot
        self.assertIn('<script src="/assets/waitlist.js', html)
        out = self.public.parent
        config = (out / "worker" / "config.js").read_text()
        self.assertIn('"status": {', config)
        self.assertIn('"/de/warteliste/"', config)
        self.assertIn('run_worker_first = ["/api/*", "/r/*"]', (out / "wrangler.toml").read_text())
        self.assertTrue((out / "schema.sql").is_file())


class Guards(SiteTestCase):
    def test_third_party_art_from_the_data_source_never_reaches_a_page(self) -> None:
        self.build()
        cache = json.loads((self.home / "state" / "projects" / "example" / "data" / "plugins_live.json").read_text())
        self.assertNotIn("image", cache["items"][0])
        for file in self.public.rglob("*.html"):
            self.assertNotIn("cdn.vendor.example", file.read_text(encoding="utf-8"))

    def test_an_image_outside_the_media_manifest_fails_the_build_and_keeps_the_old_site(self) -> None:
        self.build()
        before = self.page("/")
        self.edit("projects/example/project.toml", 'share_image = "img/share-card.svg"', 'share_image = "img/stolen-key-art.jpg"')
        with self.assertRaises(PolicyViolation) as caught:
            self.build(fetch=False)
        self.assertEqual(caught.exception.rule, "own-footage-only")
        self.assertEqual(self.page("/"), before)

    def test_game_footage_from_a_publisher_not_allowed_is_refused(self) -> None:
        self.append(
            "projects/example/media.toml",
            '[[media]]\npath = "video/demo.mp4"\nsource = "own-recording"\ncontains_game_footage = true\npublisher = "Someone Else"\n',
        )
        self.append(
            "projects/example/data/measurements.toml",
            '[[measurement]]\nid = "glass"\nfps = 60\nsource = "session"\nmeasured_at = "2026-10-01"\nclip = "video/demo.mp4"\n',
        )
        self.edit("projects/example/data/plugins.toml", 'slug = "glasshouse-reverb"', 'slug = "glasshouse-reverb"\nmeasurement = "glass"')
        with self.assertRaises(PolicyViolation) as caught:
            self.build()
        self.assertEqual(caught.exception.rule, "own-footage-only")

    def test_a_number_without_a_source_fails_the_build(self) -> None:
        self.edit("projects/example/data/measurements.toml", 'source = "example fixture, not a real measurement"\n', "")
        with self.assertRaises(PolicyViolation) as caught:
            self.build()
        self.assertEqual(caught.exception.rule, "no-invented-numbers")

    def test_stale_live_data_stops_the_build(self) -> None:
        self.build()
        with self.assertRaises(BuildError):
            self.build(now=NOW + timedelta(days=4), fetch=False)

    def test_no_page_loads_anything_from_another_host(self) -> None:
        self.build()
        for file in self.public.rglob("*.html"):
            html = file.read_text(encoding="utf-8")
            for ref in re.findall(r'(?:src|href)="(https?://[^"]+)"', html):
                self.assertFalse(ref.endswith((".js", ".css", ".woff2", ".png", ".jpg")), f"{file}: {ref}")

