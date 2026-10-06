"""deploy: publish the project's site and waitlist Worker to Cloudflare (Workers static assets + D1).

Runs on its schedule (e.g. every 15 minutes) and does nothing unless the built output changed, so it
follows both content changes and engine updates. Nothing reaches Cloudflare before the launch go.

1. rebuilds the site with the current engine code and checks every internal link;
2. runs the build on this machine with `wrangler dev` and a throwaway local D1 database, and
   smoke-checks it on 127.0.0.1: the pages answer 200 and the waitlist answers. No public URL and no
   production database are involved;
3. only when the project's `[deploy] launched = true` (the owner's launch go, false by default):
   makes sure the D1 database exists (the id is kept in state, never in the public repo) and applies
   schema.sql (CREATE ... IF NOT EXISTS only), uploads a new Worker version, promotes it to 100% of
   production, smoke-checks the live domain, and on failure rolls production back to the last
   version that passed, automatically. A build that was rolled back is not promoted again; the next
   changed build is.

What to do is decided from `deploy.json` (the last local check and the production record), so a build
checked before the launch goes live on the first run after the launch go.

Credentials come from the encrypted secrets store and are only needed after the launch go:
CLOUDFLARE_API_TOKEN (scopes: Workers Scripts edit, D1 edit) and CLOUDFLARE_ACCOUNT_ID. Every remote
step goes through the policy checks, the rate limit for the website channel, the kill switch and the
audit log, like any other outward action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .. import guard, net, secrets
from ..policy import Action
from ..site.links import check_links
from ..util import iso, read_json, write_atomic, write_json
from .build import run as build

WRANGLER = ["npx", "--yes", "wrangler@4"]
PLACEHOLDER = "SET-BY-deploy/cloudflare.sh"
REQUIRED_SECRETS = ("EMAIL_API_KEY", "STATS_TOKEN", "HASH_SALT")
MAX_SMOKE_PAGES = 12
LOCAL_READY_SECONDS = 180
_VERSION = re.compile(r"Worker Version ID:\s*([0-9a-f-]{36})", re.I)


class DeployError(Exception):
    pass


def fingerprint(out: Path) -> str:
    """A hash of everything that gets uploaded; the same build gives the same fingerprint."""
    digest = hashlib.sha256()
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "DEPLOY.md"):
        digest.update(str(path.relative_to(out)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def local_env() -> dict[str, str]:
    """The environment for local wrangler runs: no Cloudflare credentials at all."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLOUDFLARE_", "CF_"))}
    env["WRANGLER_SEND_METRICS"] = "false"
    return env


def wrangler_env() -> dict[str, str]:
    token, account = secrets.get("CLOUDFLARE_API_TOKEN"), secrets.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise DeployError(
            "no Cloudflare credentials: store them with `growth secret set CLOUDFLARE_API_TOKEN` and "
            "`growth secret set CLOUDFLARE_ACCOUNT_ID` (docs/deploy.md lists the token's scopes)"
        )
    return {**local_env(), "CLOUDFLARE_API_TOKEN": token, "CLOUDFLARE_ACCOUNT_ID": account}


class Wrangler:
    def __init__(self, command: list[str], cwd: Path, env: dict[str, str]):
        self.command, self.cwd, self.env = command, cwd, env

    def __call__(self, *args: str, timeout: int = 600) -> str:
        proc = subprocess.run([*self.command, *args], cwd=self.cwd, env=self.env, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-600:]
            raise DeployError(f"wrangler {args[0]} {args[1] if len(args) > 1 else ''} failed: {tail}")
        return proc.stdout


