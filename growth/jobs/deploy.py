"""deploy: publish the project's site and waitlist Worker to Cloudflare (Workers static assets + D1).

Runs on its schedule (e.g. every 15 minutes) and does nothing unless the built output changed, so it
follows both content changes and engine updates. Each deploy:

1. rebuilds the site with the current engine code and checks every internal link;
2. makes sure the D1 database exists (the id is kept in state, never in the public repo) and applies
   schema.sql (CREATE ... IF NOT EXISTS only);
3. uploads a new Worker version with `wrangler versions upload`. That version is only reachable on its
   private preview URL; production keeps serving the previous version;
4. smoke-checks the preview: the pages answer 200 and the waitlist answers;
5. only when the project's `[deploy] launched = true` (the owner's launch go, false by default):
   promotes the version to 100% of production, smoke-checks the live domain, and on failure rolls
   production back to the last version that passed, automatically.

Credentials come from the encrypted secrets store: CLOUDFLARE_API_TOKEN (scopes: Workers Scripts edit,
D1 edit) and CLOUDFLARE_ACCOUNT_ID. Every upload and promote goes through the policy checks, the rate
limit for the website channel, the kill switch and the audit log, like any other outward action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .. import guard, net, secrets
from ..policy import Action
from ..site.links import check_links
from ..util import iso, read_json, write_atomic, write_json
from .build import run as build

WRANGLER = ["npx", "--yes", "wrangler@4"]
PLACEHOLDER = "SET-BY-deploy/cloudflare.sh"
REQUIRED_SECRETS = ("EMAIL_API_KEY", "STATS_TOKEN", "HASH_SALT")
MAX_SMOKE_PAGES = 12
_VERSION = re.compile(r"Worker Version ID:\s*([0-9a-f-]{36})", re.I)
_PREVIEW = re.compile(r"Version Preview URL:\s*(https://\S+)", re.I)


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


def wrangler_env() -> dict[str, str]:
    token, account = secrets.get("CLOUDFLARE_API_TOKEN"), secrets.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise DeployError(
            "no Cloudflare credentials: store them with `growth secret set CLOUDFLARE_API_TOKEN` and "
            "`growth secret set CLOUDFLARE_ACCOUNT_ID` (docs/deploy.md lists the token's scopes)"
        )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLOUDFLARE_", "CF_"))}
    env.update(CLOUDFLARE_API_TOKEN=token, CLOUDFLARE_ACCOUNT_ID=account, WRANGLER_SEND_METRICS="false")
    return env


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


def _smoke_paths(state_dir: Path) -> list[str]:
    registry = read_json(state_dir / "site-registry.json", {}) or {}
    pages = sorted(registry.get("pages", {}))
    home = [p for p in pages if p.count("/") <= 2]  # the home pages first, then the rest
    chosen = list(dict.fromkeys(home + pages))[:MAX_SMOKE_PAGES]
    return (chosen or ["/"]) + ["/robots.txt"]


def run(ctx: Any) -> str:
    project, engine = ctx.project, ctx.engine
    guard.check_running(engine.state_dir)
    env = wrangler_env()
    build_note = build(ctx)
    out = engine.dist_dir / project.site.get("out", project.id)
    broken = check_links(out / "public")
    if broken:
        raise DeployError(f"{len(broken)} broken links, nothing uploaded: {'; '.join(broken[:5])}")

    state_path = ctx.state_dir / "deploy.json"
    state = read_json(state_path, {}) or {}
    fp = fingerprint(out)
    preview, production = state.get("preview") or {}, state.get("production") or {}
    launched = project.launched
    if preview.get("fingerprint") == fp and preview.get("status") == "previewed":
        if not launched or (production.get("fingerprint") == fp and production.get("status") == "promoted"):
            return "up to date" + ("" if launched else "; production waits for the launch go")

    wrangler = Wrangler(list(engine.deploy.get("wrangler", WRANGLER)), out, env)
    waitlist = "waitlist" in project.raw
    if waitlist:
        _ensure_database(project, out, state, wrangler)
        write_json(state_path, state)
        ctx.act(
            Action(channel="website", kind="publish", url=project.site["base_url"], meta={"step": "schema"}),
            f"deploy:schema:{_schema_hash(out)}",
            lambda: wrangler("d1", "execute", _database_name(out), "--remote", "--yes", "--file", "schema.sql") and "schema applied",
            retry_failed=True,
        )

    # 1. A new version, reachable only on its preview URL.
    uploaded: dict[str, str] = {}

    def upload() -> str:
        text = wrangler("versions", "upload", "--message", f"growth-engine {fp[:12]}")
        version, url = _VERSION.search(text), _PREVIEW.search(text)
        if not version:
            raise DeployError(f"wrangler did not report a version id: {text.strip()[-300:]}")
        uploaded.update(version=version.group(1), url=url.group(1).rstrip("/") if url else "")
        return f"version {uploaded['version']}"

    result = ctx.act(Action(channel="website", kind="publish", url=project.site["base_url"], meta={"step": "preview"}), f"deploy:preview:{fp}", upload, retry_failed=True)
    if result != "done":
        return f"preview for this build {result}; not repeated"
    record = {"at": iso(ctx.now), "fingerprint": fp, "version_id": uploaded["version"], "url": uploaded["url"], "stage": "preview"}
    if not uploaded["url"]:
        state["preview"] = {**record, "status": "failed", "detail": "no preview URL (enable preview_urls for the Worker)"}
        _save(state_path, state, state["preview"])
        raise DeployError("wrangler reported no preview URL, so the version could not be checked; production untouched")
    problems = smoke(uploaded["url"], _smoke_paths(ctx.state_dir), waitlist)
    if problems:
        state["preview"] = {**record, "status": "failed", "detail": "; ".join(problems[:5])}
        _save(state_path, state, state["preview"])
        raise DeployError(f"preview failed its checks, production untouched: {'; '.join(problems[:5])}")
    state["preview"] = {**record, "status": "previewed", "detail": build_note[:200]}
    _save(state_path, state, state["preview"])
    if not launched:
        return f"preview {uploaded['url']} passed its checks; production waits for the launch go"

    # 2. Production, only after the launch go, with automatic rollback.
    _check_production_ready(project, wrangler)
    version, last_good = uploaded["version"], state.get("last_good")

    def promote() -> str:
        wrangler("versions", "deploy", f"{version}@100%", "--yes", "--message", f"growth-engine {fp[:12]}")
        return f"promoted {version}"

    ctx.act(Action(channel="website", kind="publish", url=project.site["base_url"], meta={"step": "production"}), f"deploy:production:{fp}", promote, retry_failed=True)
    problems = smoke(project.site["base_url"], _smoke_paths(ctx.state_dir), waitlist)
    if not problems:
        state["production"] = {**record, "stage": "production", "url": project.site["base_url"], "status": "promoted"}
        state["last_good"] = version
        _save(state_path, state, state["production"])
        return f"live: {version} on {project.site['base_url']}"
    detail = "; ".join(problems[:5])
    if last_good and last_good != version:
        wrangler("versions", "deploy", f"{last_good}@100%", "--yes", "--message", "growth-engine rollback")
        after = smoke(project.site["base_url"], _smoke_paths(ctx.state_dir), waitlist)
        state["production"] = {
            **record, "stage": "production", "url": project.site["base_url"], "status": "rolled-back",
            "detail": f"{detail}; rolled back to {last_good}" + (f", which also fails: {'; '.join(after[:3])}" if after else ""),
        }
        _save(state_path, state, state["production"])
        guard.audit(engine.state_dir, "engine", "deploy.rollback", scope=project.id, bad=version, restored=last_good, problems=detail[:300])
        raise DeployError(f"production failed its checks and was rolled back to {last_good}: {detail}")
    state["production"] = {**record, "stage": "production", "url": project.site["base_url"], "status": "failed", "detail": f"{detail}; no earlier good version to roll back to"}
    _save(state_path, state, state["production"])
    raise DeployError(f"production failed its checks and there is no earlier good version: {detail}")


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
