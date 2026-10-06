"""Small helpers shared by the engine: durations, time, files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DURATION = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(text: str) -> timedelta:
    """"90s", "30m", "6h", "3d" or "1w" as a timedelta."""
    match = _DURATION.match(str(text))
    if not match:
        raise ValueError(f"not a duration: {text!r} (use e.g. 30m, 6h, 3d)")
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return timedelta(**{_UNITS[unit]: amount})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """A UTC timestamp as stored in the state database: 2026-10-05T04:00:00Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def write_atomic(path: Path, text: str) -> None:
    """Write a file so readers never see half of it, even if the PC loses power mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
