"""Read a project's own numbers for the digest: web traffic (PostHog) and waitlist stats (Worker).

Both are read-only and keyed by environment variables, so no secret ever sits in a config file.
Anything not connected is reported as "not connected" with what it needs, never guessed.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from . import net


def traffic(project: Any) -> dict[str, Any]:
    conf = project.analytics
    provider = conf.get("provider", "none")
    if provider == "none":
        return {"connected": False, "why": "no analytics provider configured ([analytics] provider)"}
    if provider != "posthog":
        return {"connected": False, "why": f"unknown analytics provider {provider!r}"}
    key = os.environ.get(str(conf.get("api_key_env", "")), "")
    if not key or not conf.get("project_id") or not conf.get("host"):
        return {"connected": False, "why": f"PostHog needs host, project_id and a personal API key in ${conf.get('api_key_env')}"}
    site_host = urlparse(project.site["base_url"]).hostname or ""

    def query(hogql: str) -> list[list[Any]]:
        status, raw = net.request(
            "POST",
            f"{conf['host'].rstrip('/')}/api/projects/{conf['project_id']}/query/",
            body={"query": {"kind": "HogQLQuery", "query": hogql}},
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )
        if status != 200:
            raise net.HttpError(f"PostHog answered {status}")
        return json.loads(raw).get("results", [])

    where = f"event = '$pageview' AND timestamp > now() - INTERVAL 7 DAY AND properties.$host = '{_quote(site_host)}'"
    try:
        focus = str(conf.get("focus_os", ""))  # e.g. "Mac OS X": the share of views from the audience's OS
        totals = query(f"SELECT count(), count(DISTINCT person_id), countIf(properties.$os = '{_quote(focus)}') FROM events WHERE {where}")
        pages = query(f"SELECT properties.$pathname AS p, count() AS c FROM events WHERE {where} GROUP BY p ORDER BY c DESC LIMIT 10")
        sources = query(
            "SELECT coalesce(properties.utm_source, properties.$referring_domain, 'direct') AS s, count() AS c "
            f"FROM events WHERE {where} GROUP BY s ORDER BY c DESC LIMIT 10"
        )
    except Exception as exc:  # report, never guess
        return {"connected": False, "why": f"PostHog query failed: {exc}"}
    views, visitors, focused = (totals[0] if totals else [0, 0, 0])
    return {
        "connected": True,
        "pageviews_7d": views,
        "visitors_7d": visitors,
        "focus_os": focus,
        "focus_os_share_7d": round(focused / views, 3) if views and focus else None,
        "top_pages": [{"path": p, "views": c} for p, c in pages],
        "sources": [{"source": s, "views": c} for s, c in sources],
    }


def waitlist_stats(project: Any) -> tuple[dict[str, Any] | None, str]:
    conf = project.raw.get("waitlist", {})
    token = os.environ.get(str(conf.get("stats_token_env", "")), "")
    if not conf:
        return None, "no waitlist configured"
    if not token:
        return None, f"waitlist stats need the Worker's STATS_TOKEN in ${conf.get('stats_token_env')}"
    try:
        data = net.get_json(project.site["base_url"] + "/api/waitlist/stats", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except Exception as exc:
        return None, f"waitlist stats unreachable: {exc}"
    return data, ""


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")
