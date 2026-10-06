"""Everything the control app can change, as small validated operations.

Settings changes land in `state/engine/control.json`, a layer over engine.toml and project.toml.
Each change is checked by loading the whole configuration with it (the same validation the scheduler
uses), refused with the loader's messages when it does not pass, and recorded in the audit log with
who made it. Nothing here posts, sends or publishes: approving a Reddit draft only marks it for the
owner to post by hand, and the launch switch only allows the next deploy to reach production.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .. import guard
from ..config import ENGINE_SCOPE, ConfigError, Engine, load_engine, read_control, resolve_state_dir
from ..util import write_json

DRAFT_STATUSES = ("open", "approved", "rejected", "posted")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DRAFT_ID = re.compile(r"^[a-f0-9]{6,64}$")


class ControlError(Exception):
    """A refused control action; the message is safe to show in the app."""


@dataclass(frozen=True)
class Home:
    """Where the engine's config and state live, as the server was started with."""

    root: Path
    state_dir: Path | None = None
    dist_dir: Path | None = None

    def engine(self) -> Engine:
        return load_engine(self.root, state_dir=self.state_dir, dist_dir=self.dist_dir)

    def state(self) -> Path:
        return self.state_dir or resolve_state_dir(self.root)


@contextmanager
def _control_file(state_dir: Path) -> Iterator[tuple[dict[str, Any], Path]]:
    folder = state_dir / "engine"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "control.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield read_control(state_dir), folder / "control.json"


def _set(data: dict[str, Any], path: list[str], value: Any) -> None:
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    if value is None:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value


def change(home: Home, actor: str, what: str, changes: list[tuple[list[str], Any]], *, scope: str = "engine") -> Engine:
    """Apply `changes` (path in control.json -> value; None removes) if the result still validates."""
    engine = home.engine()
    with _control_file(engine.state_dir) as (data, path):
        before = json.loads(json.dumps(data))
        for keys, value in changes:
            _set(data, keys, value)
        write_json(path, data)
        try:
            engine = home.engine()
        except ConfigError as exc:
            write_json(path, before)
            raise ControlError("; ".join(exc.errors)) from None
    guard.audit(engine.state_dir, actor, f"control.{what}", scope=scope, changes={".".join(k): v for k, v in changes})
    return engine


def _project(engine: Engine, pid: str) -> Any:
    if not _ID.match(pid) or pid not in engine.projects:
        raise ControlError(f"no enabled project {pid!r}")
    return engine.projects[pid]


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ControlError(f"{name} must be true or false")
    return value


