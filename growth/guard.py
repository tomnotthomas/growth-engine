"""Guards around everything the engine does on its own: the kill switch, the audit log, per-channel
rate limits and the outbound allow-list. They sit next to the never-automate checks (policy.py), which
still run before every outward action.

- Kill switch: `state/engine/kill.json`. While it exists no job, deploy, update or outward action
  runs; ticks only record that they were halted. The control app and `growth kill` set and clear it.
- Audit log: `state/engine/audit.jsonl`, append-only and hash-chained (each entry carries the hash of
  the one before), so an edited or deleted line shows up in `growth audit --verify`. Every outward
  action, block, kill-switch change, control action, engine update and deploy is recorded. Secret
  values never are.
- Rate limits: `[guard.rate_limits]` in engine.toml (and per project in the control layer) caps
  outward actions per channel, e.g. `directory-submit = "20/24h"`. A channel can also be paused.
- Outbound allow-list: every HTTP request the engine makes goes to a host derived from the config
  (data sources, the project's own domain, analytics, IndexNow, verified directory sites, GitHub for
  updates) or listed in `[guard] outbound_allow`. Anything else is refused before it is sent.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .policy import PolicyViolation, hostname
from .util import iso, utcnow, write_json

GENESIS = "0" * 64
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
BUILTIN_HOSTS = frozenset({"api.indexnow.org", "api.github.com"})
_RATE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*([mhd])\s*$")


class Halted(Exception):
    """The kill switch is on."""


# ---- kill switch ----------------------------------------------------------------------------------


def _engine_dir(state_dir: Path) -> Path:
    path = state_dir / "engine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def kill_state(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "engine" / "kill.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"on": True, "reason": "kill.json is unreadable, so the engine stays halted", "at": "", "by": "?"}


def set_kill(state_dir: Path, on: bool, *, reason: str, actor: str) -> dict[str, Any] | None:
    path = _engine_dir(state_dir) / "kill.json"
    if on:
        state = {"on": True, "reason": reason or "no reason given", "at": iso(utcnow()), "by": actor}
        write_json(path, state)
    else:
        state = None
        path.unlink(missing_ok=True)
    audit(state_dir, actor, "kill-switch.on" if on else "kill-switch.off", reason=reason)
    return state


def check_running(state_dir: Path) -> None:
    state = kill_state(state_dir)
    if state:
        raise Halted(f"kill switch is on since {state.get('at') or '?'} ({state.get('by', '?')}: {state.get('reason', '')})")


# ---- audit log ------------------------------------------------------------------------------------


def _entry_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    with open(path.with_suffix(".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _last_entry(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65536))
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        if line.strip():
            return json.loads(line)
    return None


def audit(state_dir: Path, actor: str, event: str, *, scope: str = "engine", **detail: Any) -> dict[str, Any]:
    """Append one entry to the hash-chained audit log and return it."""
    path = _engine_dir(state_dir) / "audit.jsonl"
    with _locked(path):
        last = _last_entry(path)
        entry = {
            "seq": (last["seq"] + 1) if last else 1,
            "at": iso(utcnow()),
            "actor": actor,
            "event": event,
            "scope": scope,
            "detail": {k: v for k, v in detail.items() if v is not None},
            "prev": last["hash"] if last else GENESIS,
        }
        entry["hash"] = _entry_hash(entry)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return entry


def read_audit(state_dir: Path, limit: int = 200, *, scope: str | None = None) -> list[dict[str, Any]]:
    path = state_dir / "engine" / "audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines):
        if not line.strip():
            continue
        entry = json.loads(line)
        if scope is None or entry.get("scope") == scope:
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def verify_audit(state_dir: Path) -> tuple[bool, int, str]:
    """(intact, entries checked, the first problem). Detects edited, reordered and deleted lines."""
    path = state_dir / "engine" / "audit.jsonl"
    prev, seq, count = GENESIS, 0, 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return True, 0, ""
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return False, count, f"line {number} is not JSON"
        if entry.get("prev") != prev:
            return False, count, f"line {number} does not follow the line before it (a line was removed or reordered)"
        if entry.get("seq") != seq + 1:
            return False, count, f"line {number} has sequence {entry.get('seq')}, expected {seq + 1}"
        if entry.get("hash") != _entry_hash(entry):
            return False, count, f"line {number} was changed after it was written"
        prev, seq, count = entry["hash"], entry["seq"], count + 1
    return True, count, ""


# ---- rate limits ----------------------------------------------------------------------------------


def parse_rate(text: str) -> tuple[int, timedelta]:
    match = _RATE.match(str(text))
    if not match:
        raise ValueError(f"rate limit must look like 20/24h, 5/1h or 100/7d, not {text!r}")
    count, amount, unit = int(match.group(1)), int(match.group(2)), match.group(3)
    if amount <= 0:
        raise ValueError(f"rate limit window must be positive: {text!r}")
    return count, timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[unit]: amount})


def check_rate(store: Any, scope: str, channel: str, limit: str | None, now: datetime) -> None:
    """Raise PolicyViolation("rate-limit") when the channel already used its allowance in the window."""
    if not limit:
        return
    count, window = parse_rate(limit)
    used = sum(1 for e in store.effects_since(now - window, scope) if e["channel"] == channel and e["status"] != "failed")
    if used >= count:
        raise PolicyViolation("rate-limit", f"{channel}: {used} outward actions in the last {window}, the limit is {limit}")


# ---- outbound allow-list --------------------------------------------------------------------------

_allowed: frozenset[str] | None = None


def allow_hosts(hosts: set[str] | frozenset[str] | None) -> None:
    """Set the hosts outbound requests may reach (None: not configured, as in unit tests of single modules)."""
    global _allowed
    _allowed = None if hosts is None else frozenset(h.lower().rstrip(".") for h in hosts if h)


def allowed_hosts() -> frozenset[str] | None:
    return _allowed


def check_outbound(url: str) -> None:
    if _allowed is None:
        return
    host = hostname(url)
    if host in LOOPBACK or host in _allowed or any(h.startswith("*.") and host.endswith(h[1:]) for h in _allowed):
        return
    raise PolicyViolation("outbound-allow-list", f"{host or url!r} is not on the outbound allow-list ([guard] outbound_allow in engine.toml)")


def derive_hosts(engine: Any) -> set[str]:
    """Every host the configuration itself needs, plus [guard] outbound_allow."""
    hosts = set(BUILTIN_HOSTS) | {str(h) for h in engine.guard.get("outbound_allow", [])}
    for project in engine.projects.values():
        hosts.add(hostname(project.site.get("base_url", "")))
        if any(job.kind == "deploy" for job in project.jobs.values()):
            hosts.add("*.workers.dev")  # the deploy job's preview URLs
        for source in project.data.values():
            if source.get("url"):
                hosts.add(hostname(str(source["url"])))
        for key in ("host", "capture_host"):
            if project.analytics.get(key):
                hosts.add(hostname(str(project.analytics[key])))
        directories = project.raw.get("directories", {})
        if directories:
            from .directories import DEFAULT_SOURCE

            source = str(directories.get("source", ""))
            hosts.add(hostname(source if source.startswith("https://") else DEFAULT_SOURCE))
            submit = project.root / str(directories.get("submit", ""))
            if directories.get("submit") and submit.is_file():
                from .util import read_toml

                for site in read_toml(submit).get("site", []):
                    hosts.add(hostname(str(site.get("endpoint", ""))))
    return {h for h in hosts if h}
