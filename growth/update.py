"""Engine self-update: move the checkout on the always-on PC to the newest good main commit.

`growth self-update` (the growth-update timer runs it every 15 minutes):

1. fetches `origin/main` and walks the new first-parent commits, newest first;
2. takes the first one whose GitHub checks all passed and whose signature GitHub verified (merges
   made on GitHub are signed by GitHub), skipping commits that failed a self-check here before;
3. under the engine lock (so no job runs on half-updated code) fast-forwards to it, reinstalls
   dependencies if pyproject.toml changed, and runs the self-check with the new code: `growth check`
   against the private home and the unit tests;
4. on a failed self-check resets to the previous commit and remembers the bad one; on success
   reinstalls the systemd units if they changed and restarts the control API and the scheduler.

Every outcome other than "already up to date" is written to `state/engine/update.json` (the weekly
digest and the control app show it) and to the audit log. The kill switch stops updates too.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import guard, net, secrets
from .runner import engine_lock
from .util import iso, read_json, utcnow, write_json

REPO = Path(__file__).resolve().parent.parent
GOOD_CONCLUSIONS = {"success", "neutral", "skipped"}
UNITS = ("growth-engine.service", "growth-engine.timer", "growth-update.service", "growth-update.timer", "growth-app.service")
MAX_CANDIDATES = 20
SELF_CHECK_TIMEOUT = 20 * 60


class UpdateError(Exception):
    pass


@dataclass
class Outcome:
    status: str  # up-to-date, updated, rolled-back, refused, no-green-commit, skipped, failed
    detail: str
    from_sha: str = ""
    to_sha: str = ""

    def record(self) -> dict[str, Any]:
        return {"at": iso(utcnow()), "status": self.status, "from": self.from_sha[:12], "to": self.to_sha[:12], "detail": self.detail[:600]}


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=300)
    if check and proc.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def github_slug(repo: Path, remote: str) -> str:
    url = _git(repo, "remote", "get-url", remote)
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise UpdateError(f"remote {remote} is not a GitHub repository: {url}")
    return f"{match.group(1)}/{match.group(2)}"


def commit_verdict(slug: str, sha: str, get_json: Callable[..., Any] = net.get_json) -> tuple[bool, str]:
    """(good, why): every GitHub check passed and GitHub verified the commit's signature."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = secrets.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api = f"https://api.github.com/repos/{slug}/commits/{sha}"
    commit = get_json(api, headers=headers, timeout=30)
    verification = (commit.get("commit") or {}).get("verification") or {}
    if not verification.get("verified"):
        return False, f"signature not verified by GitHub ({verification.get('reason', 'unsigned')})"
    runs = get_json(api + "/check-runs?per_page=100", headers=headers, timeout=30)
    checks = runs.get("check_runs", [])
    if not checks:
        return False, "no checks ran on this commit"
    pending = [c["name"] for c in checks if c.get("status") != "completed"]
    if pending:
        return False, f"checks still running: {', '.join(pending[:5])}"
    bad = [f"{c['name']}={c.get('conclusion')}" for c in checks if c.get("conclusion") not in GOOD_CONCLUSIONS]
    if bad:
        return False, f"checks not green: {', '.join(bad[:5])}"
    status = get_json(api + "/status", headers=headers, timeout=30)
    if status.get("total_count") and status.get("state") != "success":
        return False, f"commit status is {status.get('state')}"
    return True, f"{len(checks)} checks green, signature verified"


def _dependencies(pyproject: str) -> list[str]:
    try:
        return list(tomllib.loads(pyproject).get("project", {}).get("dependencies", []))
    except tomllib.TOMLDecodeError:
        return []