def smoke(base: str, paths: list[str], waitlist: bool) -> list[str]:
    """Problems found on a deployment: pages that do not answer 200, a waitlist that does not answer."""
    problems = []
    for path in paths:
        try:
            status, _ = net.request("GET", base + path, timeout=20, retries=2, backoff=3)
        except (net.HttpError, Exception) as exc:  # noqa: BLE001 - every failure is a smoke failure
            problems.append(f"{path}: {exc}")
            continue
        if status != 200:
            problems.append(f"{path}: answered {status}")
    if waitlist:
        try:
            status, body = net.request("GET", base + "/api/waitlist/count", timeout=20, retries=2, backoff=3)
            if status != 200 or not isinstance(json.loads(body or b"null"), dict):
                problems.append(f"waitlist health: /api/waitlist/count answered {status}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"waitlist health: {exc}")
    return problems


def local_check(command: list[str], out: Path, paths: list[str], waitlist: bool) -> list[str]:
    """Serve the build on 127.0.0.1 with `wrangler dev` and a throwaway local D1, and smoke-check it."""
    env = local_env()
    with tempfile.TemporaryDirectory(prefix="growth-wrangler-") as persist:
        if waitlist:
            Wrangler(command, out, env)("d1", "execute", _database_name(out), "--local", "--persist-to", persist, "--file", "schema.sql")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log_path = Path(persist) / "dev.log"
        with open(log_path, "w") as log_file:
            proc = subprocess.Popen(
                [*command, "dev", "--local", "--ip", "127.0.0.1", "--port", str(port), "--persist-to", persist],
                cwd=out, env=env, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
            )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + LOCAL_READY_SECONDS
            while True:
                if proc.poll() is not None:
                    raise DeployError(f"wrangler dev stopped before it served the site: {log_path.read_text(errors='replace').strip()[-400:]}")
                try:
                    net.request("GET", base + "/", timeout=5)
                    break
                except net.HttpError:
                    if time.monotonic() > deadline:
                        raise DeployError(f"wrangler dev did not answer on {base} within {LOCAL_READY_SECONDS}s") from None
                    time.sleep(1)
            return smoke(base, paths, waitlist)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=15)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def _smoke_paths(state_dir: Path) -> list[str]:
    registry = read_json(state_dir / "site-registry.json", {}) or {}
    pages = sorted(registry.get("pages", {}))
    home = [p for p in pages if p.count("/") <= 2]  # the home pages first, then the rest
    chosen = list(dict.fromkeys(home + pages))[:MAX_SMOKE_PAGES]
    return (chosen or ["/"]) + ["/robots.txt"]


def run(ctx: Any) -> str:
    project, engine = ctx.project, ctx.engine
    guard.check_running(engine.state_dir)
    launched = project.launched
    env = wrangler_env() if launched else None
    build_note = build(ctx)
    out = engine.dist_dir / project.site.get("out", project.id)
    broken = check_links(out / "public")
    if broken:
        raise DeployError(f"{len(broken)} broken links, nothing uploaded: {'; '.join(broken[:5])}")

    state_path = ctx.state_dir / "deploy.json"
    state = read_json(state_path, {}) or {}
    fp = fingerprint(out)
    check, production = state.get("check") or {}, state.get("production") or {}
    waitlist = "waitlist" in project.raw
    paths = _smoke_paths(ctx.state_dir)
    base_url = project.site["base_url"]
    if launched and production.get("fingerprint") == fp:
        if production.get("status") == "promoted":
            return "up to date"
        problems = smoke(base_url, paths, waitlist)
        if problems:
            raise DeployError(f"production still fails its live checks: {'; '.join(problems[:5])}")
        if production.get("status") == "failed":
            state["production"] = {**production, "at": iso(ctx.now), "status": "promoted", "detail": "live checks pass again"}
            state["last_good"] = production["version_id"]
            _save(state_path, state, state["production"])
            return f"live: {production['version_id']} on {base_url}"
        return f"this build was {production.get('status')} on production; waiting for a changed build"

    # 1. The local check: this machine only, nothing leaves it.
    command = list(engine.deploy.get("wrangler", WRANGLER))
    if not (check.get("fingerprint") == fp and check.get("status") == "checked"):
        problems = local_check(command, out, paths, waitlist)
        record = {"at": iso(ctx.now), "fingerprint": fp, "stage": "check", "url": "", "status": "failed" if problems else "checked"}
        state["check"] = {**record, "detail": "; ".join(problems[:5]) if problems else build_note[:200]}
        _save(state_path, state, state["check"])
        if problems:
            raise DeployError(f"the local check failed, nothing uploaded: {'; '.join(problems[:5])}")
    if env is None:
        return "local check passed; nothing uploaded, production waits for the launch go"

    # 2. Production, only after the launch go, with automatic rollback.
    wrangler = Wrangler(command, out, env)
    _check_production_ready(project, wrangler)
    if waitlist:
        _ensure_database(project, out, state, wrangler)
        write_json(state_path, state)
        _act(ctx, "schema", fp, lambda: wrangler("d1", "execute", _database_name(out), "--remote", "--yes", "--file", "schema.sql") and "schema applied")
    uploaded: dict[str, str] = {}

    def upload() -> str:
        text = wrangler("versions", "upload", "--message", f"growth-engine {fp[:12]}")
        match = _VERSION.search(text)
        if not match:
            raise DeployError(f"wrangler did not report a version id: {text.strip()[-300:]}")
        uploaded["version"] = match.group(1)
        return f"version {uploaded['version']}"

    _act(ctx, "upload", fp, upload)
    version, last_good = uploaded["version"], state.get("last_good")
    record = {"at": iso(ctx.now), "fingerprint": fp, "version_id": version, "stage": "production", "url": base_url}
    _act(ctx, "production", fp, lambda: wrangler("versions", "deploy", f"{version}@100%", "--yes", "--message", f"growth-engine {fp[:12]}") and f"promoted {version}")
    problems = smoke(base_url, paths, waitlist)
    if not problems:
        state["production"] = {**record, "status": "promoted"}
        state["last_good"] = version
        _save(state_path, state, state["production"])
        return f"live: {version} on {base_url}"
    detail = "; ".join(problems[:5])
    if last_good and last_good != version:
        wrangler("versions", "deploy", f"{last_good}@100%", "--yes", "--message", "growth-engine rollback")
        after = smoke(base_url, paths, waitlist)
        state["production"] = {
            **record, "status": "rolled-back",
            "detail": f"{detail}; rolled back to {last_good}" + (f", which also fails: {'; '.join(after[:3])}" if after else ""),
        }
        _save(state_path, state, state["production"])
        guard.audit(engine.state_dir, "engine", "deploy.rollback", scope=project.id, bad=version, restored=last_good, problems=detail[:300])
        raise DeployError(f"production failed its checks and was rolled back to {last_good}: {detail}")
    state["production"] = {**record, "status": "failed", "detail": f"{detail}; no earlier good version to roll back to"}
    _save(state_path, state, state["production"])
    raise DeployError(f"production failed its checks and there is no earlier good version: {detail}")