def _int(value: Any, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ControlError(f"{name} must be a whole number from {low} to {high}")
    return value


# ---- jobs, projects, channels ---------------------------------------------------------------------


def set_job(home: Home, actor: str, scope: str, job: str, enabled: Any) -> None:
    engine = home.engine()
    jobs = engine.jobs if scope == "engine" else _project(engine, scope).jobs
    if job not in jobs:
        raise ControlError(f"{scope} has no job {job!r}")
    path = ["engine", "jobs", job, "enabled"] if scope == "engine" else ["projects", scope, "jobs", job, "enabled"]
    change(home, actor, "job", [(path, _bool(enabled, "enabled"))], scope=scope)


def set_paused(home: Home, actor: str, pid: str, paused: Any) -> None:
    _project(home.engine(), pid)
    change(home, actor, "pause" if paused else "resume", [(["projects", pid, "paused"], _bool(paused, "paused") or None)], scope=pid)


def set_channel(home: Home, actor: str, pid: str, channel: str, *, paused: Any = None, rate_limit: Any = None) -> None:
    project = _project(home.engine(), pid)
    if channel not in project.channels:
        raise ControlError(f"{pid} has no channel {channel!r} configured")
    changes: list[tuple[list[str], Any]] = []
    if paused is not None:
        changes.append((["projects", pid, "channels", channel, "paused"], _bool(paused, "paused") or None))
    if rate_limit is not None:
        if rate_limit == "":
            changes.append((["projects", pid, "guard", "rate_limits", channel], None))
        else:
            try:
                guard.parse_rate(str(rate_limit))
            except ValueError as exc:
                raise ControlError(str(exc)) from None
            changes.append((["projects", pid, "guard", "rate_limits", channel], str(rate_limit)))
    if not changes:
        raise ControlError("nothing to change")
    change(home, actor, "channel", changes, scope=pid)


# ---- targets, budgets, settings -------------------------------------------------------------------


def set_goal(home: Home, actor: str, pid: str, values: dict[str, Any]) -> None:
    _project(home.engine(), pid)
    changes: list[tuple[list[str], Any]] = []
    if "target" in values:
        changes.append((["projects", pid, "goal", "target"], _int(values["target"], "target", 1, 10_000_000)))
    if "days" in values:
        changes.append((["projects", pid, "goal", "days"], _int(values["days"], "days", 1, 3650)))
    if "start" in values:
        start = str(values["start"] or "")
        if start:
            try:
                date.fromisoformat(start)
            except ValueError:
                raise ControlError("start must be a date (YYYY-MM-DD) or empty") from None
        changes.append((["projects", pid, "goal", "start"], start))
    if "zone" in values:
        zone = values["zone"]
        if not isinstance(zone, list) or not all(isinstance(c, str) and re.fullmatch(r"[A-Za-z]{2}", c) for c in zone):
            raise ControlError("countries must be two-letter codes, e.g. DE, AT, CH")
        changes.append((["projects", pid, "goal", "zone"], [c.upper() for c in zone]))
    if not changes:
        raise ControlError("nothing to change")
    change(home, actor, "goal", changes, scope=pid)


def set_budget(home: Home, actor: str, values: dict[str, Any]) -> None:
    changes: list[tuple[list[str], Any]] = []
    for key, high in (("max_runs_per_day", 24), ("max_runs_per_week", 100), ("per_project_runs_per_week", 100)):
        if key in values:
            changes.append((["engine", "ai", key], _int(values[key], key, 0, high)))
    for channel, limit in dict(values.get("rate_limits", {})).items():
        if limit:
            try:
                guard.parse_rate(str(limit))
            except ValueError as exc:
                raise ControlError(str(exc)) from None
        changes.append((["engine", "guard", "rate_limits", str(channel)], str(limit) or None))
    if not changes:
        raise ControlError("nothing to change")
    change(home, actor, "budget", changes)


SETTINGS = {
    "brand_name": (["brand", "name"], str),
    "base_url": (["site", "base_url"], str),
    "domain_decided": (["site", "domain_decided"], bool),
    "indexable": (["site", "indexable"], bool),
    "launched": (["deploy", "launched"], bool),
}


def set_settings(home: Home, actor: str, pid: str, values: dict[str, Any]) -> None:
    """Brand, domain, indexing, keywords and the launch switch. Each passes the full project validation."""
    _project(home.engine(), pid)
    changes: list[tuple[list[str], Any]] = []
    for key, value in values.items():
        if key == "keywords":
            if not isinstance(value, list) or not all(isinstance(c, dict) and c.get("page") and c.get("primary") for c in value):
                raise ControlError("keywords must be a list of {page, primary} clusters")
            clusters = [{k: v for k, v in c.items() if k in ("page", "primary", "lang", "volume_de")} for c in value]
            changes.append((["projects", pid, "keywords"], clusters))
            continue
        if key not in SETTINGS:
            raise ControlError(f"{key!r} cannot be changed from the app (allowed: {', '.join([*SETTINGS, 'keywords'])})")
        path, kind = SETTINGS[key]
        if not isinstance(value, kind):
            raise ControlError(f"{key} must be {'true or false' if kind is bool else 'text'}")
        changes.append((["projects", pid, *path], value))
    if not changes:
        raise ControlError("nothing to change")
    what = "launch" if values.get("launched") is True else "settings"
    change(home, actor, what, changes, scope=pid)


# ---- the human queue ------------------------------------------------------------------------------


def decide_draft(home: Home, actor: str, pid: str, draft_id: str, status: str) -> dict[str, Any]:
    """Approve, reject or mark a Reddit draft as posted. The engine never posts it."""
    engine = home.engine()
    _project(engine, pid)
    if status not in DRAFT_STATUSES or not _DRAFT_ID.match(draft_id):
        raise ControlError(f"status must be one of {', '.join(DRAFT_STATUSES)}")
    matches = list((engine.state_dir / "projects" / pid / "queue").glob(f"*/{draft_id}.json"))
    if not matches:
        raise ControlError(f"no draft {draft_id!r}")
    data = json.loads(matches[0].read_text(encoding="utf-8"))
    old = data.get("status", "open")
    data["status"] = status
    write_json(matches[0], data)
    guard.audit(engine.state_dir, actor, "control.draft", scope=pid, draft=draft_id, community=data.get("community"), status=status, was=old)
    return data


# ---- run now, kill switch -------------------------------------------------------------------------


def run_now(home: Home, actor: str, scope: str, job: str) -> int:
    """Start one job now in its own process (under the engine lock and ledger, like `growth run`)."""
    engine = home.engine()
    guard.check_running(engine.state_dir)
    jobs = engine.jobs if scope == "engine" else _project(engine, scope).jobs
    if job not in jobs or not jobs[job].enabled:
        raise ControlError(f"{scope} has no enabled job {job!r}")
    log = engine.state_dir / "engine" / "manual-runs.log"
    env = dict(os.environ)
    if home.state_dir:
        env["GROWTH_STATE_DIR"] = str(home.state_dir)
    with open(log, "a") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "growth", "--home", str(home.root), "run", scope, job],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            start_new_session=True,
        )
    guard.audit(engine.state_dir, actor, "control.run-now", scope=scope, job=job, pid=proc.pid)
    return proc.pid


