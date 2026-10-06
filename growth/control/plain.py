"""Plain words for the control app: what each job does, when it runs, and the setup steps.

The app is used by people who do not know marketing jargon. Everything it shows about the engine's
work is phrased here, once, so the app and its tests agree on the words.
"""

from __future__ import annotations

import re
from typing import Any

from .. import secrets

# kind -> (name, what it does, what it produces)
JOBS: dict[str, tuple[str, str, str]] = {
    "fetch-data": ("Refresh product data", "Downloads the latest product list from your app so the website stays current.", "an up-to-date product list"),
    "build-site": ("Rebuild the website", "Regenerates every page from your settings, texts and product data.", "the website's pages, ready to publish"),
    "deploy": (
        "Publish the website",
        "Before launch it only checks the rebuilt site on the engine's own computer; nothing is uploaded. After you launch, it puts the site online and undoes it if the live check fails.",
        "a checked site (online only after launch)",
    ),
    "indexnow": ("Tell search engines about changes", "Lets Bing and others know which pages changed, so they visit them sooner.", "a notice to search engines"),
    "directories-sync": ("Find launch directories", "Collects websites where new products can be listed, and picks the ones that fit.", "a scored list of directories"),
    "directories-draft": ("Write directory listings", "Claude writes a short listing text for the best directories (uses your AI budget).", "listing texts, ready to submit"),
    "directories-submit": ("Submit to directories", "From launch day on, submits your listing to directories that allow it. Others are left for you.", "submissions, at most once per site"),
    "digest": ("Weekly report", "Writes a summary of the week: visits, sign-ups, what ran and what needs you.", "a report (also in your notifications, if set)"),
}

DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
UNITS = {"m": "minute", "h": "hour", "d": "day", "w": "week"}


def job_words(kind: str) -> dict[str, str]:
    name, does, produces = JOBS.get(kind, (kind.replace("-", " ").capitalize(), "", ""))
    return {"name": name, "does": does, "produces": produces}


def schedule_words(text: str) -> str:
    """"every 6h" -> "Every 6 hours"; "daily 04:00" -> "Every day at 04:00"; "weekly sun 03:00" -> "Every Sunday at 03:00"."""
    parts = text.split()
    if parts and parts[0] == "every" and len(parts) == 2:
        match = re.fullmatch(r"(\d+)([mhdw])", parts[1])
        if match:
            n, unit = int(match.group(1)), UNITS[match.group(2)]
            return f"Every {unit}" if n == 1 else f"Every {n} {unit}s"
    if parts and parts[0] == "daily" and len(parts) == 2:
        return f"Every day at {parts[1]}"
    if parts and parts[0] == "weekly" and len(parts) == 3:
        return f"Every {DAYS.get(parts[1], parts[1])} at {parts[2]}"
    return text


# ---- the guided first run ------------------------------------------------------------------------

SECRET_NAMES = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "GROWTH_DIGEST_WEBHOOK", "GITHUB_TOKEN")


def settable_secrets(project: Any) -> list[str]:
    """The secrets the app may set for a project: Cloudflare, the waitlist stats token, PostHog, the webhook."""
    names = list(SECRET_NAMES)
    for name in (project.raw.get("waitlist", {}).get("stats_token_env"), project.analytics.get("api_key_env")):
        if name and str(name) not in names:
            names.append(str(name))
    return names


def setup_steps(project: Any, registry: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps from a new project to a launched one, in order, each with plain words and where to do it."""
    host = project.site.get("base_url", "")
    legal = project.raw.get("legal", {})
    goal = project.raw.get("goal", {})
    try:
        stored = set(secrets.load())
    except secrets.SecretsError:
        stored = set()
    deploy_jobs = [j for j in project.jobs.values() if j.kind == "deploy"]
    steps = [
        {
            "id": "basics",
            "title": "Name your product and its web address",
            "why": "The website, the sign-up mails and every page use them.",
            "done": not host.endswith(".example") and bool(project.brand.get("name")),
            "go": "edit-basics",
        },
        {
            "id": "goal",
            "title": "Set a sign-up goal",
            "why": "How many people you want on the waitlist, by when. The Results page tracks it.",
            "done": bool(goal.get("target")) and bool(goal.get("start")),
            "go": "edit-basics",
        },
        {
            "id": "legal",
            "title": "Fill in the legal notice",
            "why": "German law needs a name and address on the site before it collects sign-ups.",
            "done": all(str(legal.get(key, "")).strip() for key in ("name", "street", "postcode_city", "email")),
            "go": "edit-basics",
        },
        {
            "id": "build",
            "title": "Build the website once",
            "why": "Creates every page from your settings, so you can check them before anything goes online.",
            "done": bool(registry.get("pages")),
            "go": "build",
        },
        {
            "id": "cloudflare",
            "title": "Connect Cloudflare (free)",
            "why": "Where the website and the waitlist will run. Needs a free account and a token with two permissions.",
            "done": {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"} <= stored,
            "go": "edit-connections",
        },
        {
            "id": "publishing",
            "title": "Turn on publishing",
            "why": "Lets the engine check the rebuilt site whenever it changes, on its own computer. Nothing goes online before launch.",
            "done": any(j.enabled for j in deploy_jobs),
            "go": "edit-schedule",
        },
        {
            "id": "launch",
            "title": "Launch",
            "why": "Your go: from now on the website is online and updates on its own.",
            "done": project.launched,
            "go": "edit-launch",
        },
    ]
    return steps
