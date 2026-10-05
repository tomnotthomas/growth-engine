"""directories-sync, directories-draft and directories-submit: the launch-directory pipeline.

- sync (weekly, no AI): import the list, merge into the project's catalogue, score every entry.
- draft (weekly, AI): write each site's listing text in the project's voice from its fact sheet,
  for the best-scoring entries without a draft; text with numbers the fact sheet lacks is refused.
- submit (daily, no AI, never before launch day): submit only to sites the project verified for
  automatic submission (an API or a plain form whose terms allow it, no captcha, no account). Every
  other website entry is marked skipped with the reason, and the digest reports it. Skipped entries
  are checked again on every run, so a site verified later is submitted then.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from .. import net
from ..directories import (
    DEFAULT_SOURCE,
    Entry,
    entry_id,
    launch_reached,
    load_catalogue,
    merge,
    parse_list,
    ranked,
    save_catalogue,
)
from ..policy import Action, PolicyViolation, hostname, on_host
from ..util import read_toml
from .digest import invented_numbers

DRAFT_PROMPT = """Write directory listings for the product described in FACT SHEET, one per site below.
Voice: {voice}
Each listing: "tagline" (at most 80 characters) and "description" (at most 400 characters), written
for that site's readers. Use only facts from the fact sheet; never invent numbers, users, reviews or
partners. Answer with one JSON object mapping each site id to {{"tagline": ..., "description": ...}}
and nothing else.

FACT SHEET:
{facts}

