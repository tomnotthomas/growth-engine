"""Render the weekly digest as Markdown (for the webhook and the archive) and as one HTML page.

The HTML uses the base theme's paper band: off-white paper, ink, hairlines, a wide display face
for the masthead, mono only for figures. Every number in the tables comes from the facts, never from the AI.
"""

from __future__ import annotations

import re
from typing import Any, Callable

FUNNEL = ("waitlist_form_view", "waitlist_submit", "waitlist_signup", "waitlist_confirmed", "referral_sent", "referral_joined")


def render_markdown(facts: dict[str, Any], narrative: str, note: str) -> str:
    out = [f"# Growth digest, {facts['week']['from']} to {facts['week']['to']}", ""]
    if note:
        out += [f"_{note}_", ""]
    if narrative:
        out += [narrative, ""]
    out.append(f"AI runs this week: {facts['ai']['runs_7d']} of {facts['ai']['budget_per_week']} budgeted.")
    for p in facts["projects"]:
        out += ["", f"## {p['name']}", ""]
        goal = p.get("goal")
        if goal:
            zone = f" in {goal['zone']}" if goal.get("zone") else ""
            out.append(f"- Goal ({goal['name']}{zone}): {goal.get('confirmed_total', 'n/a')} of {goal['target']}; {goal.get('status', '')}")
            if goal.get("outside_zone"):
                out.append(f"- Confirmed outside the zone (not counted): {goal['outside_zone']}")
            if goal.get("numbers_from"):
                out.append(f"- Numbers from: {goal['numbers_from']}")
            if goal.get("needed_per_day") is not None:
                out.append(f"- Needed per day: {goal['needed_per_day']}; last 7 days: {goal.get('avg_per_day_7')} per day")
            if goal.get("k_factor") is not None:
                out.append(f"- Referral k-factor: {goal['k_factor']}")
            for src in goal.get("sources", [])[:6]:
                out.append(f"  - {src['source']}: {src['confirmed']}")
        traffic = p["traffic"]
        if traffic.get("connected"):
            out.append(f"- Pageviews (7 days): {traffic['pageviews_7d']}")
            funnel = traffic.get("funnel_7d", {})
            if funnel:
                out.append("- Funnel (7 days): " + ", ".join(f"{name} {funnel.get(name, 0)}" for name in FUNNEL))
        else:
            out.append(f"- Traffic: not connected ({traffic.get('why')})")
        site = p["site"]
        out.append(f"- Site: {site['pages']} pages, {site['indexable']} indexable, last build {site['last_build']}, public: {site['public']}")
        out.append(f"- Reddit drafts waiting: {p['reddit_drafts_waiting']}")
        if p.get("directories"):
            d = p["directories"]
            out.append(f"- Launch directories: {d['total']} ({', '.join(f'{n} {s}' for s, n in d['by_status'].items())})")
            for entry in d["top_skipped"]:
                out.append(f"  - skipped {entry['name']} ({entry['url']}): {entry['why']}")
        for job, counts in p["jobs"].items():
            out.append(f"- {job}: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
        for problem in p["problems"]:
            out.append(f"- {problem['status'].upper()} {problem['job']} at {problem['when']}: {problem['detail']}")
        for block in p["blocked"]:
            out.append(f"- BLOCKED {block['job']} [{block['rule']}]: {block['message']}")
    return "\n".join(out) + "\n"


def render_html(facts: dict[str, Any], narrative: str, note: str, esc: Callable[[Any], str]) -> str:
    sections = []
    for p in facts["projects"]:
        sections.append(_project(p, esc))
    story = _markdown_to_html(narrative, esc) if narrative else ""
    note_html = f'<p class="note">{esc(note)}</p>' if note else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Growth digest {esc(facts['week']['to'])}</title><style>{CSS}</style></head>
<body><main>
<header class="mast"><h1>Growth digest</h1><p class="range">{esc(facts['week']['from'])} to {esc(facts['week']['to'])}</p></header>
{note_html}{f'<section class="story">{story}</section>' if story else ''}
{''.join(sections)}
<p class="foot">AI runs this week: <span class="num">{esc(facts['ai']['runs_7d'])}</span> of {esc(facts['ai']['budget_per_week'])} budgeted. Numbers in tables come straight from the data, never from the AI.</p>
</main></body></html>
"""


def _project(p: dict[str, Any], esc: Callable[[Any], str]) -> str:
    parts = [f'<section class="project"><h2>{esc(p["name"])}</h2>']
    goal = p.get("goal")
    if goal:
        rows = [("Confirmed", goal.get("confirmed_total", "n/a")), ("Target", goal["target"])]
        if goal.get("outside_zone"):
            rows.append(("Outside the zone", goal["outside_zone"]))
        for key, label in (
            ("plan_to_date", "Plan to date"),
            ("needed_per_day", "Needed per day"),
            ("avg_per_day_7", "Per day, last 7"),
            ("projection", "Projection at deadline"),
            ("k_factor", "Referral k-factor"),
            ("deadline", "Deadline"),
        ):
            if goal.get(key) is not None:
                rows.append((label, goal[key]))
        stat = "".join(f'<div><dt>{esc(label)}</dt><dd class="num">{esc(value)}</dd></div>' for label, value in rows)
        tone = "ok" if goal.get("on_track") else "warn"
        zone = f' in {esc(goal["zone"])}' if goal.get("zone") else ""
        source = f'<p class="status">Numbers from: {esc(goal["numbers_from"])}</p>' if goal.get("numbers_from") else ""
        parts.append(f'<h3>{esc(goal["name"]).capitalize()}{zone}</h3><p class="status {tone}">{esc(goal.get("status", ""))}</p>{source}<dl class="stats">{stat}</dl>')
        if goal.get("series"):
            body = "".join(
                f'<tr><td>{esc(r["date"])}</td><td class="num">{esc(r["confirmed"])}</td><td class="num">{esc(r["cumulative"])}</td><td class="num">{esc(r["plan"])}</td></tr>'
                for r in goal["series"]
            )
            parts.append(f'<table><tr><th>Day</th><th class="num">Confirmed</th><th class="num">Total</th><th class="num">Plan</th></tr>{body}</table>')
        if goal.get("sources"):
            body = "".join(f'<tr><td>{esc(s["source"])}</td><td class="num">{esc(s["confirmed"])}</td></tr>' for s in goal["sources"][:8])
            parts.append(f'<h3>Sign-ups by source</h3><table><tr><th>Source</th><th class="num">Confirmed</th></tr>{body}</table>')
    traffic = p["traffic"]
    if traffic.get("connected"):
        rows = "".join(f'<tr><td>{esc(r["path"])}</td><td class="num">{esc(r["views"])}</td></tr>' for r in traffic["top_pages"])
        funnel = traffic.get("funnel_7d", {})
        steps = "".join(f'<tr><td>{esc(name)}</td><td class="num">{esc(funnel.get(name, 0))}</td></tr>' for name in FUNNEL)
        parts.append(
            f'<h3>Traffic, 7 days</h3><p><span class="num">{esc(traffic["pageviews_7d"])}</span> pageviews.</p>'
            f'<table><tr><th>Page</th><th class="num">Views</th></tr>{rows}</table>'
            + (f'<h3>Sign-up funnel, 7 days</h3><table><tr><th>Step</th><th class="num">Events</th></tr>{steps}</table>' if funnel else "")
        )
    else:
        parts.append(f'<h3>Traffic</h3><p class="muted">Not connected: {esc(traffic.get("why"))}</p>')
    site = p["site"]
    parts.append(
        f'<h3>Site and queue</h3><p>{esc(site["pages"])} pages, {esc(site["indexable"])} indexable, last build {esc(site["last_build"])}, '
        f'{"public" if site["public"] else "not public yet"}. Reddit drafts waiting: <span class="num">{esc(p["reddit_drafts_waiting"])}</span>.</p>'
    )
    directories = p.get("directories")
    if directories:
        counts = ", ".join(f"{n} {s}" for s, n in directories["by_status"].items())
        skipped = "".join(
            f'<li><a href="{esc(e["url"])}">{esc(e["name"])}</a>: {esc(e["why"])}</li>' for e in directories["top_skipped"]
        )
        parts.append(
            f'<h3>Launch directories</h3><p><span class="num">{esc(directories["total"])}</span> in the catalogue: {esc(counts)}.</p>'
            + (f'<p class="muted">Skipped, because they need a person:</p><ul class="trouble">{skipped}</ul>' if skipped else "")
        )
    jobs = "".join(
        f'<tr><td>{esc(job)}</td><td>{esc(", ".join(f"{n} {s}" for s, n in counts.items()))}</td></tr>' for job, counts in p["jobs"].items()
    )
    if jobs:
        parts.append(f"<h3>What ran</h3><table><tr><th>Job</th><th>Runs</th></tr>{jobs}</table>")
    trouble = [f'<li><b>{esc(x["status"])}</b> {esc(x["job"])} at {esc(x["when"])}: {esc(x["detail"])}</li>' for x in p["problems"]]
    trouble += [f'<li><b>blocked</b> {esc(x["job"])} [{esc(x["rule"])}]: {esc(x["message"])}</li>' for x in p["blocked"]]
    parts.append("<h3>Needs attention</h3>" + (f'<ul class="trouble">{"".join(trouble)}</ul>' if trouble else '<p class="muted">Nothing failed or was blocked.</p>'))
    parts.append("</section>")
    return "".join(parts)



def _markdown_to_html(text: str, esc: Callable[[Any], str]) -> str:
    html, items = [], []

    def flush() -> None:
        if items:
            html.append("<ul>" + "".join(items) + "</ul>")
            items.clear()

    def inline(line: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(line))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            html.append(f"<h3>{inline(line.lstrip('#').strip())}</h3>")
        elif re.match(r"^([-*]|\d+\.)\s", line):
            bullet = re.sub(r"^([-*]|\d+\.)\s+", "", line)
            items.append(f"<li>{inline(bullet)}</li>")
        else:
            flush()
            html.append(f"<p>{inline(line)}</p>")
    flush()
    return "".join(html)


CSS = re.sub(
    r"\s+",
    " ",
    """
:root { --paper: #e8e9e8; --ink: #131313; --ink-2: #4a4b4b; --line: rgb(19 19 19 / .16); --line-2: rgb(19 19 19 / .34);
  --lime: #d4f53c; --lime-deep: #5f6f12; --warn: #a3341b; color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 300 16px/1.55 "Outfit", system-ui, -apple-system, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 48px 20px 72px; }
.mast h1 { margin: 0; font: 400 clamp(26px, 4vw, 40px)/1.05 "Michroma", system-ui, sans-serif; text-transform: uppercase; letter-spacing: -.01em; }
.mast h1::after { content: ""; display: block; width: 18px; height: 3px; margin-top: 14px; background: var(--lime-deep); }
.range, .num, td.num, dd.num { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.range { margin: 12px 0 0; font-size: 13px; color: var(--ink-2); }
.note { margin: 24px 0 0; padding: 12px 0; border-top: 1px solid var(--line-2); color: var(--ink-2); font-size: 14px; }
.story { margin-top: 28px; font-size: 17px; }
.story p, .story ul { max-width: 66ch; }
.project { margin-top: 44px; padding-top: 22px; border-top: 1px solid var(--line-2); }
h2 { margin: 0; font: 500 26px/1.2 "Outfit", system-ui, sans-serif; }
h3 { margin: 26px 0 8px; font: 500 17px/1.3 "Outfit", system-ui, sans-serif; }
.status { margin: 0; font-weight: 500; }
.status.ok { color: var(--lime-deep); }
.status.warn { color: var(--warn); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0 24px; margin: 14px 0 0; }
.stats div { padding: 10px 0; border-bottom: 1px solid var(--line); }
.stats dt { font: 400 11.5px/1.4 "IBM Plex Mono", ui-monospace, monospace; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-2); }
.stats dd { margin: 4px 0 0; font-size: 22px; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
th { text-align: left; font: 400 11.5px/1.5 "IBM Plex Mono", ui-monospace, monospace; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-2); padding: 6px 12px 6px 0; border-bottom: 1px solid var(--line-2); }
td { padding: 6px 12px 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
td.num { text-align: right; }
th.num { text-align: right; }
.trouble { padding-left: 18px; }
.trouble li { margin: 6px 0; }
.muted { color: var(--ink-2); }
.foot { margin-top: 48px; font-size: 13px; color: var(--ink-2); }
::selection { background: var(--lime); }
""",
).strip()
