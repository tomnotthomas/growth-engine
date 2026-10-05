"""Command line: `python3 -m growth <command>`. The scheduler calls `tick`; people mostly use `check`,
`status`, `build` and `queue`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from . import channels as ch
from .config import ConfigError, Engine, default_home, load_engine
from .policy import NEVER_AUTOMATE
from .runner import EngineBusy, run_now, tick
from .site.html import esc
from .store import Store
from .util import utcnow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growth", description="Autonomous growth engine")
    parser.add_argument(
        "--home", type=Path, default=None, help="private engine home with engine.toml and projects/ (default: $GROWTH_HOME or ~/growth-home)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="run every due job once (the systemd timer calls this)")
    run = sub.add_parser("run", help="run one job now, outside its schedule")
    run.add_argument("scope", help="project id, or 'engine' for engine jobs")
    run.add_argument("job")
    build = sub.add_parser("build", help="fetch data and build one project's site now")
    build.add_argument("project")
    sub.add_parser("check", help="validate engine.toml and every project")
    sub.add_parser("status", help="recent runs, AI budget, blocks")
    sub.add_parser("channels", help="every channel and how far it may be automated")
    queue = sub.add_parser("queue", help="write the Reddit draft page for a project and print its path")
    queue.add_argument("project")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if args.command == "channels":
        return _channels()
    try:
        engine = load_engine(args.home or default_home())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.command == "check":
        return _check(engine)
    if args.command == "status":
        return _status(engine)
    if args.command == "queue":
        from .queue import write_queue_page

        project = engine.projects[args.project]
        print(write_queue_page(engine.state_dir, project.id, project.name, esc))
        return 0
    try:
        if args.command == "tick":
            for line in tick(engine).lines():
                print(line)
            return 0
        if args.command == "run":
            status = run_now(engine, args.scope, args.job)
            print(status)
            return 0 if status in ("ok", "already ran") else 1
        if args.command == "build":
            project = engine.projects[args.project]
            for job in ("fetch-data", "build-site"):
                matches = [j.id for j in project.jobs.values() if j.kind == job]
                for jid in matches:
                    status = run_now(engine, project.id, jid)
                    print(f"{jid}: {status}")
                    if status != "ok":
                        _print_last(engine, project.id, jid)
                        return 1
            return 0
    except EngineBusy as exc:
        print(f"busy: {exc}", file=sys.stderr)
        return 0 if args.command == "tick" else 1
    return 2


def _check(engine: Engine) -> int:
    print(f"engine ok: state {engine.state_dir}, output {engine.dist_dir}")
    for job in engine.jobs.values():
        print(f"  engine/{job.id}: {job.kind} {job.schedule.text}{'' if job.enabled else ' (off)'}")
    for project in engine.projects.values():
        print(f"project {project.id}: {project.name}, languages {', '.join(project.languages)}")
        for job in project.jobs.values():
            print(f"  {project.id}/{job.id}: {job.kind} {job.schedule.text}{'' if job.enabled else ' (off)'}")
        enabled = [cid for cid in project.channels if project.channel_enabled(cid)]
        print(f"  channels on: {', '.join(enabled) or 'none'}")
    print(f"never-automate rules enforced: {len(NEVER_AUTOMATE)}")
    return 0


def _status(engine: Engine) -> int:
    store = Store(engine.state_dir / "engine.db")
    try:
        since = utcnow() - timedelta(days=7)
        for run in store.runs_since(since):
            print(f"{run.slot}  {run.scope}/{run.job}  {run.status}  {run.summary[:120]}")
        print(f"AI runs: {store.ai_runs_since(utcnow() - timedelta(days=1))} today (max {engine.ai.max_runs_per_day}), "
              f"{store.ai_runs_since(since)} this week (max {engine.ai.max_runs_per_week})")
        for block in store.blocks_since(since):
            print(f"BLOCKED {block['at']} {block['scope']}/{block['job']} [{block['rule']}] {block['message']}")
    finally:
        store.close()
    return 0


def _print_last(engine: Engine, scope: str, job: str) -> None:
    store = Store(engine.state_dir / "engine.db")
    try:
        runs = [r for r in store.runs_since(utcnow() - timedelta(hours=1), scope) if r.job == job]
        if runs:
            print(runs[-1].summary, file=sys.stderr)
    finally:
        store.close()


def _channels() -> int:
    for channel in ch.CHANNELS.values():
        print(f"{channel.id:20} {channel.level:15} {channel.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

