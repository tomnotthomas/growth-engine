"""Command line: `python3 -m growth <command>`. The scheduler calls `tick`; people mostly use `check`,
`status`, `build` and `queue`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import timedelta
from pathlib import Path

from . import channels as ch
from .config import ConfigError, Engine, default_home, load_engine, resolve_state_dir
from .guard import Halted
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
    deploy = sub.add_parser("deploy", help="run a project's deploy job now: preview, and production only after the launch go")
    deploy.add_argument("project")
    sub.add_parser("self-update", help="move to the newest green, signed main commit; roll back if the self-check fails")
    sub.add_parser("check", help="validate engine.toml and every project")
    sub.add_parser("status", help="recent runs, AI budget, blocks")
    sub.add_parser("channels", help="every channel and how far it may be automated")
    queue = sub.add_parser("queue", help="write the Reddit draft page for a project and print its path")
    queue.add_argument("project")
    serve = sub.add_parser("serve", help="the control API for the Mac app, on 127.0.0.1 only")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--demo", action="store_true", help="serve made-up data from a throwaway copy of the example home")
    sub.add_parser("app-token", help="print the control app's login token (the Mac app reads it over SSH)")
    kill = sub.add_parser("kill", help="the kill switch: stop every job, deploy, update and outward action")
    kill.add_argument("state", choices=["on", "off", "status"])
    kill.add_argument("--reason", default="")
    audit = sub.add_parser("audit", help="show the newest audit entries, or check the hash chain")
    audit.add_argument("--verify", action="store_true")
    audit.add_argument("--limit", type=int, default=30)
    secret = sub.add_parser("secret", help="the encrypted secrets store")
    secret.add_argument("action", choices=["set", "list", "rm"])
    secret.add_argument("name", nargs="?")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if args.command == "channels":
        return _channels()
    if args.command == "secret":
        return _secret(args.action, args.name)
    if args.command == "app-token":
        from .control.server import app_token

        print(app_token())
        return 0
    if args.command == "serve" and args.demo:
        from .control.demo import make_demo_home
        from .control.ops import Home
        from .control.server import serve as serve_api

        serve_api(Home(make_demo_home()), args.port, demo=True)
        return 0
    if args.command in ("kill", "audit"):
        return _guard_command(args)
    try:
        engine = load_engine(args.home or default_home())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.command == "check":
        return _check(engine)
    if args.command == "self-update":
        from .update import UpdateError, update

        try:
            outcome = update(engine.root, engine.state_dir)
        except UpdateError as exc:
            print(f"update failed: {exc}", file=sys.stderr)
            return 1
        print(f"{outcome.status}: {outcome.detail}")
        return 1 if outcome.status == "rolled-back" else 0
    if args.command == "serve":
        from .control.ops import Home
        from .control.server import serve as serve_api

        serve_api(Home(engine.root), args.port)
        return 0
    if args.command == "status":
        return _status(engine)
    project = None
    if args.command in ("queue", "build", "deploy"):
        project = engine.projects.get(args.project)
        if project is None:
            known = ", ".join(sorted(engine.projects)) or "none"
            print(f"unknown or disabled project {args.project!r} (enabled: {known})", file=sys.stderr)
            return 1
    if args.command == "queue":
        from .queue import write_queue_page

        print(write_queue_page(engine.state_dir, project.id, project.name, esc))
        return 0
    try:
        if args.command == "tick":
            report = tick(engine)
            for line in report.lines():
                print(line)
            return 1 if report.failed else 0
        if args.command == "run":
            status = run_now(engine, args.scope, args.job)
            print(status)
            return 0 if status in ("ok", "already ran") else 1
        if args.command == "deploy" and project is not None:
            jobs = [j.id for j in project.jobs.values() if j.kind == "deploy" and j.enabled]
            if not jobs:
                print(f"{project.id} has no enabled deploy job; add [jobs.deploy] kind = \"deploy\" (docs/deploy.md)", file=sys.stderr)
                return 1
            status = run_now(engine, project.id, jobs[0])
            print(f"{jobs[0]}: {status}")
            if status != "ok":
                _print_last(engine, project.id, jobs[0])
            return 0 if status == "ok" else 1
        if args.command == "build" and project is not None:
            for job in ("fetch-data", "build-site"):
                matches = [j.id for j in project.jobs.values() if j.kind == job and j.enabled]
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
    except Halted as exc:
        print(f"halted: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 1
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


def _secret(action: str, name: str | None) -> int:
    import getpass

    from . import secrets

    try:
        if action == "list":
            for key in secrets.names():
                print(key)
            return 0
        if not name:
            print("name the secret, e.g. growth secret set CLOUDFLARE_API_TOKEN", file=sys.stderr)
            return 2
        if action == "rm":
            print("removed" if secrets.delete(name) else "no such secret")
            return 0
        value = getpass.getpass(f"{name}: ") if sys.stdin.isatty() else sys.stdin.read().strip()
        if not value:
            print("empty value; nothing stored", file=sys.stderr)
            return 1
        secrets.put(name, value)
        print(f"stored {name} (encrypted, backend {secrets.backend()})")
        return 0
    except secrets.SecretsError as exc:
        print(exc, file=sys.stderr)
        return 1


def _guard_command(args: argparse.Namespace) -> int:
    from . import guard

    state = resolve_state_dir(args.home or default_home())
    if args.command == "kill":
        if args.state == "status":
            current = guard.kill_state(state)
            print(f"ON since {current.get('at')} by {current.get('by')}: {current.get('reason')}" if current else "off")
            return 0
        guard.set_kill(state, args.state == "on", reason=args.reason, actor="cli")
        print(f"kill switch {args.state}")
        return 0
    if args.verify:
        intact, count, problem = guard.verify_audit(state)
        print(f"audit log intact: {count} entries" if intact else f"audit log BROKEN after {count} entries: {problem}")
        return 0 if intact else 1
    for entry in reversed(guard.read_audit(state, args.limit)):
        print(f"{entry['at']}  {entry['actor']:8} {entry['scope']:12} {entry['event']:22} {json.dumps(entry['detail'], ensure_ascii=False)[:160]}")
    return 0


def _channels() -> int:
    for channel in ch.CHANNELS.values():
        print(f"{channel.id:20} {channel.level:15} {channel.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

