"""digest: the weekly report, one section per project, written by headless Claude from facts only.

The engine collects the facts (runs, failures, blocks, AI usage, site state, traffic, sign-ups
against the goal, drafts waiting). Claude turns them into a short narrative with three changes to
make. Every number Claude writes must appear in the facts; otherwise its text is thrown away and
the digest goes out without a narrative. The fact tables are always rendered by the engine.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import timedelta
from typing import Any

from .. import analytics, net
from ..ai import AIFailed, AIUnavailable
from ..goal import goal_report
from ..policy import Action
from ..directories import load_catalogue, summary
from ..queue import open_drafts
from ..site.html import esc
from ..store import FAILED, INTERRUPTED, MISSED
from ..util import read_json, write_atomic
from .digest_render import render_html, render_markdown

SYSTEM = (
    "You write a weekly operations digest for a solo founder who does not want to be involved in "
    "day-to-day marketing. Use only the facts given. Never invent or estimate a number; if a number "
    "is not in the facts, say it is not available. Be short and concrete."
)
PROMPT = """Write the narrative part of this week's growth digest in {language}, as Markdown.

For each project: one short paragraph on what ran and what failed or was blocked, one on traffic and
sign-ups against the goal (say plainly if the goal is behind plan), then exactly three recommended
changes, each tied to a number from the facts. Mention Reddit drafts waiting, since posting them is the
owner's only manual task, and launch directories that were skipped because they need a person. No tables (the engine adds them), no greeting, no sign-off, at most 350 words.

FACTS (JSON):
{facts}
"""


def collect_facts(ctx: Any) -> dict[str, Any]:
    engine = ctx.engine
    since = ctx.now - timedelta(days=7)
    facts: dict[str, Any] = {
        "week": {"from": since.date().isoformat(), "to": ctx.now.date().isoformat()},
        "ai": {"runs_7d": ctx.store.ai_runs_since(since), "budget_per_week": engine.ai.max_runs_per_week},
        "projects": [],
    }
    for project in engine.projects.values():
        runs = ctx.store.runs_since(since, project.id)
        per_job: dict[str, Counter[str]] = {}
        problems = []
        for run in runs:
            per_job.setdefault(run.job, Counter())[run.status] += 1
            if run.status in (FAILED, INTERRUPTED, MISSED):
                problems.append({"job": run.job, "status": run.status, "when": run.slot, "detail": run.summary[:300]})
        registry = read_json(engine.state_dir / "projects" / project.id / "site-registry.json", {}) or {}
        pages = registry.get("pages", {})
        today = ctx.now.astimezone(project.tz).date()
        stats, why = analytics.signup_stats(project)
        goal = goal_report(stats, project.raw.get("goal", {}), today) if project.raw.get("goal") else None
        if goal is not None and stats is None:
            goal["status"] = why
        facts["projects"].append(
            {
                "id": project.id,
                "name": project.name,
                "jobs": {job: {s: n for s, n in sorted(c.items())} for job, c in sorted(per_job.items())},
                "problems": problems[-12:],
                "blocked": ctx.store.blocks_since(since, project.id),
                "site": {
                    "pages": len(pages),
                    "indexable": sum(1 for p in pages.values() if p.get("indexable")),
                    "last_build": registry.get("built_at", "never"),
                    "public": bool(project.site.get("indexable")),
                },
                "traffic": analytics.traffic(project),
                "goal": goal,
                "reddit_drafts_waiting": len(open_drafts(engine.state_dir, project.id)),
                "directories": _directories(engine.state_dir / "projects" / project.id),
            }
        )
    return facts


def _directories(state_dir: Any) -> dict[str, Any] | None:
    entries = load_catalogue(state_dir)
    return summary(entries) if entries else None


SCALES = {
    "k": 1e3,
    "thousand": 1e3,
    "tsd": 1e3,
    "tausend": 1e3,
    "m": 1e6,
    "million": 1e6,
    "millionen": 1e6,
    "mio": 1e6,
    "bn": 1e9,
    "billion": 1e9,
    "mrd": 1e9,
    "milliarden": 1e9,
}
_NUMBER_IN_TEXT = re.compile(
    r"(?<![\w.])(\d[\d.,]*)(?:\s*(%|(?:" + "|".join(sorted(SCALES, key=len, reverse=True)) + r")\b\.?))?", re.I
)


def _canon(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def invented_numbers(text: str, facts: dict[str, Any]) -> list[str]:
    """Numbers in `text` that the facts do not contain (small counts and years without a unit are allowed)."""
    known = set()
    for match in re.finditer(r"\d+(?:\.\d+)?", json.dumps(facts)):
        value = float(match.group(0))
        known.add(_canon(value))
        if "." in match.group(0):
            known.add(_canon(round(value * 100, 1)))  # shares written as percent
    bad = []
    for match in _NUMBER_IN_TEXT.finditer(text):
        raw = match.group(1).rstrip(".,")
        unit = (match.group(2) or "").lower().rstrip(".")
        norm = raw.replace(",", "").replace(".", "") if re.fullmatch(r"[1-9]\d{0,2}([.,]\d{3})+", raw) else raw.replace(",", ".")
        try:
            number = float(norm)
        except ValueError:
            continue
        if _canon(number * SCALES.get(unit, 1)) in known:
            continue
        if not unit and (number <= 12 or 2000 <= number <= 2100):
            continue
        bad.append(match.group(0).strip())
    return bad


def run(ctx: Any) -> str:
    facts = collect_facts(ctx)
    conf = ctx.engine.digest
    language = conf.get("language", "English")
    narrative, note = "", ""
    try:
        result = ctx.ai().run(PROMPT.format(language=language, facts=json.dumps(facts, ensure_ascii=False, indent=1)), system=SYSTEM)
        bad = invented_numbers(result.text, facts)
        if bad:
            note = f"AI narrative discarded: it contained numbers not in the facts ({', '.join(bad[:5])})"
        else:
            narrative = result.text.strip()
    except (AIUnavailable, AIFailed) as exc:
        note = f"written without AI: {exc}"

    stamp = ctx.now.astimezone(ctx.engine.tz).date().isoformat()
    folder = ctx.state_dir / "digests"
    markdown = render_markdown(facts, narrative, note)
    html = render_html(facts, narrative, note, esc)
    write_atomic(folder / f"{stamp}.md", markdown)
    write_atomic(folder / f"{stamp}.html", html)
    write_atomic(folder / "latest.html", html)

    url = os.environ.get(str(conf.get("webhook_env", "")), "")
    if url:
        action = Action(channel="digest", kind="notify", url=url)
        ctx.act(action, f"digest:{stamp}", lambda: _post(url, markdown))
    return f"digest {stamp} written" + (f" ({note})" if note else "")


def _post(url: str, text: str) -> str:
    status, _ = net.request("POST", url, body=text.encode("utf-8"), headers={"Content-Type": "text/markdown; charset=utf-8"}, timeout=30)
    if status >= 300:
        raise net.HttpError(f"digest webhook answered {status}")
    return f"posted ({status})"

