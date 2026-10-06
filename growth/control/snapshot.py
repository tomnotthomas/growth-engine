"""What the control app shows: one JSON snapshot of the goal, the jobs, the queue and the engine's health.

The snapshot only reads. Sign-up numbers come from a cache (`state/projects/<id>/signup-stats.json`)
that the server refreshes in the background at most every ten minutes, so opening the app never
waits on the waitlist Worker or PostHog.
"""

from __future__ import annotations

import socket
import subprocess
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import __version__, analytics, guard
from ..channels import CHANNELS
from ..config import ENGINE_SCOPE, Engine, Project
from ..goal import goal_report, in_zone
from ..jobs import JOB_KINDS
from ..queue import Draft
from ..site.spec import load_keywords
from ..store import FAILED, INTERRUPTED, MISSED, Store
from ..util import iso, parse_iso, read_json, utcnow, write_json

REPO = Path(__file__).resolve().parents[2]
STATS_MAX_AGE = timedelta(minutes=10)
TICK_STALE = timedelta(minutes=15)
_refreshing: set[str] = set()
_lock = threading.Lock()


def engine_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---- sign-up numbers ------------------------------------------------------------------------------


def _stats_path(engine: Engine, pid: str) -> Path:
    return engine.state_dir / "projects" / pid / "signup-stats.json"


def cached_stats(engine: Engine, project: Project) -> dict[str, Any]:
    return read_json(_stats_path(engine, project.id), {}) or {}


def refresh_stats(engine: Engine, project: Project, now: datetime) -> None:
    stats, why = analytics.signup_stats(project)
    write_json(_stats_path(engine, project.id), {"fetched_at": iso(now), "stats": stats, "why": why})


def refresh_stale_stats(engine: Engine, now: datetime) -> None:
    """Start a background refresh for every project whose cached numbers are older than ten minutes."""
    for project in engine.projects.values():
        cache = cached_stats(engine, project)
        if cache.get("demo") or (cache.get("fetched_at") and now - parse_iso(cache["fetched_at"]) < STATS_MAX_AGE):
            continue
        with _lock:
            if project.id in _refreshing:
                continue
            _refreshing.add(project.id)

        def work(p: Project = project) -> None:
            try:
                refresh_stats(engine, p, now)
            finally:
                with _lock:
                    _refreshing.discard(p.id)

        threading.Thread(target=work, daemon=True).start()


def _goal(project: Project, cache: dict[str, Any], today: date) -> dict[str, Any] | None:
    conf = project.raw.get("goal")
    if not conf:
        return None
    stats = cache.get("stats")
    report = goal_report(stats, conf, today)
    report["fetched_at"] = cache.get("fetched_at", "")
    report["days"] = int(conf.get("days", 0)) or None
    report["start"] = str(conf.get("start", "") or "") or None
    if stats is None:
        report["status"] = cache.get("why") or report["status"]
        report.update(daily=[], channels=[], countries=[])
        return report
    zoned, _ = in_zone(stats, [str(c) for c in conf.get("zone", [])])
    per_day: Counter[str] = Counter()
    for row in zoned.get("days", []):
        per_day[row["date"]] += int(row.get("confirmed", 0))
    countries: Counter[str] = Counter()
    for row in stats.get("days", []):
        countries[str(row.get("country") or "unknown").upper()] += int(row.get("confirmed", 0))
    start = date.fromisoformat(conf["start"]) if conf.get("start") else None
    total_days = int(conf.get("days", 0))
    target = int(conf.get("target", 0))
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    running = int(report.get("confirmed_total", 0)) - sum(n for d, n in per_day.items() if d >= days[0].isoformat())
    daily = []
    for day in days:
        running += per_day.get(day.isoformat(), 0)
        plan = None
        if start and total_days:
            done = max(0, min(total_days, (day - start).days + 1))
            plan = round(target * done / total_days)
        daily.append({"date": day.isoformat(), "confirmed": per_day.get(day.isoformat(), 0), "cumulative": running, "plan": plan})
    zone = {c.upper() for c in conf.get("zone", [])}
    report["daily"] = daily
    report["channels"] = report.pop("sources", [])
    report["countries"] = [
        {"country": c, "confirmed": n, "in_zone": not zone or c in zone} for c, n in countries.most_common()
    ]
    report.pop("series", None)
    return report


