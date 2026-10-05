"""Outbound HTTP for jobs. Every request passes the URL policy (no Google Indexing API)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from . import __version__
from .policy import check_url

USER_AGENT = f"growth-engine/{__version__} (+static site generator)"


class HttpError(Exception):
    pass


def request(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    retries: int = 0,
    backoff: float = 5,
) -> tuple[int, bytes]:
    check_url(url)
    data = None
    sent = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json; charset=utf-8"
    sent.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                return exc.code, exc.read()
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise HttpError(f"{method} {url} failed: {last}")


def get_json(url: str, **kwargs: Any) -> Any:
    status, raw = request("GET", url, **kwargs)
    if status != 200:
        raise HttpError(f"GET {url} answered {status}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(f"GET {url} did not return JSON: {exc}") from None