def set_kill(home: Home, actor: str, on: Any, reason: str) -> dict[str, Any] | None:
    # Works even when the configuration does not load: stopping must always be possible.
    return guard.set_kill(home.state(), _bool(on, "on"), reason=str(reason or "")[:300], actor=actor)


# ---- editing a project: schedule, website text, legal notice, connections, new projects -----------

def set_schedule(home: Home, actor: str, scope: str, job: str, schedule: Any) -> None:
    """Change when a job runs ("every 6h", "daily 04:00", "weekly sun 03:00")."""
    from ..schedule import parse_schedule

    engine = home.engine()
    jobs = engine.jobs if scope == "engine" else _project(engine, scope).jobs
    if job not in jobs:
        raise ControlError(f"{scope} has no job {job!r}")
    try:
        parse_schedule(str(schedule))
    except ValueError as exc:
        raise ControlError(str(exc)) from None
    path = ["engine", "jobs", job, "schedule"] if scope == "engine" else ["projects", scope, "jobs", job, "schedule"]
    change(home, actor, "schedule", [(path, str(schedule))], scope=scope)


PAGE_TEXT_KEYS = ("title", "description", "headline", "sub")


def set_page_text(home: Home, actor: str, pid: str, page: str, lang: str, values: dict[str, Any]) -> None:
    """Edit a page's title, description, main heading and subline in one language."""
    from ..site.spec import load_pages

    project = _project(home.engine(), pid)
    pages = load_pages(project)
    if page not in pages or lang not in pages[page].langs:
        raise ControlError(f"{pid} has no page {page!r} in {lang!r}")
    changes = []
    for key, value in values.items():
        if key not in PAGE_TEXT_KEYS:
            raise ControlError(f"{key!r} cannot be edited here (allowed: {', '.join(PAGE_TEXT_KEYS)})")
        if not isinstance(value, str) or len(value) > 2000:
            raise ControlError(f"{key} must be text of at most 2000 characters")
        changes.append((["projects", pid, "pages", page, lang, key], value))
    if not changes:
        raise ControlError("nothing to change")
    change(home, actor, "page-text", changes, scope=pid)


LEGAL_KEYS = ("name", "street", "postcode_city", "country", "email")


def set_legal(home: Home, actor: str, pid: str, values: dict[str, Any]) -> None:
    _project(home.engine(), pid)
    changes = []
    for key, value in values.items():
        if key not in LEGAL_KEYS or not isinstance(value, str) or len(value) > 300:
            raise ControlError(f"legal notice fields are {', '.join(LEGAL_KEYS)}, as text")
        changes.append((["projects", pid, "legal", key], value.strip()))
    if not changes:
        raise ControlError("nothing to change")
    change(home, actor, "legal", changes, scope=pid)


