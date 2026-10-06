"""Read a project's own numbers for the digest: web traffic (PostHog) and waitlist stats (Worker).

Both are read-only and keyed by secrets from the encrypted store (or the environment), never a config file.
Anything not connected is reported as "not connected" with what it needs, never guessed.
"""

from __future__ import annotations

import json
from typing import Any

from . import net, secrets

SIGNUP_ROWS = 50000
# The PostHog project also holds the app's events; bound the scan instead of reading its whole history.
SIGNUP_DAYS = 365


def traffic(project: Any) -> dict[str, Any]:
    """Pageviews of the generated site (events relayed by the Worker carry site = project id)."""
    conf = project.analytics
    provider = conf.get("provider", "none")
    if provider == "none":
        return {"connected": False, "why": "no analytics provider configured ([analytics] provider)"}
    if provider != "posthog":
        return {"connected": False, "why": f"unknown analytics provider {provider!r}"}
    key = secrets.get(str(conf.get("api_key_env", "")))
    if not key or not conf.get("project_id") or not conf.get("host"):
        return {"connected": False, "why": f"PostHog needs host, project_id and a personal API key in ${conf.get('api_key_env')}"}
    where = f"event = '$pageview' AND timestamp > now() - INTERVAL 7 DAY AND properties.site = '{_quote(project.id)}'"
    try:
        totals = _hogql(conf, key, f"SELECT count() FROM events WHERE {where}")
        pages = _hogql(conf, key, f"SELECT properties.path AS p, count() AS c FROM events WHERE {where} GROUP BY p ORDER BY c DESC LIMIT 10")
        sources = _hogql(
            conf, key, f"SELECT coalesce(properties.utm_source, properties.channel, 'direct') AS s, count() AS c FROM events WHERE {where} GROUP BY s ORDER BY c DESC LIMIT 10"
        )
        countries = _hogql(conf, key, f"SELECT properties.country AS k, count() AS c FROM events WHERE {where} GROUP BY k ORDER BY c DESC LIMIT 10")
        funnel = _hogql(
            conf,
            key,
            "SELECT event, count() FROM events WHERE timestamp > now() - INTERVAL 7 DAY "
            f"AND properties.site = '{_quote(project.id)}' AND event IN ('waitlist_form_view', 'waitlist_submit', 'waitlist_signup', "
            "'waitlist_confirmed', 'referral_sent', 'referral_joined') GROUP BY event",
        )
    except Exception as exc:  # report, never guess
        return {"connected": False, "why": f"PostHog query failed: {exc}"}
    views = int(totals[0][0]) if totals else 0
    return {
        "connected": True,
        "pageviews_7d": views,
        "top_pages": [{"path": p, "views": c} for p, c in pages],
        "sources": [{"source": s, "views": c} for s, c in sources],
        "countries": [{"country": k or "unknown", "views": c} for k, c in countries],
        "funnel_7d": {name: int(n) for name, n in funnel},
    }


def waitlist_stats(project: Any) -> tuple[dict[str, Any] | None, str]:
    conf = project.raw.get("waitlist", {})
    token = secrets.get(str(conf.get("stats_token_env", "")))
    if not conf:
        return None, "no waitlist configured"
    if not token:
        return None, f"waitlist stats need the Worker's STATS_TOKEN in ${conf.get('stats_token_env')}"
    try:
        data = net.get_json(project.site["base_url"] + "/api/waitlist/stats", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except Exception as exc:
        return None, f"waitlist stats unreachable: {exc}"
    return data, ""


def posthog_signup_stats(project: Any) -> tuple[dict[str, Any] | None, str]:
    """Confirmed sign-ups per day, country and channel from PostHog's waitlist_confirmed events."""
    conf = project.analytics
    key = secrets.get(str(conf.get("api_key_env", "")))
    if conf.get("provider") != "posthog" or not key or not conf.get("project_id") or not conf.get("host"):
        return None, "PostHog not connected"
    try:
        rows = _hogql(
            conf,
            key,
            "SELECT toDate(timestamp) AS d, properties.role AS role, properties.country AS country, properties.channel AS channel, "
            "count() AS n, countIf(properties.referred = true) AS referred FROM events WHERE event = 'waitlist_confirmed' "
            f"AND timestamp > now() - INTERVAL {SIGNUP_DAYS} DAY "
            f"AND properties.site = '{_quote(project.id)}' GROUP BY d, role, country, channel ORDER BY d LIMIT {SIGNUP_ROWS}",
        )
    except Exception as exc:  # report, never guess
        return None, f"PostHog query failed: {exc}"
    if len(rows) >= SIGNUP_ROWS:
        return None, f"PostHog returned {len(rows)} rows, the limit, so the numbers may be cut"
    days: dict[tuple[str, str, str], dict[str, Any]] = {}
    sources: dict[tuple[str, str, str], int] = {}
    for d, role, country, channel, n, referred in rows:
        k = (str(d), str(role or "player"), str(country or ""))
        row = days.setdefault(k, {"date": k[0], "role": k[1], "country": k[2], "confirmed": 0, "referred": 0})
        row["confirmed"] += int(n)
        row["referred"] += int(referred)
        s_key = (str(channel or "direct"), k[1], k[2])
        sources[s_key] = sources.get(s_key, 0) + int(n)
    return {
        "totals": {"confirmed": sum(r["confirmed"] for r in days.values())},
        "days": list(days.values()),
        "sources": [{"source": s, "role": r, "country": c, "confirmed": n} for (s, r, c), n in sources.items()],
        "cohorts": [],
    }, ""


def signup_stats(project: Any) -> tuple[dict[str, Any] | None, str]:
    """The Worker's ledger when it answers; else PostHog's waitlist_confirmed events, labelled as an estimate.

    PostHog can miss confirmations (a lost relay) and keeps those of people who left, so it never overrides the ledger.
    """
    worker, worker_why = waitlist_stats(project)
    if worker is not None:
        worker["source_of_numbers"] = "waitlist ledger"
        return worker, ""
    posthog, posthog_why = posthog_signup_stats(project)
    if posthog is not None:
        posthog["source_of_numbers"] = "PostHog (estimate: waitlist ledger not reachable)"
        return posthog, ""
    return None, worker_why if posthog_why == "PostHog not connected" else f"{posthog_why}; {worker_why}"


def _hogql(conf: dict[str, Any], key: str, hogql: str) -> list[list[Any]]:
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


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")