def install_dependencies(repo: Path, old: str, new: str) -> str:
    """Reinstall when pyproject.toml changed: into .venv if there is one; the engine itself needs none."""
    if subprocess.run(["git", "diff", "--quiet", old, new, "--", "pyproject.toml"], cwd=repo).returncode == 0:
        return "dependencies unchanged"
    venv = repo / ".venv" / "bin" / "python"
    if venv.exists():
        proc = subprocess.run([str(venv), "-m", "pip", "install", "--quiet", "-e", str(repo)], capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise UpdateError(f"pip install failed: {proc.stderr.strip()[-400:]}")
        return "dependencies reinstalled in .venv"
    deps = _dependencies((repo / "pyproject.toml").read_text(encoding="utf-8"))
    if deps:
        raise UpdateError(f"the new version needs packages ({', '.join(deps[:5])}); create ~/growth-engine/.venv first (docs/setup.md)")
    return "pyproject.toml changed, no packages needed"


def self_check(repo: Path, home: Path, python: str = sys.executable) -> tuple[bool, str]:
    """The new code must load the private home and pass its own unit tests."""
    with tempfile.TemporaryDirectory(prefix="growth-selfcheck-") as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GROWTH_")}
        env.update(GROWTH_HOME=str(home), GROWTH_CONFIG_DIR=tmp, GROWTH_KEY_BACKEND="file")
        for label, cmd in (
            ("config check", [python, "-m", "growth", "--home", str(home), "check"]),
            ("unit tests", [python, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"]),
        ):
            try:
                proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=SELF_CHECK_TIMEOUT)
            except subprocess.TimeoutExpired:
                return False, f"{label} timed out"
            if proc.returncode != 0:
                return False, f"{label} failed: {(proc.stdout + proc.stderr).strip()[-500:]}"
    return True, "config check and unit tests passed"


def restart_services(repo: Path, old: str | None, new: str) -> str:
    """Reinstall changed unit files and restart the control API and the scheduler (systemd user units)."""
    systemctl = shutil.which("systemctl")
    target = Path.home() / ".config" / "systemd" / "user"
    if not systemctl or not (target / "growth-engine.timer").exists():
        return "no systemd units installed; the next tick uses the new code"
    changed = []
    for unit in UNITS:
        src = repo / "deploy" / "systemd" / unit
        if src.exists() and (not (target / unit).exists() or (target / unit).read_bytes() != src.read_bytes()):
            shutil.copy2(src, target / unit)
            changed.append(unit)
    run = lambda *a: subprocess.run([systemctl, "--user", *a], capture_output=True, text=True, timeout=60)  # noqa: E731
    run("daemon-reload")
    for unit in changed:
        if unit.endswith(".timer"):
            run("enable", "--now", unit)
    run("restart", "growth-engine.timer")
    if (target / "growth-app.service").exists():
        run("try-restart", "growth-app.service")
    run("start", "--no-block", "growth-engine.service")
    return "services restarted" + (f", units updated: {', '.join(changed)}" if changed else "")


def _record(state_dir: Path, outcome: Outcome, rejected: str | None = None) -> None:
    path = state_dir / "engine" / "update.json"
    data = read_json(path, {}) or {}
    data["history"] = (data.get("history", []) + [outcome.record()])[-50:]
    if rejected:
        data["rejected"] = sorted(set(data.get("rejected", [])) | {rejected})
    write_json(path, data)
    guard.audit(state_dir, "updater", f"update.{outcome.status}", sha_from=outcome.from_sha[:12], sha_to=outcome.to_sha[:12], detail=outcome.detail[:300])


def update(
    home: Path,
    state_dir: Path,
    *,
    repo: Path = REPO,
    remote: str = "origin",
    branch: str = "main",
    verdict: Callable[[str, str], tuple[bool, str]] = commit_verdict,
    check: Callable[[Path, Path], tuple[bool, str]] = self_check,
    restart: Callable[[Path, str | None, str], str] = restart_services,
    slug: str | None = None,
) -> Outcome:
    guard.check_running(state_dir)
    current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        return _refuse(state_dir, f"the checkout is on {current!r}, not {branch!r}; updates only move {branch}")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        return _refuse(state_dir, "the checkout has local changes; updates never overwrite them")
    _git(repo, "fetch", "--quiet", remote, branch)
    head = _git(repo, "rev-parse", "HEAD")
    upstream = _git(repo, "rev-parse", f"{remote}/{branch}")
    if head == upstream:
        return Outcome("up-to-date", "already on the newest main commit", head, head)
    if subprocess.run(["git", "merge-base", "--is-ancestor", head, upstream], cwd=repo).returncode != 0:
        return _refuse(state_dir, f"{branch} here is not an ancestor of {remote}/{branch}; refusing anything but a fast-forward")
    rejected = set((read_json(state_dir / "engine" / "update.json", {}) or {}).get("rejected", []))
    slug = slug or github_slug(repo, remote)
    target, why_not = None, []
    for sha in _git(repo, "rev-list", "--first-parent", f"{head}..{upstream}").split()[:MAX_CANDIDATES]:
        if sha in rejected:
            why_not.append(f"{sha[:7]} failed its self-check here before")
            continue
        good, why = verdict(slug, sha)
        if good:
            target = (sha, why)
            break
        why_not.append(f"{sha[:7]}: {why}")
    if target is None:
        return Outcome("no-green-commit", "; ".join(why_not[:3]) or "no candidate", head, upstream)

    sha, why = target
    with engine_lock(state_dir):
        _git(repo, "merge", "--ff-only", "--quiet", sha)
        try:
            deps = install_dependencies(repo, head, sha)
            ok, result = check(repo, home)
        except (UpdateError, OSError, subprocess.SubprocessError) as exc:
            ok, result, deps = False, str(exc), ""
        if not ok:
            _git(repo, "reset", "--hard", "--quiet", head)
            try:
                install_dependencies(repo, sha, head)
            except UpdateError:
                pass
            outcome = Outcome("rolled-back", f"self-check failed on {sha[:12]}, back on {head[:12]}: {result}", head, sha)
            _record(state_dir, outcome, rejected=sha)
            restart(repo, sha, head)
            return outcome
    services = restart(repo, head, sha)
    outcome = Outcome("updated", f"{why}; {deps}; {result}; {services}", head, sha)
    _record(state_dir, outcome)
    return outcome


def _refuse(state_dir: Path, why: str) -> Outcome:
    return record_once(state_dir, Outcome("refused", why))


def record_once(state_dir: Path, outcome: Outcome) -> Outcome:
    """Record an outcome unless it repeats the last one: a stuck state is reported once, not every 15 minutes."""
    data = read_json(state_dir / "engine" / "update.json", {}) or {}
    last = (data.get("history") or [{}])[-1]
    if last.get("status") != outcome.status or last.get("detail") != outcome.detail[:600]:
        _record(state_dir, outcome)
    return outcome
