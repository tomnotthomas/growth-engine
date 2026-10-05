"""The never-automate list (growth plan 1.11) as hard checks: one test per line of the list,
plus the channel registry's rule that Reddit drafts are the only human queue."""

from __future__ import annotations

import http.server
import re
import threading
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from growth import channels as ch
from growth import net
from growth.policy import (
    NEVER_AUTOMATE,
    Action,
    Media,
    Number,
    PolicyViolation,
    ProjectRules,
    check_action,
    fingerprint,
)
from growth.queue import QueueRefused, add_draft, open_drafts

from .helpers import NOW, HomeTestCase

RULES = ProjectRules(footage_publishers=frozenset({"Valve"}), accounts={"reddit": "u/owner"})
REPO = Path(__file__).resolve().parent.parent


def no_texts(_: str) -> list[str]:
    return []


class NeverAutomate(unittest.TestCase):
    def assertBlocked(self, rule: str, action: Action, texts=no_texts) -> None:
        with self.assertRaises(PolicyViolation) as caught:
            check_action(action, RULES, texts)
        self.assertEqual(caught.exception.rule, rule)

    def test_every_rule_has_a_test(self) -> None:
        tested = {name[len("test_") :].replace("_", "-") for name in dir(self) if name.startswith("test_")}
        for rule in NEVER_AUTOMATE:
            self.assertIn(rule.id, tested, f"no test for never-automate rule {rule.id}")

    def test_no_scripted_community_posting(self) -> None:
        for channel in ("reddit", "forums"):
            for kind in ("post", "comment", "vote", "dm"):
                self.assertBlocked("no-scripted-community-posting", Action(channel=channel, kind=kind, in_reply_to="x"))
        self.assertBlocked("no-scripted-community-posting", Action(channel="reddit", kind="publish"))
        for url in ("https://www.reddit.com/api/submit", "https://oauth.reddit.com./api/comment", "https://www.computerbase.de/forum/"):
            self.assertBlocked("no-scripted-community-posting", Action(channel="directory-submit", kind="submit", url=url))
        check_action(Action(channel="reddit", kind="draft", url="https://www.reddit.com/r/x/comments/1"), RULES, no_texts)

    def test_one_account_per_platform(self) -> None:
        self.assertBlocked("one-account-per-platform", Action(channel="reddit", kind="draft", account="u/second"))

    def test_no_bought_engagement(self) -> None:
        for good in ("followers", "views", "likes", "reviews"):
            self.assertBlocked("no-bought-engagement", Action(channel="instagram", kind="purchase", target=good))

    def test_no_engagement_pods(self) -> None:
        self.assertBlocked("no-engagement-pods", Action(channel="instagram", kind="engagement-exchange"))

    def test_no_duplicate_text(self) -> None:
        seen = {fingerprint("Try it, it's great!"): ["r/macgaming"]}
        action = Action(channel="reddit", kind="draft", community="r/linux_gaming", text="try it -  it's GREAT")
        self.assertBlocked("no-duplicate-text", action, lambda fp: seen.get(fp, []))
        same_place = Action(channel="reddit", kind="draft", community="r/macgaming", text="Try it, it's great!")
        check_action(same_place, RULES, lambda fp: seen.get(fp, []))  # the same thread's community is fine

    def test_no_unsolicited_dms(self) -> None:
        self.assertBlocked("no-unsolicited-dms", Action(channel="instagram", kind="dm"))
        check_action(Action(channel="instagram", kind="dm", in_reply_to="msg-1"), RULES, no_texts)

    def test_no_unconsented_email(self) -> None:
        self.assertBlocked("no-unconsented-email", Action(channel="waitlist-email", kind="email"))
        self.assertBlocked(
            "no-unconsented-email",
            Action(channel="waitlist-email", kind="email", recipients=({"address": "a@b.example"},)),
        )
        check_action(
            Action(channel="waitlist-email", kind="email", recipients=({"address": "a@b.example", "consent": "doi-42"},)),
            RULES,
            no_texts,
        )

    def test_own_footage_only(self) -> None:
        other = Media(path="clip.mp4", contains_game_footage=True, publisher="CD PROJEKT RED", source="own-recording")
        self.assertBlocked("own-footage-only", Action(channel="instagram", kind="post", media=(other,)))
        fan = Media(path="clip.mp4", contains_game_footage=True, publisher="Valve", source="community-screenshot")
        self.assertBlocked("own-footage-only", Action(channel="instagram", kind="post", media=(fan,)))
        ours = Media(path="clip.mp4", contains_game_footage=True, publisher="Valve", source="own-recording")
        check_action(Action(channel="instagram", kind="post", media=(ours,)), RULES, no_texts)

    def test_no_paid_boost_of_game_footage(self) -> None:
        ours = Media(path="clip.mp4", contains_game_footage=True, publisher="Valve", source="own-recording")
        self.assertBlocked("no-paid-boost-of-game-footage", Action(channel="paid-ads", kind="ad", media=(ours,)))

    def test_no_google_indexing_api(self) -> None:
        for host in ("indexing.googleapis.com", "Indexing.GoogleAPIs.com."):
            url = f"https://{host}/v3/urlNotifications:publish"
            self.assertBlocked("no-google-indexing-api", Action(channel="website", kind="ping", url=url))

    def test_no_invented_numbers(self) -> None:
        self.assertBlocked(
            "no-invented-numbers", Action(channel="website", kind="publish", numbers=(Number(label="fps", value=60),))
        )
        check_action(
            Action(channel="website", kind="publish", numbers=(Number(label="fps", value=60, source="session 7", measured_at="2026-10-01"),)),
            RULES,
            no_texts,
        )


    def test_no_captcha_or_account_creation(self) -> None:
        base = {"terms_url": "https://d.example/terms", "terms_checked": "2026-10-01"}
        self.assertBlocked("no-captcha-or-account-creation", Action(channel="directory-submit", kind="submit", meta={**base, "captcha": True, "account": False}))
        self.assertBlocked("no-captcha-or-account-creation", Action(channel="directory-submit", kind="submit", meta={**base, "captcha": False, "account": True}))
        self.assertBlocked("no-captcha-or-account-creation", Action(channel="directory-submit", kind="submit", meta=base))

    def test_terms_allow_automation(self) -> None:
        verified = {
            "captcha": False,
            "account": False,
            "terms_url": "https://d.example/terms",
            "terms_checked": "2026-10-01",
            "terms_allow_automation": True,
        }
        check_action(Action(channel="directory-submit", kind="submit", meta=verified), RULES, no_texts)
        check_action(Action(channel="directory-submit", kind="submit", meta={**verified, "terms_checked": date(2026, 10, 1)}), RULES, no_texts)
        missing = {k: v for k, v in verified.items() if k != "terms_allow_automation"}
        self.assertBlocked("terms-allow-automation", Action(channel="directory-submit", kind="submit", meta=missing))
        for value in (False, "true", "yes", 1, None):
            meta = {**verified, "terms_allow_automation": value}
            self.assertBlocked("terms-allow-automation", Action(channel="directory-submit", kind="submit", meta=meta))
        for key, value in (("terms_url", ""), ("terms_checked", ""), ("terms_checked", "1 Oct 2026"), ("terms_checked", "2026-13-01")):
            meta = {**verified, key: value}
            self.assertBlocked("terms-allow-automation", Action(channel="directory-submit", kind="submit", meta=meta))

    def test_channel_level(self) -> None:
        for channel, kind in (("forums", "draft"), ("comment-replies", "notify"), ("outreach-email", "publish"), ("directories", "publish")):
            self.assertBlocked("channel-level", Action(channel=channel, kind=kind))
        for channel in ("Reddit", "nowhere"):
            with self.assertRaises(ValueError):
                check_action(Action(channel=channel, kind="draft"), RULES, no_texts)
        check_action(Action(channel="indexnow", kind="ping", url="https://api.indexnow.org/indexnow"), RULES, no_texts)
        check_action(Action(channel="digest", kind="notify", url="https://hooks.example/x"), RULES, no_texts)
        project = ProjectRules(channels=frozenset({"website"}))
        with self.assertRaises(PolicyViolation) as caught:
            check_action(Action(channel="indexnow", kind="ping", url="https://api.indexnow.org/indexnow"), project, no_texts)
        self.assertEqual(caught.exception.rule, "channel-level")
        check_action(Action(channel="website", kind="publish"), project, no_texts)


