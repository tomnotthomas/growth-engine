"""Load and validate engine.toml and every projects/<id>/project.toml.

The engine holds no project knowledge: brand, URLs, languages, pages, data sources, channels and
rules all come from the project folder. Loading fails with every problem listed at once, so a bad
config never half-runs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import channels as ch
from .policy import PolicyViolation, ProjectRules, check_url
from .schedule import Schedule, parse_schedule
from .util import parse_duration, read_toml

ENGINE_SCOPE = "engine"
INDEXNOW_KEY = re.compile(r"[A-Za-z0-9-]{8,128}")
CATCHUP = {"latest", "all", "skip"}
FORBIDDEN_ARGS = ("--bare", "--max-budget-usd")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WINDOW = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("invalid configuration:\n  - " + "\n  - ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class JobSpec:
    id: str
    kind: str
    schedule: Schedule
    enabled: bool = True
    catchup: str = "latest"
    max_late: timedelta = timedelta(hours=20)
    max_attempts: int = 3
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIConfig:
    command: list[str]
    extra_args: list[str]
    max_runs_per_day: int
    max_runs_per_week: int
    per_project_runs_per_week: int | None
    window: tuple[time, time] | None
    timeout: timedelta
    cooldown: timedelta


@dataclass
class Project:
    id: str
    root: Path
    raw: dict[str, Any]
    name: str
    tz: ZoneInfo
    languages: list[str]
    default_language: str
    brand: dict[str, Any]
    site: dict[str, Any]
    rules: ProjectRules
    channels: dict[str, dict[str, Any]]
    data: dict[str, dict[str, Any]]
    collections: dict[str, dict[str, Any]]
    jobs: dict[str, JobSpec]
    analytics: dict[str, Any]

    @property
    def scope(self) -> str:
        return self.id

    def channel_enabled(self, channel_id: str) -> bool:
        return bool(self.channels.get(channel_id, {}).get("enabled", False))


@dataclass
class Engine:
    root: Path
    state_dir: Path
    dist_dir: Path
    tz: ZoneInfo
    ai: AIConfig
    jobs: dict[str, JobSpec]
    digest: dict[str, Any]
    projects: dict[str, Project]


def default_home() -> Path:
    """The private folder with engine.toml, projects/, state/ and dist/; never inside the public repo."""
    return Path(os.environ.get("GROWTH_HOME", "~/growth-home")).expanduser()


def load_engine(root: Path, *, state_dir: Path | None = None, dist_dir: Path | None = None) -> Engine:
    """Load `root`/engine.toml and every enabled project under it. Raises ConfigError.

    `root` is the engine home: a private folder outside the repository, because project configs,
    keyword data and anything owner-specific must never be published with the engine's code.
    """
    errors: list[str] = []
    root = root.expanduser().resolve()
    if not (root / "engine.toml").is_file():
        raise ConfigError([f"{root}/engine.toml not found (set GROWTH_HOME or pass --home; see docs/setup.md)"])
    raw = read_toml(root / "engine.toml")
    tz = _zone(raw.get("timezone", "UTC"), "engine.toml timezone", errors)
    env_state = os.environ.get("GROWTH_STATE_DIR")
    state = state_dir or root / Path(env_state or raw.get("state_dir", "state")).expanduser()
    dist = dist_dir or root / raw.get("dist_dir", "dist")
    ai = _ai(raw.get("ai", {}), errors)
    from .jobs import JOB_KINDS

    jobs = _jobs(raw.get("jobs", {}), "engine.toml", errors)
    for job in jobs.values():
        kind = JOB_KINDS.get(job.kind)
        if kind and kind.scope != ENGINE_SCOPE:
            errors.append(f"engine.toml jobs.{job.id}: kind {job.kind!r} runs per project; configure it in a project")

    projects: dict[str, Project] = {}
    projects_dir = root / raw.get("projects_dir", "projects")
    for folder in sorted(p for p in projects_dir.iterdir() if (p / "project.toml").is_file()) if projects_dir.is_dir() else []:
        try:
            project = load_project(folder)
        except ConfigError as exc:
            errors.extend(exc.errors)
            continue
        if project is not None:
            projects[project.id] = project

    if errors:
        raise ConfigError(errors)
    return Engine(
        root=root,
        state_dir=state,
        dist_dir=dist,
        tz=tz,
        ai=ai,
        jobs=jobs,
        digest=raw.get("digest", {}),
        projects=projects,
    )


def load_project(folder: Path) -> Project | None:
    """Load one project folder; None when it is switched off (enabled = false)."""
    errors: list[str] = []
    where = f"projects/{folder.name}/project.toml"
    raw = read_toml(folder / "project.toml")
    if not raw.get("enabled", True):
        return None
    pid = raw.get("id", "")
    if not _ID.match(str(pid)):
        errors.append(f"{where}: id must be lower-case letters, digits and dashes")
    elif pid != folder.name:
        errors.append(f"{where}: id {pid!r} must match its folder name {folder.name!r}")

    languages = list(raw.get("languages", []))
    default_language = raw.get("default_language", languages[0] if languages else "")
    if not languages:
        errors.append(f"{where}: languages must list at least one language")
    elif default_language not in languages:
        errors.append(f"{where}: default_language {default_language!r} is not in languages")

    brand = dict(raw.get("brand", {}))
    if not brand.get("name"):
        errors.append(f"{where}: [brand] name is required")

    site = dict(raw.get("site", {}))
    _check_site(site, where, errors)

    channels = _channels(raw.get("channels", {}), where, errors)
    rules = ProjectRules(
        footage_publishers=frozenset(raw.get("rules", {}).get("footage_publishers", [])),
        accounts=_accounts(raw.get("channels", {}), where, errors),
        channels=frozenset(cid for cid, conf in channels.items() if conf.get("enabled")),
    )
    data = dict(raw.get("data", {}))
    for sid, source in data.items():
        if source.get("kind") not in {"http-json", "file-json"}:
            errors.append(f"{where}: data.{sid}: kind must be 'http-json' or 'file-json'")
        if source.get("kind") == "http-json":
            try:
                check_url(str(source.get("url", "")))
            except PolicyViolation as exc:
                errors.append(f"{where}: data.{sid}: {exc}")
            if not str(source.get("url", "")).startswith("https://"):
                errors.append(f"{where}: data.{sid}: url must be https")

    jobs = _jobs(raw.get("jobs", {}), where, errors)
    project = Project(
        id=str(pid),
        root=folder,
        raw=raw,
        name=str(raw.get("name", pid)),
        tz=_zone(raw.get("timezone", "UTC"), f"{where} timezone", errors),
        languages=languages,
        default_language=default_language,
        brand=brand,
        site=site,
        rules=rules,
        channels=channels,
        data=data,
        collections=dict(raw.get("collections", {})),
        jobs=jobs,
        analytics=dict(raw.get("analytics", {"provider": "none"})),
    )
    _check_project_jobs(project, where, errors)

    if not errors:
        from .site.spec import validate_site

        errors.extend(validate_site(project))
    if errors:
        raise ConfigError(errors)
    return project


def _check_site(site: dict[str, Any], where: str, errors: list[str]) -> None:
    base = str(site.get("base_url", ""))
    if not re.match(r"^https://[a-z0-9.-]+(:\d+)?$", base):
        errors.append(f"{where}: [site] base_url must be https://host with no path or trailing slash")
    if site.get("indexable"):
        if not site.get("domain_decided"):
            errors.append(f"{where}: [site] indexable needs domain_decided = true (search engines would index a placeholder)")
        if not site.get("legal_notice") or not site.get("privacy"):
            errors.append(f"{where}: [site] indexable needs legal_notice and privacy pages (German law: Impressum)")


def _accounts(raw_channels: dict[str, Any], where: str, errors: list[str]) -> dict[str, str]:
    accounts: dict[str, str] = {}
    for cid, conf in raw_channels.items():
        listed = conf.get("accounts", [])
        if isinstance(listed, str):
            listed = [listed]
        if len(listed) > 1:
            errors.append(f"{where}: channels.{cid}: one account per platform, found {len(listed)}")
        elif listed:
            accounts[cid] = str(listed[0])
    return accounts


def _channels(raw_channels: dict[str, Any], where: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid, conf in raw_channels.items():
        known = ch.CHANNELS.get(cid)
        if known is None:
            errors.append(f"{where}: channels.{cid}: unknown channel (see docs/channels.md)")
            continue
        conf = dict(conf)
        if conf.get("enabled"):
            if known.level == ch.OFF:
                errors.append(f"{where}: channels.{cid}: cannot be enabled: {known.why}")
            if known.level == ch.NEEDS_APPROVAL:
                approval = conf.get("approval", {})
                if not approval.get("granted") or not approval.get("evidence"):
                    errors.append(
                        f"{where}: channels.{cid}: needs a platform approval first; record it as "
                        'approval = { granted = "YYYY-MM-DD", evidence = "..." }'
                    )
        out[cid] = conf
    return out


def _jobs(raw_jobs: dict[str, Any], where: str, errors: list[str]) -> dict[str, JobSpec]:
    from .jobs import JOB_KINDS

    jobs: dict[str, JobSpec] = {}
    for jid, conf in raw_jobs.items():
        if not _ID.match(jid):
            errors.append(f"{where}: jobs.{jid}: id must be lower-case letters, digits and dashes")
            continue
        kind = conf.get("kind", "")
        if kind not in JOB_KINDS:
            errors.append(f"{where}: jobs.{jid}: unknown kind {kind!r} (known: {', '.join(sorted(JOB_KINDS))})")
            continue
        try:
            schedule = parse_schedule(conf.get("schedule", ""))
        except ValueError as exc:
            errors.append(f"{where}: jobs.{jid}: {exc}")
            continue
        catchup = conf.get("catchup", "latest")
        if catchup not in CATCHUP:
            errors.append(f"{where}: jobs.{jid}: catchup must be one of {sorted(CATCHUP)}")
        default_late = schedule.period if schedule.kind == "every" else (
            timedelta(hours=20) if schedule.kind == "daily" else timedelta(days=3)
        )
        try:
            max_late = parse_duration(conf["max_late"]) if "max_late" in conf else default_late
        except ValueError as exc:
            errors.append(f"{where}: jobs.{jid}: max_late: {exc}")
            continue
        params = {k: v for k, v in conf.items() if k not in {"kind", "schedule", "enabled", "catchup", "max_late", "max_attempts"}}
        jobs[jid] = JobSpec(
            id=jid,
            kind=kind,
            schedule=schedule,
            enabled=bool(conf.get("enabled", True)),
            catchup=catchup,
            max_late=max_late,
            max_attempts=int(conf.get("max_attempts", 3)),
            params=params,
        )
    return jobs


def _check_project_jobs(project: Project, where: str, errors: list[str]) -> None:
    from .jobs import JOB_KINDS

    for job in project.jobs.values():
        kind = JOB_KINDS[job.kind]
        if kind.scope == ENGINE_SCOPE:
            errors.append(f"{where}: jobs.{job.id}: kind {job.kind!r} runs once for the engine; configure it in engine.toml")
        if kind.channel and not project.channel_enabled(kind.channel):
            level = ch.CHANNELS[kind.channel].level
            errors.append(
                f"{where}: jobs.{job.id}: needs channel {kind.channel!r} enabled (level {level}); "
                "a job on a channel that may not run does not run at all"
            )
        if not job.enabled:
            continue
        if job.kind.startswith("directories-"):
            conf = project.raw.get("directories", {})
            if job.kind == "directories-draft" and not (project.root / str(conf.get("fact_sheet", ""))).is_file():
                errors.append(f"{where}: jobs.{job.id}: [directories] fact_sheet must name an existing file")
            if job.kind == "directories-submit" and conf.get("submit") and not (project.root / str(conf["submit"])).is_file():
                errors.append(f"{where}: jobs.{job.id}: [directories] submit file {conf['submit']!r} not found")
        if job.kind == "indexnow":
            if not project.site.get("indexable"):
                errors.append(f"{where}: jobs.{job.id}: IndexNow needs [site] indexable = true")
            if not INDEXNOW_KEY.fullmatch(str(job.params.get("key", ""))):
                errors.append(f"{where}: jobs.{job.id}: key must be 8-128 letters, digits or dashes")


def _ai(raw: dict[str, Any], errors: list[str]) -> AIConfig:
    command = raw.get("command", ["claude"])
    if isinstance(command, str):
        command = [command]
    window = None
    if raw.get("window"):
        match = _WINDOW.match(str(raw["window"]).replace(" ", ""))
        if not match:
            errors.append("engine.toml [ai] window must look like 01:00-07:00")
        else:
            h1, m1, h2, m2 = (int(g) for g in match.groups())
            window = (time(h1 % 24, m1), time(h2 % 24, m2))
    try:
        timeout = parse_duration(raw.get("timeout", "20m"))
        cooldown = parse_duration(raw.get("cooldown_after_limit", "5h"))
    except ValueError as exc:
        errors.append(f"engine.toml [ai]: {exc}")
        timeout, cooldown = timedelta(minutes=20), timedelta(hours=5)
    command = [str(c) for c in command]
    extra_args = [str(a) for a in raw.get("extra_args", [])]
    for arg in forbidden_args([*command, *extra_args]):
        errors.append(f"engine.toml [ai]: command and extra_args may not contain {arg}: it needs a paid API key")
    per_project = raw.get("per_project_runs_per_week")
    return AIConfig(
        command=command,
        extra_args=extra_args,
        max_runs_per_day=int(raw.get("max_runs_per_day", 4)),
        max_runs_per_week=int(raw.get("max_runs_per_week", 12)),
        per_project_runs_per_week=int(per_project) if per_project is not None else None,
        window=window,
        timeout=timeout,
        cooldown=cooldown,
    )


def forbidden_args(args: list[str]) -> list[str]:
    return [arg for arg in args if arg.split("=")[0] in FORBIDDEN_ARGS]


def _zone(name: str, where: str, errors: list[str]) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"{where}: unknown time zone {name!r}")
        return ZoneInfo("UTC")