# ---- jobs and the schedule ------------------------------------------------------------------------


def _next_slot(job: Any, now: datetime, tz: Any) -> str | None:
    found = job.schedule.slots(now, now + timedelta(days=8), tz)
    return iso(found[0]) if found else None


def _jobs(engine: Engine, project: Project | None, store: Store, now: datetime) -> list[dict[str, Any]]:
    scope = project.id if project else ENGINE_SCOPE
    tz = project.tz if project else engine.tz
    runs = store.runs_since(now - timedelta(days=14), scope)
    out = []
    for job in (project.jobs if project else engine.jobs).values():
        kind = JOB_KINDS[job.kind]
        mine = [r for r in runs if r.job == job.id and r.status != MISSED]
        last = mine[-1] if mine else None
        paused = bool(project and (project.paused or (kind.channel and project.channels.get(kind.channel, {}).get("paused"))))
        out.append(
            {
                "id": job.id,
                "kind": job.kind,
                "description": kind.description,
                "schedule": job.schedule.text,
                "enabled": job.enabled,
                "paused": paused,
                "channel": kind.channel,
                "uses_ai": kind.ai,
                "last": {"status": last.status, "at": last.finished_at or last.started_at, "summary": last.summary[:400]} if last else None,
                "next_at": _next_slot(job, now, tz) if job.enabled and not paused else None,
            }
        )
    return out


def _channels(engine: Engine, project: Project, store: Store, now: datetime) -> list[dict[str, Any]]:
    effects = store.effects_since(now - timedelta(days=1), project.id)
    limits = {**engine.guard.get("rate_limits", {}), **project.raw.get("guard", {}).get("rate_limits", {})}
    out = []
    for cid, conf in project.channels.items():
        channel = CHANNELS[cid]
        out.append(
            {
                "id": cid,
                "label": channel.label,
                "level": channel.level,
                "why": channel.why,
                "enabled": bool(conf.get("enabled")),
                "paused": bool(conf.get("paused")),
                "rate_limit": limits.get(cid, ""),
                "used_last_day": sum(1 for e in effects if e["channel"] == cid and e["status"] != "failed"),
            }
        )
    return out


def _drafts(engine: Engine, project: Project) -> list[dict[str, Any]]:
    import json

    root = engine.state_dir / "projects" / project.id / "queue"
    out = []
    for file in sorted(root.glob("*/*.json")) if root.is_dir() else []:
        data = json.loads(file.read_text(encoding="utf-8"))
        draft = Draft(**data)
        if draft.status in ("open", "approved"):
            out.append(draft.__dict__)
    return sorted(out, key=lambda d: d["created_at"], reverse=True)


# ---- the snapshot ---------------------------------------------------------------------------------