SITES (id, name, why it matters):
{sites}
"""


def _conf(ctx: Any) -> dict[str, Any]:
    return dict(ctx.project.raw.get("directories", {}))


def sync(ctx: Any) -> str:
    conf = _conf(ctx)
    source = str(conf.get("source", DEFAULT_SOURCE))
    if source.startswith("https://"):
        status, raw = net.request("GET", source, headers={"Accept": "text/plain"}, timeout=60, retries=2)
        if status != 200:
            raise net.HttpError(f"directory list answered {status}")
        text = raw.decode("utf-8")
    else:  # a list kept in the project folder
        text = (ctx.project.root / source).read_text(encoding="utf-8")
    fresh = parse_list(text)
    if not fresh:
        raise ValueError("the directory list parsed to no entries; keeping the catalogue as it is")
    entries, added = merge(load_catalogue(ctx.state_dir), fresh, conf)
    save_catalogue(ctx.state_dir, entries, source, ctx.now.date().isoformat())
    return f"{len(entries)} directories in the catalogue, {added} new"


def draft(ctx: Any) -> str:
    conf = _conf(ctx)
    entries = load_catalogue(ctx.state_dir)
    todo = [e for e in ranked(entries) if e.status in {"new", "skipped"} and not e.listing and e.section == "websites" and e.score > 0]
    todo = todo[: int(conf.get("draft_batch", 25))]
    if not todo:
        return "no directory needs a draft"
    facts = (ctx.project.root / conf["fact_sheet"]).read_text(encoding="utf-8")
    sites = "\n".join(f"- {e.id} | {e.name} | {', '.join(e.value + e.reasons) or 'general directory'}" for e in todo)
    prompt = DRAFT_PROMPT.format(voice=conf.get("voice", "plain, friendly, concrete"), facts=facts, sites=sites)
    result = ctx.ai().run(prompt, system="You write short, factual directory listings. Output JSON only.")
    listings = _parse_json(result.text)
    forbidden = list(ctx.project.site.get("forbid_chars", []))
    done = 0
    for entry in todo:
        listing = listings.get(entry.id)
        problem = _check_listing(listing, facts, forbidden)
        if problem:
            entry.note = f"draft refused: {problem}"
            continue
        entry.listing = {"tagline": listing["tagline"].strip(), "description": listing["description"].strip()}
        entry.status, entry.note = "drafted", ""
        done += 1
    save_catalogue(ctx.state_dir, entries, str(conf.get("source", DEFAULT_SOURCE)), ctx.now.date().isoformat())
    return f"drafted {done} of {len(todo)} listings"


def submit(ctx: Any) -> str:
    conf = _conf(ctx)
    launch = str(conf.get("launch_date") or ctx.project.raw.get("goal", {}).get("start", ""))
    today = ctx.now.astimezone(ctx.project.tz).date()
    if not launch_reached(launch, today):
        return f"waiting for launch day ({launch or 'not set'}); nothing submitted"
    sites = read_toml(ctx.project.root / conf["submit"]).get("site", []) if conf.get("submit") else []
    verified = {entry_id("https://" + str(s.get("id", ""))): s for s in sites}
    entries = load_catalogue(ctx.state_dir)
    counts = {"submitted": 0, "failed": 0, "skipped": 0}
    try:
        for entry in ranked(entries):
            if entry.section == "websites" and entry.status in {"new", "drafted", "skipped"}:
                counts[_submit_one(ctx, entry, verified.get(entry.id))] += 1
    finally:
        save_catalogue(ctx.state_dir, entries, str(conf.get("source", DEFAULT_SOURCE)), ctx.now.date().isoformat())
    return f"submitted {counts['submitted']}, failed {counts['failed']}, skipped {counts['skipped']} (need a person)"


def _submit_one(ctx: Any, entry: Entry, site: dict[str, Any] | None) -> str:
    if not entry.listing:
        refused = entry.note.startswith("draft refused")
        return _mark(entry, "skipped", entry.note if refused else "no listing drafted (score too low or not reached yet); needs a person")
    if site is None:
        return _mark(entry, "skipped", "no API or plain form verified for automatic submission; needs a person")
    problem = _site_problem(site, entry)
    if problem:
        return _mark(entry, "skipped", f"verified-submit entry unusable: {problem}")
    action = Action(
        channel="directory-submit",
        kind="submit",
        url=site["endpoint"],
        target=entry.id,
        meta={k: site.get(k) for k in ("terms_allow_automation", "terms_url", "terms_checked", "captcha", "account", "method")},
    )
    try:
        state = ctx.act(action, f"directory:{entry.id}", lambda: _post(ctx, site, entry), retry_failed=False)
    except PolicyViolation as exc:
        return _mark(entry, "skipped", f"refused: {exc}")
    except Exception as exc:
        return _mark(entry, "failed", f"submission failed: {exc}")
    if state in {"done", "already"}:
        entry.submitted_at = ctx.now.date().isoformat()
        return _mark(entry, "submitted", "")
    if state == "unknown":
        return _mark(entry, "skipped", "an earlier attempt never reported back; check the site by hand")
    return _mark(entry, "failed", "an earlier attempt failed; not repeated")


def _mark(entry: Entry, status: str, note: str) -> str:
    entry.status, entry.note = status, note
    return status


def _site_problem(site: dict[str, Any], entry: Entry) -> str:
    endpoint = str(site.get("endpoint", ""))
    home = entry.id.split("/")[0]
    if site.get("method") not in {"api", "form"}:
        return f"method must be 'api' or 'form', not {site.get('method')!r}"
    if not endpoint.startswith("https://"):
        return "endpoint must be https"
    if not on_host(hostname(endpoint), {home}):
        return f"endpoint is not on {home}"
    if not isinstance(site.get("fields"), dict) or not site["fields"]:
        return "fields missing"
    return ""


def _post(ctx: Any, site: dict[str, Any], entry: Entry) -> str:
    project = ctx.project
    values = {
        "brand": project.brand["name"],
        "base_url": project.site["base_url"],
        "tagline": entry.listing.get("tagline", ""),
        "description": entry.listing.get("description", ""),
    }
    fields = {k: re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), str(v)) for k, v in site["fields"].items()}
    if site["method"] == "api":
        status, _ = net.request("POST", site["endpoint"], body=fields, timeout=30, follow_redirects=False)
    else:
        body = urlencode(fields).encode("utf-8")
        status, _ = net.request(
            "POST",
            site["endpoint"],
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            follow_redirects=False,
        )
    if not 200 <= status < 300:
        raise net.HttpError(f"{entry.id} answered {status}")
    return f"HTTP {status}"


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _check_listing(listing: Any, facts: str, forbidden: list[str]) -> str:
    if not isinstance(listing, dict) or not isinstance(listing.get("tagline"), str) or not isinstance(listing.get("description"), str):
        return "missing tagline or description"
    if len(listing["tagline"]) > 80 or len(listing["description"]) > 400:
        return "too long"
    text = listing["tagline"] + " " + listing["description"]
    bad = invented_numbers(text, {"facts": facts})
    if bad:
        return f"numbers not in the fact sheet: {', '.join(bad)}"
    for char in forbidden:
        if char in text:
            return f"contains {char!r}"
    return ""