def _act(ctx: Any, step: str, fp: str, do: Callable[[], str]) -> None:
    """One remote step, through the policy checks and the audit log; recorded per attempt, not per build."""
    action = Action(channel="website", kind="publish", url=ctx.project.site["base_url"], meta={"step": step})
    result = ctx.act(action, f"deploy:{step}:{fp[:16]}:{iso(ctx.now)}", do)
    if result != "done":
        raise DeployError(f"deploy step {step} for this attempt is {result}; nothing further done")


def _save(path: Path, state: dict[str, Any], entry: dict[str, Any]) -> None:
    state["history"] = (state.get("history", []) + [entry])[-30:]
    write_json(path, state)


def _schema_hash(out: Path) -> str:
    return hashlib.sha256((out / "schema.sql").read_bytes()).hexdigest()[:16]


def _database_name(out: Path) -> str:
    match = re.search(r'^database_name = "(.*)"$', (out / "wrangler.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise DeployError("wrangler.toml has no D1 database_name")
    return match.group(1)


def _ensure_database(project: Any, out: Path, state: dict[str, Any], wrangler: Wrangler) -> None:
    """Fill in the D1 database id: from the project, from earlier deploys, or by creating the database."""
    toml = out / "wrangler.toml"
    text = toml.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        return
    name = _database_name(out)
    db_id = state.get("d1_database_id", "")
    if not db_id:
        listed = json.loads(wrangler("d1", "list", "--json") or "[]")
        db_id = next((d.get("uuid", "") for d in listed if d.get("name") == name), "")
    if not db_id:
        wrangler("d1", "create", name)
        listed = json.loads(wrangler("d1", "list", "--json") or "[]")
        db_id = next((d.get("uuid", "") for d in listed if d.get("name") == name), "")
    if not re.fullmatch(r"[0-9a-f-]{36}", db_id):
        raise DeployError(f"could not find or create the D1 database {name!r}")
    state["d1_database_id"] = db_id
    write_atomic(toml, text.replace(PLACEHOLDER, db_id))


def _check_production_ready(project: Any, wrangler: Wrangler) -> None:
    conf = project.raw.get("waitlist", {})
    if conf and conf.get("email_provider", "log") == "log":
        raise DeployError("[waitlist] email_provider is 'log' (local development only); set it to 'brevo' or 'resend' before launch")
    if conf:
        listed = wrangler("secret", "list", "--format", "json")
        missing = [name for name in REQUIRED_SECRETS if f'"{name}"' not in listed]
        if missing:
            raise DeployError(f"Worker secrets missing before launch: {', '.join(missing)} (docs/deploy.md)")