class ChannelRegistry(unittest.TestCase):
    def test_reddit_is_the_only_human_queue(self) -> None:
        self.assertEqual(ch.human_queue_channels(), ["reddit"])

    def test_platform_gated_channels_stay_off(self) -> None:
        self.assertEqual(ch.CHANNELS["tiktok"].level, ch.NEEDS_APPROVAL)
        self.assertEqual(ch.CHANNELS["youtube"].level, ch.NEEDS_APPROVAL)
        for off in ("forums", "comment-replies", "google-indexing-api", "outreach-email", "paid-ads"):
            self.assertEqual(ch.CHANNELS[off].level, ch.OFF, off)

    def test_docs_list_every_channel_as_the_registry_does(self) -> None:
        doc = (REPO / "docs" / "channels.md").read_text(encoding="utf-8")
        rows = {cid: (level, label, why) for cid, level, label, why in re.findall(r"^\| `([a-z-]+)` \| ([a-z-]+) \| (.*?) \| (.*?) \|$", doc, re.M)}
        self.assertEqual(rows, {c.id: (c.level, c.label, c.why) for c in ch.CHANNELS.values()})


class HumanQueue(HomeTestCase):
    def test_only_reddit_gets_drafts_and_never_the_same_text_twice(self) -> None:
        engine = self.engine()
        store = self.store(engine)
        rules = replace(engine.projects["example"].rules, channels=frozenset({"reddit"}))
        common = dict(thread_url="https://www.reddit.com/r/x/comments/1", title="Q", why="fits", now=NOW)
        with self.assertRaises(QueueRefused):
            add_draft(store, engine.state_dir, "example", rules, channel="forums", community="pcgh", text="hi", **common)
        add_draft(store, engine.state_dir, "example", rules, channel="reddit", community="r/macgaming", text="An honest answer.", **common)
        with self.assertRaises(PolicyViolation):
            add_draft(store, engine.state_dir, "example", rules, channel="reddit", community="r/linux", text="an honest answer", **common)
        drafts = open_drafts(engine.state_dir, "example")
        self.assertEqual([d.community for d in drafts], ["r/macgaming"])
        self.assertTrue(store.blocks_since(NOW - timedelta(days=1)) == [])  # queue refusals are not job blocks


class _Redirects(http.server.BaseHTTPRequestHandler):
    """/to-google redirects to the Indexing API; /to-ok redirects to /ok, which answers 200."""

    def do_GET(self) -> None:  # noqa: N802
        targets = {"/to-google": "https://indexing.googleapis.com/v3/urlNotifications:publish", "/to-ok": "/ok"}
        if self.path in targets:
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


class RedirectPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Redirects)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_a_redirect_target_passes_the_url_policy(self) -> None:
        self.assertEqual(net.request("GET", self.base + "/to-ok"), (200, b"ok"))
        with self.assertRaises(PolicyViolation):
            net.request("GET", self.base + "/to-google")

    def test_a_keyed_request_never_follows_a_redirect(self) -> None:
        with self.assertRaises(net.HttpError) as caught:
            net.request("GET", self.base + "/to-ok", headers={"Authorization": "Bearer secret"})
        self.assertIn("302", str(caught.exception))
