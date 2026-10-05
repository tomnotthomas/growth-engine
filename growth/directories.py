"""Launch directories: a per-project catalogue of places to list a product, scored and tracked.

The source is a community list (by default mmccaff/PlacesToPostYourStartup, CC0): lines like
"* Name - https://url" under "# Reddit" and "# Websites". Each project keeps its own catalogue in
its state folder, so status survives re-imports:

    new -> drafted -> submitted | failed            (only entries verified for automatic submission)
    new -> drafted -> skipped (needs a person)      (forms with accounts or captchas, unknown terms)
    reddit                                           (subreddits: the owner's Reddit queue, never automated)

Scoring is deterministic and explained: audience terms in the name or URL, the project's regions
(country domains), early-adopter sites (beta, launch, hunt), a backlink value for every public
directory, and penalties for vendor sign-ups and paid listings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .util import read_json, write_json

DEFAULT_SOURCE = "https://raw.githubusercontent.com/mmccaff/PlacesToPostYourStartup/master/README.md"
_LINE = re.compile(r"^\s*[*-]\s+(?P<name>.+?)\s+-\s+(?P<url>https?://\S+)\s*$")
EARLY_ADOPTER_TERMS = ("beta", "launch", "hunt", "alpha", "early", "betalist", "indie", "maker", "showcase")
PAID_OR_VENDOR_TERMS = ("advertise", "vendors/sign-up", "sponsor", "pricing")
STATUSES = {"new", "drafted", "submitted", "failed", "skipped", "reddit", "excluded"}


@dataclass
class Entry:
    id: str
    name: str
    url: str
    section: str
    status: str = "new"
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    value: list[str] = field(default_factory=list)
    listing: dict[str, str] = field(default_factory=dict)
    note: str = ""
    submitted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def entry_id(url: str) -> str:
    parsed = urlparse(url.lower())
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path.rstrip("/")
    return host + path if host in {"reddit.com", "old.reddit.com"} else host


def parse_list(markdown: str) -> list[Entry]:
    """Every "* Name - URL" line, with the "# Section" it sits under. Duplicates keep the first."""
    section = ""
    entries: dict[str, Entry] = {}
    for line in markdown.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
            continue
        match = _LINE.match(line)
        if not match or section not in {"reddit", "websites"}:
            continue
        url = match.group("url").rstrip(").,")
        eid = entry_id(url)
        if eid and eid not in entries:
            entries[eid] = Entry(id=eid, name=match.group("name").strip(), url=url, section=section)
    return list(entries.values())


def score(entry: Entry, profile: dict[str, Any]) -> Entry:
    """Score an entry for the project's audience; the reasons say why."""
    text = f"{entry.name} {entry.url}".lower()
    host = urlparse(entry.url).hostname or ""
    points, reasons, value = 0, [], []
    if entry.section == "reddit":
        entry.status = "reddit" if entry.status in {"new", "reddit"} else entry.status
        value.append("community")
    for term in profile.get("audience_terms", []):
        if term.lower() in text:
            points += 3
            reasons.append(f"audience term '{term}'")
    for region in profile.get("regions", []):
        if host.endswith("." + region.lower()):
            points += 3
            reasons.append(f"{region} domain")
    if any(term in text for term in EARLY_ADOPTER_TERMS):
        points += 2
        value.append("early adopters")
        reasons.append("early-adopter site")
    if entry.section == "websites":
        points += 1
        value.append("backlink")
    if any(term in text for term in PAID_OR_VENDOR_TERMS):
        points -= 3
        reasons.append("vendor sign-up or paid listing")
    for term in profile.get("exclude_terms", []):
        if term.lower() in text:
            entry.status = "excluded"
            reasons.append(f"excluded by '{term}'")
    entry.score, entry.reasons, entry.value = points, reasons, value
    return entry


def catalogue_path(state_dir: Path) -> Path:
    return state_dir / "directories.json"


def load_catalogue(state_dir: Path) -> dict[str, Entry]:
    raw = read_json(catalogue_path(state_dir), {}) or {}
    return {eid: Entry(**data) for eid, data in raw.get("entries", {}).items()}


def save_catalogue(state_dir: Path, entries: dict[str, Entry], source: str, imported: str) -> None:
    write_json(
        catalogue_path(state_dir),
        {"source": source, "imported": imported, "entries": {eid: e.to_dict() for eid, e in sorted(entries.items())}},
    )


def merge(existing: dict[str, Entry], fresh: list[Entry], profile: dict[str, Any]) -> tuple[dict[str, Entry], int]:
    """Add new entries, refresh names and scores, and keep every entry's status and drafts."""
    added = 0
    for entry in fresh:
        old = existing.get(entry.id)
        if old is None:
            existing[entry.id] = score(entry, profile)
            added += 1
        else:
            old.name, old.url, old.section = entry.name, entry.url, entry.section
            score(old, profile)
    return existing, added


def ranked(entries: dict[str, Entry]) -> list[Entry]:
    return sorted(entries.values(), key=lambda e: (-e.score, e.name.lower()))


def launch_reached(launch_date: str, today: date) -> bool:
    return bool(launch_date) and today >= date.fromisoformat(launch_date)


def summary(entries: dict[str, Entry]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entry in entries.values():
        counts[entry.status] = counts.get(entry.status, 0) + 1
    skipped = [
        {"name": e.name, "url": e.url, "why": e.note or "needs a person (account, captcha, or terms not checked)"}
        for e in ranked(entries)
        if e.status == "skipped"
    ][:10]
    return {"total": len(entries), "by_status": dict(sorted(counts.items())), "top_skipped": skipped}
