"""Every channel the engine knows, and how far each may be automated.

Levels:
- auto: runs on its own once a project enables it.
- human-queue: the engine only drafts; a person posts. Reddit is the only one, by design.
- needs-approval: stays off until the platform grants an approval; the project must record it.
- off: the platform forbids automating it, or it needs a person who is not available. Never runs.

The levels are platform facts, not project choices, so they live here and a project can only
switch a channel on within its level. docs/channels.md mirrors this table (a test keeps them equal).
"""

from __future__ import annotations

from dataclasses import dataclass

AUTO, HUMAN_QUEUE, NEEDS_APPROVAL, OFF = "auto", "human-queue", "needs-approval", "off"


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    level: str
    why: str


CHANNELS: dict[str, Channel] = {
    c.id: c
    for c in (
        Channel("website", "Static search pages (generated site)", AUTO, "Own pages on own hosting."),
        Channel("indexnow", "IndexNow pings (Bing, Yandex and others)", AUTO, "Free, keyed to the own domain."),
        Channel(
            "analytics",
            "Reading own analytics (PostHog) for the digest",
            AUTO,
            "Read-only access to the project's own data.",
        ),
        Channel(
            "digest",
            "Weekly digest to the owner",
            AUTO,
            "A report to the owner; nothing is published.",
        ),
        Channel(
            "reddit",
            "Reddit replies and posts",
            HUMAN_QUEUE,
            "Reddit bans automated posting, commenting and voting; new API access needs approval since "
            "11 Nov 2025. The engine may only queue drafts that the owner posts from their own account.",
        ),
        Channel(
            "forums",
            "German forums (ComputerBase, Hardwareluxx, PCGH, macuser.de)",
            OFF,
            "Forum rules punish promotional posts, automated or not, and nobody is available to post by hand.",
        ),
        Channel(
            "comment-replies",
            "Replies to TikTok and Instagram comments",
            OFF,
            "No reply API for small accounts; replies would need a person.",
        ),
        Channel(
            "tiktok",
            "TikTok posting",
            NEEDS_APPROVAL,
            "Unaudited Content Posting API clients can only post privately; public posting needs TikTok's "
            "audit, and the inbox-draft upload needs an approved video.upload scope.",
        ),
        Channel(
            "youtube",
            "YouTube Shorts uploads",
            NEEDS_APPROVAL,
            "Uploads from an unverified API project are forced to private until Google's API audit passes.",
        ),
        Channel(
            "instagram",
            "Instagram Reels publishing (Graph API)",
            NEEDS_APPROVAL,
            "Allowed for an own business account through an own app; needs that app and account set up "
            "and recorded first.",
        ),
        Channel(
            "outreach-email",
            "Press, creator and publisher emails",
            OFF,
            "UWG § 7 needs consent for advertising email, B2B included; individual pitches need a person to "
            "send them, and the owner opted out of manual steps.",
        ),
        Channel(
            "waitlist-email",
            "Waitlist, referral and milestone emails (double opt-in only)",
            NEEDS_APPROVAL,
            "Needs the own domain, a sender (Resend or PostHog Workflows) and a consent log first.",
        ),
        Channel(
            "directory-submit",
            "Launch directories with an API or a plain form",
            AUTO,
            "Only sites the project verified: an API or a form whose terms allow automated submission, with "
            "no captcha and no account. Never before launch day, once per site.",
        ),
        Channel(
            "directories",
            "Directory listings that need an account, a captcha or a person, and the Steam curator page",
            OFF,
            "Captchas and account creation are never automated; these are skipped and reported in the digest.",
        ),
        Channel(
            "google-indexing-api",
            "Google Indexing API",
            OFF,
            "Only for job postings and livestreams; using it for normal pages risks losing access.",
        ),
        Channel(
            "paid-ads",
            "Paid ads and boosted posts",
            OFF,
            "Costs money; and boosting game footage is never allowed.",
        ),
    )
}


def human_queue_channels() -> list[str]:
    return [c.id for c in CHANNELS.values() if c.level == HUMAN_QUEUE]