def snapshot(engine: Engine, *, now: datetime | None = None, demo: bool = False) -> dict[str, Any]:
    now = now or utcnow()
    store = Store(engine.state_dir / "engine.db")
    try:
        last_tick = store.get("last_tick")
        kill = guard.kill_state(engine.state_dir)
        tick_age = (now - parse_iso(last_tick)).total_seconds() if last_tick else None
        audit_ok, audit_entries, audit_problem = guard.verify_audit(engine.state_dir)
        updates = (read_json(engine.state_dir / "engine" / "update.json", {}) or {}).get("history", [])[-8:]
        projects = []
        attention: list[dict[str, Any]] = []
        for project in engine.projects.values():
            today = now.astimezone(project.tz).date()
            drafts = _drafts(engine, project)
            jobs = _jobs(engine, project, store, now)
            problems = [
                {"job": r.job, "status": r.status, "at": r.finished_at or r.slot, "summary": r.summary[:400]}
                for r in store.runs_since(now - timedelta(days=2), project.id)
                if r.status in (FAILED, INTERRUPTED)
            ]
            blocks = store.blocks_since(now - timedelta(days=7), project.id)
            for d in drafts:
                if d["status"] == "open":
                    attention.append({"kind": "draft", "project": project.id, "id": d["id"], "title": d["title"], "detail": d["community"], "at": d["created_at"]})
            for p in problems[-5:]:
                attention.append({"kind": "failure", "project": project.id, "title": f"{p['job']} {p['status']}", "detail": p["summary"], "at": p["at"]})
            for b in blocks[-5:]:
                attention.append({"kind": "block", "project": project.id, "title": f"Blocked by {b['rule']}", "detail": b["message"], "at": b["at"]})
            projects.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "brand_name": str(project.brand.get("name", project.name)),
                    "base_url": project.site.get("base_url", ""),
                    "domain_decided": bool(project.site.get("domain_decided")),
                    "indexable": bool(project.site.get("indexable")),
                    "launched": project.launched,
                    "paused": project.paused,
                    "languages": project.languages,
                    "goal": _goal(project, cached_stats(engine, project), today),
                    "jobs": jobs,
                    "channels": _channels(engine, project, store, now),
                    "drafts": drafts,
                    "problems": problems[-10:],
                    "blocks": blocks[-10:],
                    "deploy": read_json(engine.state_dir / "projects" / project.id / "deploy.json", {}) or {},
                    "keywords": load_keywords(project),
                }
            )
        upcoming = []
        for p in projects:
            upcoming += [{"at": j["next_at"], "scope": p["id"], "job": j["id"], "kind": j["kind"]} for j in p["jobs"] if j["next_at"]]
        engine_jobs = _jobs(engine, None, store, now)
        upcoming += [{"at": j["next_at"], "scope": ENGINE_SCOPE, "job": j["id"], "kind": j["kind"]} for j in engine_jobs if j["next_at"]]
        activity = [
            {"at": r.finished_at or r.started_at or r.slot, "scope": r.scope, "job": r.job, "status": r.status, "summary": r.summary[:300]}
            for r in store.runs_since(now - timedelta(days=3))
        ][-60:]
        since_day, since_week = now - timedelta(days=1), now - timedelta(days=7)
        return {
            "generated_at": iso(now),
            "demo": demo,
            "engine": {
                "host": socket.gethostname(),
                "version": __version__,
                "commit": engine_commit(),
                "last_tick": last_tick,
                "tick_age_seconds": tick_age,
                "healthy": bool(last_tick and tick_age is not None and tick_age < TICK_STALE.total_seconds() and not kill),
                "kill": kill,
                "ai": {
                    "today": store.ai_runs_since(since_day),
                    "max_per_day": engine.ai.max_runs_per_day,
                    "week": store.ai_runs_since(since_week),
                    "max_per_week": engine.ai.max_runs_per_week,
                    "per_project_per_week": engine.ai.per_project_runs_per_week,
                    "window": [engine.ai.window[0].strftime("%H:%M"), engine.ai.window[1].strftime("%H:%M")] if engine.ai.window else None,
                },
                "rate_limits": engine.guard.get("rate_limits", {}),
                "jobs": engine_jobs,
                "updates": updates,
                "audit": {"intact": audit_ok, "entries": audit_entries, "problem": audit_problem},
            },
            "attention": sorted(attention, key=lambda a: a.get("at") or "", reverse=True),
            "projects": projects,
            "upcoming": sorted(upcoming, key=lambda u: u["at"])[:16],
            "activity": list(reversed(activity)),
        }
    finally:
        store.close()