def set_secret(home: Home, actor: str, pid: str, name: str, value: Any) -> None:
    """Store a connection secret (encrypted). Only the names the project uses; the value is never echoed or logged."""
    from .. import secrets
    from .plain import settable_secrets

    project = _project(home.engine(), pid)
    if name not in settable_secrets(project):
        raise ControlError(f"{name!r} is not a secret this project uses")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(c.isspace() for c in value.strip()):
        raise ControlError("paste the value as one line")
    try:
        secrets.put(name, value.strip())
    except secrets.SecretsError as exc:
        raise ControlError(str(exc)) from None
    guard.audit(home.state(), actor, "control.secret", scope=pid, name=name)


TEMPLATE = Path(__file__).resolve().parents[2] / "examples" / "home" / "projects" / "example"


def add_project(home: Home, actor: str, pid: Any, name: Any, base_url: Any) -> None:
    """Start a project from the example template, renamed; everything that acts outward starts switched off."""
    import shutil

    from ..util import read_toml

    if not isinstance(pid, str) or not _ID.match(pid) or len(pid) > 40:
        raise ControlError("the project id is lower-case letters, digits and dashes, e.g. my-product")
    if pid == ENGINE_SCOPE:
        raise ControlError(f"{ENGINE_SCOPE!r} is reserved for the engine's own jobs")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ControlError("give the product a name")
    if not isinstance(base_url, str) or not re.fullmatch(r"https://[a-z0-9.-]+", base_url):
        raise ControlError("the web address looks like https://my-product.com (no path)")
    engine = home.engine()
    folder = engine.root / "projects" / pid
    if folder.exists():
        raise ControlError(f"a project folder {pid!r} already exists")
    shutil.copytree(TEMPLATE, folder)
    text = (folder / "project.toml").read_text(encoding="utf-8")
    raw = read_toml(folder / "project.toml")
    replacements = {
        f'id = "{raw["id"]}"': f'id = "{pid}"',
        f'name = "{raw["name"]}"': f'name = {json.dumps(name.strip())}',
        f'base_url = "{raw["site"]["base_url"]}"': f'base_url = "{base_url}"',
        f'out = "{raw["site"]["out"]}"': f'out = "{pid}"',
        f'worker_name = "{raw["waitlist"]["worker_name"]}"': f'worker_name = "{pid}-site"',
        f'database_name = "{raw["waitlist"]["database_name"]}"': f'database_name = "{pid}-waitlist"',
        f'stats_token_env = "{raw["waitlist"]["stats_token_env"]}"': f'stats_token_env = "{pid.upper().replace("-", "_")}_STATS_TOKEN"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    # The template's operator is fictional; the owner fills in their own before the site is built.
    for key, value in raw.get("legal", {}).items():
        text = text.replace(f"{key} = {json.dumps(value)}", f'{key} = ""', 1)
    text = text.replace(f'[brand]\nname = "{raw["brand"]["name"]}"', f"[brand]\nname = {json.dumps(name.strip())}", 1)
    # A new project starts quiet: no submissions and no publishing until the owner turns them on.
    text = text.replace('[channels.directory-submit]\nenabled = true', '[channels.directory-submit]\nenabled = false', 1)
    text = re.sub(r'(kind = "directories-[a-z]+"\n)', r'\1enabled = false\n', text)
    (folder / "project.toml").write_text(text, encoding="utf-8")
    try:
        home.engine()
    except ConfigError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise ControlError("; ".join(exc.errors)) from None
    guard.audit(home.state(), actor, "control.add-project", scope=pid, name=name.strip(), base_url=base_url)


def build_now(home: Home, actor: str, pid: str) -> int:
    """Refresh the data and rebuild the website now, in one process (`growth build`), under the engine lock."""
    engine = home.engine()
    guard.check_running(engine.state_dir)
    _project(engine, pid)
    log = engine.state_dir / "engine" / "manual-runs.log"
    env = dict(os.environ)
    if home.state_dir:
        env["GROWTH_STATE_DIR"] = str(home.state_dir)
    with open(log, "a") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "growth", "--home", str(home.root), "build", pid],
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2], env=env, start_new_session=True,
        )
    guard.audit(engine.state_dir, actor, "control.build-now", scope=pid, pid=proc.pid)
    return proc.pid
