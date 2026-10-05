"""Hard lines the engine never crosses, checked before every outward action.

The list is the "never automate" section of the growth plan. Every job that would act outside the
engine (a ping, a post, an email, a published page with media or numbers) describes the act as an
`Action` and passes it through `check_action` first; a violation raises and is logged as blocked.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .util import sha256


@dataclass(frozen=True)
class Rule:
    id: str
    text: str


NEVER_AUTOMATE: tuple[Rule, ...] = (
    Rule("no-scripted-community-posting", "Posting, commenting or voting on Reddit or forums from scripts"),
    Rule("one-account-per-platform", "More than one account per platform"),
    Rule("no-bought-engagement", "Buying followers, views, likes or reviews"),
    Rule("no-engagement-pods", "Engagement pods"),
    Rule("no-duplicate-text", "The same text in several communities"),
    Rule("no-unsolicited-dms", "DMs nobody asked for"),
    Rule("no-unconsented-email", "Emails to lists the recipient didn't join"),
    Rule("own-footage-only", "Game footage in own posts or pages from publishers the project's rules don't allow"),
    Rule("no-paid-boost-of-game-footage", "Boosting any game footage with paid ads"),
    Rule("no-google-indexing-api", "Google's Indexing API for normal pages"),
    Rule("no-invented-numbers", "Fake activity or invented numbers (UWG § 5)"),
    Rule("no-captcha-or-account-creation", "Solving captchas or creating accounts automatically"),
    Rule("terms-allow-automation", "Submitting anywhere whose terms were not checked to allow it"),
)
RULES = {r.id: r for r in NEVER_AUTOMATE}

COMMUNITY_CHANNELS = {"reddit", "forums"}
SCRIPTED_COMMUNITY_KINDS = {"post", "comment", "vote", "dm"}
ENGAGEMENT_GOODS = {"followers", "views", "likes", "reviews", "upvotes", "votes", "subscribers", "comments"}
GOOGLE_INDEXING_HOSTS = {"indexing.googleapis.com"}
OWN_FOOTAGE_SOURCES = {"own-recording"}
FORBIDDEN_JSONLD_TYPES = {"AggregateRating", "Review", "Rating"}

KINDS = {
    "post",
    "comment",
    "vote",
    "dm",
    "email",
    "ping",
    "publish",
    "ad",
    "purchase",
    "engagement-exchange",
    "draft",
    "notify",
    "submit",
}


class PolicyViolation(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(f"[{rule}] {message}")
        self.rule = rule
        self.message = message


@dataclass(frozen=True)
class Media:
    path: str
    contains_game_footage: bool = False
    publisher: str = ""
    source: str = ""  # "own-recording", "neutral", "licensed", ...


@dataclass(frozen=True)
class Number:
    label: str
    value: Any
    source: str = ""
    measured_at: str = ""


@dataclass(frozen=True)
class Action:
    channel: str
    kind: str
    account: str = ""
    community: str = ""
    text: str = ""
    url: str = ""
    target: str = ""
    in_reply_to: str = ""
    recipients: tuple[dict[str, str], ...] = ()
    media: tuple[Media, ...] = ()
    numbers: tuple[Number, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectRules:
    """What one project allows within the hard lines."""

    footage_publishers: frozenset[str] = frozenset()
    accounts: dict[str, str] = field(default_factory=dict)  # platform -> the one account


def fingerprint(text: str) -> str:
    """The same text, however it is spaced or cased, gives the same fingerprint."""
    norm = unicodedata.normalize("NFKC", text).lower()
    norm = re.sub(r"https?://\S+", "", norm)
    norm = re.sub(r"[^\w]+", " ", norm).strip()
    return sha256(norm)


def check_url(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in GOOGLE_INDEXING_HOSTS:
        raise PolicyViolation("no-google-indexing-api", f"refusing to call {host}: it is for job postings and livestreams only")


def check_media(media: tuple[Media, ...] | list[Media], rules: ProjectRules) -> None:
    for item in media:
        if not item.contains_game_footage:
            continue
        if item.source not in OWN_FOOTAGE_SOURCES:
            raise PolicyViolation(
                "own-footage-only", f"{item.path}: game footage must be an own recording, not {item.source or 'unknown'}"
            )
        if item.publisher not in rules.footage_publishers:
            allowed = ", ".join(sorted(rules.footage_publishers)) or "none"
            raise PolicyViolation(
                "own-footage-only", f"{item.path}: footage of a {item.publisher or 'unknown'} game (allowed: {allowed})"
            )


def check_numbers(numbers: tuple[Number, ...] | list[Number]) -> None:
    for number in numbers:
        if not number.source or not number.measured_at:
            raise PolicyViolation(
                "no-invented-numbers", f"{number.label}={number.value!r} has no measurement source and date"
            )


def check_jsonld_types(types: set[str]) -> None:
    bad = types & FORBIDDEN_JSONLD_TYPES
    if bad:
        raise PolicyViolation("no-invented-numbers", f"structured data may not claim ratings or reviews: {sorted(bad)}")


def check_action(action: Action, rules: ProjectRules, texts_seen: Any = None) -> None:
    """Raise PolicyViolation if the action crosses a hard line.

    `texts_seen` is a callable fingerprint -> list of communities that already got that text
    (the store's ledger); without it, the duplicate-text rule cannot be checked and the action is
    refused if it carries community text.
    """
    if action.kind not in KINDS:
        raise ValueError(f"unknown action kind {action.kind!r}")
    if action.url:
        check_url(action.url)

    if action.channel in COMMUNITY_CHANNELS and action.kind in SCRIPTED_COMMUNITY_KINDS:
        raise PolicyViolation(
            "no-scripted-community-posting", f"{action.kind} on {action.channel} must be done by a person; queue a draft"
        )

    configured = rules.accounts.get(action.channel)
    if action.account and configured and action.account != configured:
        raise PolicyViolation(
            "one-account-per-platform", f"{action.channel}: only the account {configured!r} may be used, not {action.account!r}"
        )

    if action.kind == "purchase" and (action.target in ENGAGEMENT_GOODS or action.meta.get("engagement")):
        raise PolicyViolation("no-bought-engagement", f"buying {action.target or 'engagement'} is never allowed")

    if action.kind == "engagement-exchange":
        raise PolicyViolation("no-engagement-pods", "trading likes, follows or comments is never allowed")

    if action.kind == "dm" and not action.in_reply_to:
        raise PolicyViolation("no-unsolicited-dms", "a direct message must answer a message the person sent first")

    if action.kind == "email":
        if not action.recipients:
            raise PolicyViolation("no-unconsented-email", "an email needs recipients with recorded consent")
        for recipient in action.recipients:
            if not recipient.get("consent"):
                raise PolicyViolation(
                    "no-unconsented-email", f"{recipient.get('address', '?')} has no recorded opt-in for this list"
                )

    if action.kind == "ad" and any(m.contains_game_footage for m in action.media):
        raise PolicyViolation("no-paid-boost-of-game-footage", "game footage may never be promoted with paid ads")

    if action.kind == "submit":
        if action.meta.get("captcha") is not False or action.meta.get("account") is not False:
            raise PolicyViolation(
                "no-captcha-or-account-creation", f"{action.target}: only sites recorded with captcha = false and account = false"
            )
        if not action.meta.get("terms_checked") or not action.meta.get("terms_url"):
            raise PolicyViolation("terms-allow-automation", f"{action.target}: record terms_url and the date the terms were checked")

    check_media(action.media, rules)
    check_numbers(action.numbers)

    if action.text and action.community and action.kind in {"post", "comment", "draft"}:
        if texts_seen is None:
            raise PolicyViolation("no-duplicate-text", "cannot check for repeated text without the text ledger")
        others = [c for c in texts_seen(fingerprint(action.text)) if c != action.community]
        if others:
            raise PolicyViolation(
                "no-duplicate-text", f"this text was already used in {', '.join(sorted(others))}; write a new one"
            )
