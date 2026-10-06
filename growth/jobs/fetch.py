"""fetch-data: copy each project data source into the state cache, keeping only the allowed fields.

Fields not listed in `keep_fields` are dropped on purpose: the live games list carries Steam art URLs,
and the site must never be able to show third-party art.
"""

from __future__ import annotations

from typing import Any

from .. import net
from ..site.build import live_cache_path
from ..util import iso, parse_duration, read_json, write_json


def fetch_source(project: Any, sid: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    if source["kind"] == "http-json":
        timeout = parse_duration(source.get("timeout", "60s")).total_seconds()
        data = net.get_json(source["url"], timeout=timeout, retries=int(source.get("retries", 2)), backoff=20)
    else:
        data = read_json(project.root / source["path"])
    if isinstance(data, dict) and source.get("list_key"):
        data = data.get(source["list_key"])
    if not isinstance(data, list):
        raise ValueError(f"data.{sid}: expected a JSON list, got {type(data).__name__}")
    id_field = source.get("id_field", "id")
    keep = source.get("keep_fields")
    items = []
    for entry in data:
        if not isinstance(entry, dict) or id_field not in entry:
            raise ValueError(f"data.{sid}: every entry needs {id_field!r}")
        items.append({k: v for k, v in entry.items() if keep is None or k in keep})
    if not items and not source.get("allow_empty"):
        raise ValueError(f"data.{sid}: the source returned no entries; keeping the previous cache")
    return items


def run(ctx: Any) -> str:
    project = ctx.project
    registry_path = ctx.state_dir / "site-registry.json"
    registry = read_json(registry_path, {}) or {}
    today = ctx.now.astimezone(project.tz).date().isoformat()
    lines = []
    for sid, source in project.data.items():
        items = fetch_source(project, sid, source)
        write_json(live_cache_path(ctx.state_dir, sid), {"fetched_at": iso(ctx.now), "items": items})
        for cid, conf in project.collections.items():
            if conf.get("source") != sid:
                continue
            first_fetch = cid not in registry.setdefault("first_live", {})
            seen = registry["first_live"].setdefault(cid, {})
            # Games already listed at the first fetch are the baseline, not "new".
            stamp = "baseline" if first_fetch else today
            for item in items:
                seen.setdefault(str(item.get(conf.get("match_live", "id"))), stamp)
        lines.append(f"{sid}: {len(items)} entries")
    write_json(registry_path, registry)
    return "; ".join(lines) or "no data sources"
